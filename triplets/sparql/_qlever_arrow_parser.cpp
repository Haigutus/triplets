// See the header. The term-mapping rules mirror triplets/export/
// nquads_polars.py (the auto-engine exporter) rule for rule; typed literals
// go through qlever's own literalAndDatatypeToTripleComponent, i.e. the very
// code the N-Quads text parser runs, so value encoding (integer/double/bool/
// date folding into Ids, invalid-literal behavior) is identical by
// construction. Plain literals take the raw VALUE verbatim: the exporter's
// escaping and the parser's unescaping cancel out, so skipping both yields
// the same literal content (and stays correct for characters like \r that
// the text path never escaped).

#include "_qlever_arrow_parser.h"

#include <stdexcept>
#include <unordered_map>

#include "absl/strings/str_cat.h"
#include "parser/NormalizedString.h"
#include "parser/Tokenizer.h"
#include "util/Exception.h"
#include "util/ExceptionHandling.h"

namespace {

constexpr std::string_view RDF_TYPE =
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#type";

// Rows per conversion task — same batch size as the RdfParserBase contract.
constexpr int64_t ROWS_PER_TASK = 100'000;

bool isUri(std::string_view value) {
  return value.starts_with("http://") || value.starts_with("https://") ||
         value.starts_with("urn:");
}

// Lowercase-hex 8-4-4-4-12, same as nquads_utils.UUID_RE.
bool isUuid(std::string_view value) {
  if (value.size() != 36) return false;
  for (size_t i = 0; i < 36; ++i) {
    char c = value[i];
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (c != '-') return false;
    } else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
      return false;
    }
  }
  return true;
}

TripleComponent plainLiteral(std::string_view value) {
  // The raw VALUE already IS the literal content — take it verbatim
  // (`literalWithoutQuotes` would run RDF unescaping and corrupt a value
  // containing e.g. a backslash-t sequence). Same convention as the typed
  // path: `literalAndDatatypeToTripleComponent` treats its input as
  // normalized content too.
  return TripleComponent{TripleComponent::Literal::literalWithNormalizedContent(
      asNormalizedStringViewUnsafe(value))};
}

}  // namespace

// ____________________________________________________________________________
namespace {

// Task-local term construction: the per-KEY / per-INSTANCE_ID caches and the
// consecutive-subject memo live here, so worker tasks never share mutable
// state. Rebuilding the caches per 100k-row task is negligible (a CGMES
// dataset has a few hundred distinct KEYs).
class RangeConverter {
 public:
  RangeConverter(const TermMapping& mapping,
                 const EncodedIriManager& encodedIriManager)
      : mapping_{mapping}, encodedIriManager_{encodedIriManager} {}

  // How the object term of a row is built, resolved once per KEY.
  enum class ObjectRule { Type, Enum, Typed, PlainString, Default };
  struct KeyInfo {
    TripleComponent predicate;
    ObjectRule rule;
    std::optional<TripleComponent::Iri> datatype;  // only for Typed
    std::string name;                              // for ingest error context
  };

  TripleComponent iriComponent(std::string_view iri) const {
    auto component = TripleComponent::Iri::fromIrirefWithoutBrackets(iri);
    // Same folding the text parser applies after building each IRI.
    if (auto id =
            encodedIriManager_.encode(component.toStringRepresentation()))
      return TripleComponent{id.value()};
    return TripleComponent{std::move(component)};
  }

  const KeyInfo& keyInfo(std::string_view key) {
    auto found = keyInfos_.find(std::string{key});
    if (found != keyInfos_.end()) return found->second;

    KeyInfo info{TripleComponent{}, ObjectRule::Default, std::nullopt,
                 std::string{key}};
    if (key == "Type") {
      info.predicate = iriComponent(RDF_TYPE);
      info.rule = ObjectRule::Type;
    } else {
      if (key.starts_with("http://") || key.starts_with("https://")) {
        info.predicate = iriComponent(key);
      } else {
        auto ns = mapping_.keyNamespaces.find(std::string{key});
        info.predicate = iriComponent(absl::StrCat(
            ns != mapping_.keyNamespaces.end() ? ns->second
                                               : mapping_.defaultNamespace,
            key));
      }
      // Object rule precedence mirrors the exporter: enum before datatype.
      if (mapping_.enumKeys.contains(std::string{key})) {
        info.rule = ObjectRule::Enum;
      } else if (auto datatype = mapping_.keyDatatypes.find(std::string{key});
                 datatype != mapping_.keyDatatypes.end()) {
        if (datatype->second.empty()) {
          info.rule = ObjectRule::PlainString;
        } else {
          info.rule = ObjectRule::Typed;
          info.datatype = TripleComponent::Iri::fromIrirefWithoutBrackets(
              datatype->second);
        }
      }
    }
    return keyInfos_.emplace(std::string{key}, std::move(info)).first->second;
  }

