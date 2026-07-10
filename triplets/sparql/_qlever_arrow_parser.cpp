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

#include "absl/strings/str_cat.h"
#include "parser/NormalizedString.h"
#include "parser/Tokenizer.h"

namespace {

constexpr std::string_view RDF_TYPE =
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#type";

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
std::string_view ArrowTripleParser::Column::value(int64_t row) const {
  if (dict) {
    int64_t index = dict->GetValueIndex(row);
    if (utf8) return utf8->GetView(index);
    return large->GetView(index);
  }
  if (utf8) return utf8->GetView(row);
  return large->GetView(row);
}

// ____________________________________________________________________________
ArrowTripleParser::ArrowTripleParser(
    std::vector<std::shared_ptr<arrow::RecordBatch>> batches,
    TermMapping mapping, const EncodedIriManager* encodedIriManager)
    : RdfParserBase{encodedIriManager},
      batches_{std::move(batches)},
      mapping_{std::move(mapping)} {
  // Drop empty batches up front so the cursor logic only sees real rows.
  std::erase_if(batches_, [](const auto& b) { return b->num_rows() == 0; });
  if (!batches_.empty()) openBatch(0);
}

// ____________________________________________________________________________
void ArrowTripleParser::openBatch(size_t index) {
  batchIndex_ = index;
  row_ = 0;
  const auto& batch = *batches_[index];
  batchRows_ = batch.num_rows();

  auto resolve = [&](const char* name) {
    auto array = batch.GetColumnByName(name);
    if (array == nullptr) {
      throw std::runtime_error(
          absl::StrCat("arrow ingest: required column '", name, "' missing"));
    }
    Column column;
    column.array = array;
    const arrow::Array* values = array.get();
    if (array->type_id() == arrow::Type::DICTIONARY) {
      column.dict = static_cast<const arrow::DictionaryArray*>(array.get());
      values = column.dict->dictionary().get();
    }
    if (values->type_id() == arrow::Type::STRING) {
      column.utf8 = static_cast<const arrow::StringArray*>(values);
    } else if (values->type_id() == arrow::Type::LARGE_STRING) {
      column.large = static_cast<const arrow::LargeStringArray*>(values);
    } else {
      throw std::runtime_error(
          absl::StrCat("arrow ingest: column '", name,
                       "' must be a (dictionary-encoded) string column, got ",
                       array->type()->ToString()));
    }
    return column;
  };
  id_ = resolve("ID");
  key_ = resolve("KEY");
  value_ = resolve("VALUE");
  instance_ = resolve("INSTANCE_ID");

  // Fresh per-code shortcut tables: dictionaries can differ per batch.
  keyByCode_.assign(
      key_.dict ? key_.dict->dictionary()->length() : 0, nullptr);
  graphByCode_.assign(
      instance_.dict ? instance_.dict->dictionary()->length() : 0, nullptr);
}

// ____________________________________________________________________________
TripleComponent ArrowTripleParser::iriComponent(std::string_view iri) const {
  auto component = TripleComponent::Iri::fromIrirefWithoutBrackets(iri);
  // Same folding the text parser applies after building each IRI.
  if (auto id = encodedIriManager().encode(component.toStringRepresentation()))
    return TripleComponent{id.value()};
  return TripleComponent{std::move(component)};
}

// ____________________________________________________________________________
const ArrowTripleParser::KeyInfo& ArrowTripleParser::keyInfo(
    std::string_view key) {
  auto found = keyInfos_.find(std::string{key});
  if (found != keyInfos_.end()) return found->second;

  KeyInfo info{TripleComponent{}, ObjectRule::Default, std::nullopt};
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
        info.datatype =
            TripleComponent::Iri::fromIrirefWithoutBrackets(datatype->second);
      }
    }
  }
  return keyInfos_.emplace(std::string{key}, std::move(info)).first->second;
}

// ____________________________________________________________________________
const TripleComponent& ArrowTripleParser::graphComponent(
    std::string_view instanceId) {
  auto found = graphs_.find(std::string{instanceId});
  if (found != graphs_.end()) return found->second;
  auto graph = isUri(instanceId)
                   ? iriComponent(instanceId)
                   : iriComponent(absl::StrCat("urn:uuid:", instanceId));
  return graphs_.emplace(std::string{instanceId}, std::move(graph))
      .first->second;
}

// ____________________________________________________________________________
TripleComponent ArrowTripleParser::makeObject(const KeyInfo& key,
                                              std::string_view value) const {
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
      return TurtleParser<Tokenizer>::literalAndDatatypeToTripleComponent(
          value, key.datatype.value(), encodedIriManager());
    case ObjectRule::PlainString:
      return plainLiteral(value);
    default:
      break;
  }
  if (isUuid(value)) return iriComponent(absl::StrCat("urn:uuid:", value));
  return plainLiteral(value);
}

// ____________________________________________________________________________
bool ArrowTripleParser::next(TurtleTriple* triple) {
  while (true) {
    if (batches_.empty() || batchIndex_ >= batches_.size()) return false;
    if (row_ >= batchRows_) {
      if (batchIndex_ + 1 >= batches_.size()) {
        ++batchIndex_;  // exhausted
        return false;
      }
      openBatch(batchIndex_ + 1);
      continue;
    }
    int64_t row = row_++;
    ++rowsConsumed_;

    // Null VALUE rows are dropped — same as the N-Quads exporters. Null in
    // any other column has no defined export today (the exporters emit
    // broken lines); fail loud instead of silently diverging.
    if (value_.isNull(row)) continue;
    if (id_.isNull(row) || key_.isNull(row) || instance_.isNull(row)) {
      throw std::runtime_error(absl::StrCat(
          "arrow ingest: null ID/KEY/INSTANCE_ID at row ", rowsConsumed_ - 1));
    }

    // Subject: consecutive rows usually share the ID.
    std::string_view id = id_.value(row);
    if (id != previousId_) {
      previousId_ = std::string{id};
      previousSubject_ = isUri(id)
                             ? iriComponent(id)
                             : iriComponent(absl::StrCat("urn:uuid:", id));
    }
    triple->subject_ = previousSubject_;

    // Predicate + object rule, cached per KEY (per-code shortcut when the
    // column is dictionary-encoded).
    const KeyInfo* info;
    if (int64_t code = key_.code(row); code >= 0) {
      info = keyByCode_[code];
      if (info == nullptr) info = keyByCode_[code] = &keyInfo(key_.value(row));
    } else {
      info = &keyInfo(key_.value(row));
    }
    triple->predicate_ = info->predicate;
    triple->object_ = makeObject(*info, value_.value(row));

    if (int64_t code = instance_.code(row); code >= 0) {
      const TripleComponent* graph = graphByCode_[code];
      if (graph == nullptr)
        graph = graphByCode_[code] = &graphComponent(instance_.value(row));
      triple->graphIri_ = *graph;
    } else {
      triple->graphIri_ = graphComponent(instance_.value(row));
    }
    return true;
  }
}

// ____________________________________________________________________________
bool ArrowTripleParser::getLineImpl(TurtleTriple* triple) {
  return next(triple);
}

// ____________________________________________________________________________
std::optional<std::vector<TurtleTriple>> ArrowTripleParser::getBatch() {
  std::vector<TurtleTriple> result;
  result.reserve(100'000);
  TurtleTriple triple;
  while (result.size() < 100'000 && next(&triple)) {
    result.push_back(std::move(triple));
  }
  if (result.empty()) return std::nullopt;
  return result;
}