  const TripleComponent& graphComponent(std::string_view instanceId) {
    auto found = graphs_.find(std::string{instanceId});
    if (found != graphs_.end()) return found->second;
    auto graph = isUri(instanceId)
                     ? iriComponent(instanceId)
                     : iriComponent(absl::StrCat("urn:uuid:", instanceId));
    return graphs_.emplace(std::string{instanceId}, std::move(graph))
        .first->second;
  }

  TripleComponent makeObject(const KeyInfo& key, std::string_view value) const {
    if (key.rule == ObjectRule::Type) {
      if (isUri(value)) return iriComponent(value);
      return iriComponent(absl::StrCat(mapping_.defaultNamespace, value));
    }
    if (isUri(value)) return iriComponent(value);
    switch (key.rule) {
      case ObjectRule::Enum:
        return iriComponent(absl::StrCat(mapping_.defaultNamespace, value));
      case ObjectRule::Typed:
        // qlever's own typed-literal path (identical to the N-Quads parse).
        // Strict by design: an ill-typed value is a data-vs-schema error to be
        // fixed in the instance data or the schema — name the offender.
        try {
          return TurtleParser<Tokenizer>::literalAndDatatypeToTripleComponent(
              value, key.datatype.value(), encodedIriManager_);
        } catch (const std::exception& error) {
          throw std::runtime_error(absl::StrCat(
              key.name, ": value \"", value, "\" is not a valid ",
              key.datatype.value().toStringRepresentation(),
              " — fix the instance data or the schema datatype (",
              error.what(), ")"));
        }
      case ObjectRule::PlainString:
        return plainLiteral(value);
      default:
        break;
    }
    if (isUuid(value)) return iriComponent(absl::StrCat("urn:uuid:", value));
    return plainLiteral(value);
  }

  const TripleComponent& subject(std::string_view id) {
    // Consecutive rows usually share the ID.
    if (id != previousId_) {
      previousId_ = std::string{id};
      previousSubject_ = isUri(id)
                             ? iriComponent(id)
                             : iriComponent(absl::StrCat("urn:uuid:", id));
    }
    return previousSubject_;
  }

 private:
  const TermMapping& mapping_;
  const EncodedIriManager& encodedIriManager_;
  std::unordered_map<std::string, KeyInfo> keyInfos_;
  std::unordered_map<std::string, TripleComponent> graphs_;
  std::string previousId_;
  TripleComponent previousSubject_;
};

}  // namespace

// ____________________________________________________________________________
ArrowTripleParser::Columns ArrowTripleParser::resolveColumns(
    const arrow::RecordBatch& batch, int64_t firstRow) {
  auto resolve = [&](const char* name) {
    auto array = batch.GetColumnByName(name);
    if (array == nullptr) {
      throw std::runtime_error(
          absl::StrCat("arrow ingest: required column '", name, "' missing"));
    }
    return Column::resolve(std::move(array), name);
  };
  return Columns{resolve("ID"), resolve("KEY"), resolve("VALUE"),
                 resolve("INSTANCE_ID"), firstRow};
}

// ____________________________________________________________________________
ArrowTripleParser::ArrowTripleParser(
    std::vector<std::shared_ptr<arrow::RecordBatch>> batches,
    TermMapping mapping, const EncodedIriManager* encodedIriManager)
    : RdfParserBase{encodedIriManager},
      batches_{std::move(batches)},
      mapping_{std::move(mapping)} {
  // Resolve all columns up front (throws on wrong input before any thread
  // starts) and chunk the rows into conversion tasks.
  std::erase_if(batches_, [](const auto& b) { return b->num_rows() == 0; });
  columns_.reserve(batches_.size());
  int64_t firstRow = 0;
  for (const auto& batch : batches_) {
    columns_.push_back(resolveColumns(*batch, firstRow));
    firstRow += batch->num_rows();
  }
  for (size_t i = 0; i < batches_.size(); ++i) {
    int64_t numRows = batches_[i]->num_rows();
    for (int64_t begin = 0; begin < numRows; begin += ROWS_PER_TASK) {
      ranges_.push_back({&columns_[i], begin,
                         std::min<int64_t>(begin + ROWS_PER_TASK, numRows)});
    }
  }

  // Feed the ranges to the worker tasks from a separate thread: pushing
  // blocks when the task queue is full, and the consumer only starts popping
  // after this constructor has returned (same setup as RdfMultifileParser).
  feederThread_ = ad_utility::JThread{[this] {
    for (const auto& range : ranges_) {
      bool active = conversionQueue_.push([this, range] {
        convertRangeAndPush(range);
      });
      if (!active) {
        // The queue was finished prematurely; stop to avoid deadlocks.
        break;
      }
    }
    // After `finish()` returns, all conversions have completed and pushed
    // their results, so the consumer-facing queue can be finished too.
    conversionQueue_.finish();
    finishedBatchQueue_.finish();
  }};
}

// ____________________________________________________________________________
ArrowTripleParser::~ArrowTripleParser() {
  ad_utility::ignoreExceptionIfThrows(
      [this] {
        // Note: the order of these calls is important, see the constructor
        // that sets up the `feederThread_`.
        conversionQueue_.finish();
        finishedBatchQueue_.finish();
        if (feederThread_.joinable()) {
          feederThread_.join();
        }
      },
      "During the destruction of an ArrowTripleParser");
}

// ____________________________________________________________________________
std::vector<TurtleTriple> ArrowTripleParser::convertRange(
    const RowRange& range) const {
  const Columns& c = *range.columns;
  RangeConverter converter{mapping_, encodedIriManager()};

  // Per-dictionary-code shortcut tables (task-local like all caches).
  std::vector<const RangeConverter::KeyInfo*> keyByCode(
      c.key.dict ? c.key.dict->dictionary()->length() : 0, nullptr);
  std::vector<const TripleComponent*> graphByCode(
      c.instance.dict ? c.instance.dict->dictionary()->length() : 0, nullptr);

  std::vector<TurtleTriple> result;
  result.reserve(range.end - range.begin);
  for (int64_t row = range.begin; row < range.end; ++row) {
    // Null VALUE rows are dropped — same as the N-Quads exporters. Null in
    // any other column has no defined export today (the exporters emit
    // broken lines); fail loud instead of silently diverging.
    if (c.value.is_null(row)) continue;
    if (c.id.is_null(row) || c.key.is_null(row) || c.instance.is_null(row)) {
      throw std::runtime_error(absl::StrCat(
          "arrow ingest: null ID/KEY/INSTANCE_ID at row ", c.firstRow + row));
    }

    TurtleTriple triple;
    triple.subject_ = converter.subject(c.id.value(row));

    const RangeConverter::KeyInfo* info;
    if (int64_t code = c.key.dict_code(row); code >= 0) {
      info = keyByCode[code];
      if (info == nullptr)
        info = keyByCode[code] = &converter.keyInfo(c.key.value(row));
    } else {
      info = &converter.keyInfo(c.key.value(row));
    }
    triple.predicate_ = info->predicate;
    triple.object_ = converter.makeObject(*info, c.value.value(row));

    if (int64_t code = c.instance.dict_code(row); code >= 0) {
      const TripleComponent* graph = graphByCode[code];
      if (graph == nullptr)
        graph = graphByCode[code] =
            &converter.graphComponent(c.instance.value(row));
      triple.graphIri_ = *graph;
    } else {
      triple.graphIri_ = converter.graphComponent(c.instance.value(row));
    }
    result.push_back(std::move(triple));
  }
  return result;
}

// ____________________________________________________________________________
void ArrowTripleParser::convertRangeAndPush(const RowRange& range) {
  try {
    auto triples = convertRange(range);
    rowsConverted_ += static_cast<size_t>(range.end - range.begin);
    if (!triples.empty()) {
      // The return value is deliberately ignored: when the queue was
      // finished prematurely there is simply nothing left to do.
      std::ignore = finishedBatchQueue_.push(std::move(triples));
    }
  } catch (...) {
    finishedBatchQueue_.pushException(std::current_exception());
  }
}

// ____________________________________________________________________________
bool ArrowTripleParser::getLineImpl(TurtleTriple*) { AD_FAIL(); }

// ____________________________________________________________________________
std::optional<std::vector<TurtleTriple>> ArrowTripleParser::getBatch() {
  return finishedBatchQueue_.pop();
}
