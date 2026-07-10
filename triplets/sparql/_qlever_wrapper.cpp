// Thin C++ shim around qlever's official embedding facade — see the header.

#include "_qlever_wrapper.h"

#include <stdexcept>

#include <iostream>

#include "arrow/array/builder_binary.h"

#include "_qlever_arrow_parser.h"
#include "engine/ConstructTripleGenerator.h"
#include "engine/ExportQueryExecutionTrees.h"
#include "engine/QueryExecutionContext.h"
#include "engine/QueryExecutionTree.h"
#include "index/ExportIds.h"
#include "libqlever/Qlever.h"
#include "parser/ParsedQuery.h"
#include "parser/sparqlParser/DatasetClause.h"
#include "util/CancellationHandle.h"
#include "util/Log.h"
#include "util/MemorySize/MemorySize.h"

namespace {
struct NullBuffer : std::streambuf {
  int overflow(int c) override { return c; }
};
NullBuffer nullBuffer;
std::ostream nullStream(&nullBuffer);
}  // namespace

void QleverWrapper::set_quiet(bool quiet) {
  ad_utility::LogstreamChoice::get().setStream(quiet ? &nullStream : &std::cout);
}

static ad_utility::MediaType mediaTypeFromString(const std::string& name) {
  if (name == "sparqljson") return ad_utility::MediaType::sparqlJson;
  if (name == "turtle") return ad_utility::MediaType::turtle;
  if (name == "tsv") return ad_utility::MediaType::tsv;
  if (name == "csv") return ad_utility::MediaType::csv;
  throw std::invalid_argument("unknown media type: " + name);
}

void QleverWrapper::build_index(const std::string& input_file,
                                const std::string& index_basename,
                                const std::string& filetype, int memory_gb) {
  qlever::IndexBuilderConfig config;
  config.baseName_ = index_basename;
  config.memoryLimit_ = ad_utility::MemorySize::gigabytes(memory_gb);
  qlever::Filetype type =
      filetype == "ttl" ? qlever::Filetype::Turtle : qlever::Filetype::NQuad;
  config.inputFiles_.push_back({input_file, type, std::nullopt});
  qlever::Qlever::buildIndex(std::move(config));
}

void QleverWrapper::build_index_from_arrow(
    std::vector<std::shared_ptr<arrow::RecordBatch>> batches,
    TermMapping mapping, const std::string& index_basename, int memory_gb) {
  qlever::IndexBuilderConfig config;
  config.baseName_ = index_basename;
  config.memoryLimit_ = ad_utility::MemorySize::gigabytes(memory_gb);
  qlever::Qlever::buildIndex(
      std::move(config),
      [batches = std::move(batches), mapping = std::move(mapping)](
          const EncodedIriManager* encodedIriManager) mutable {
        return std::make_unique<ArrowTripleParser>(
            std::move(batches), std::move(mapping), encodedIriManager);
      });
}

QleverWrapper::QleverWrapper(const std::string& index_basename, int memory_gb) {
  qlever::EngineConfig config;
  config.baseName_ = index_basename;
  config.memoryLimit_ = ad_utility::MemorySize::gigabytes(memory_gb);
  config.persistUpdates_ = false;  // read-only usage
  engine_ = std::make_unique<qlever::Qlever>(config);
}

QleverWrapper::~QleverWrapper() = default;

namespace {

// Scope IRIs → SPARQL-protocol dataset clauses (`default-graph-uri`): the
// query runs against exactly the union of these graphs, taking precedence
// over any FROM inside the query text (which is therefore never modified).
// This is qlever's native protocol-dataset mechanism on parseAndPlanQuery.
std::vector<DatasetClause> datasetClauses(
    const std::vector<std::string>& scope_graphs) {
  std::vector<DatasetClause> datasets;
  datasets.reserve(scope_graphs.size());
  for (const auto& graph : scope_graphs) {
    datasets.push_back(
        {TripleComponent::Iri::fromIrirefWithoutBrackets(graph), false});
  }
  return datasets;
}

}  // namespace

std::string QleverWrapper::query(
    const std::string& sparql, const std::string& media_type,
    const std::vector<std::string>& scope_graphs) const {
  return engine_->query(
      engine_->parseAndPlanQuery(sparql, datasetClauses(scope_graphs)),
      mediaTypeFromString(media_type));
}

namespace {

void checkStatus(const arrow::Status& status) {
  if (!status.ok()) throw std::runtime_error(status.ToString());
}

std::shared_ptr<arrow::Array> finish(arrow::StringBuilder& builder) {
  std::shared_ptr<arrow::Array> array;
  checkStatus(builder.Finish(&array));
  return array;
}

// Inline replica of the (private) ExportQueryExecutionTrees::
// compensateForLimitOffsetClause: when the execution tree already applies
// LIMIT/OFFSET internally, the export must not apply the offset again.
LimitOffsetClause exportLimit(const ParsedQuery& parsedQuery,
                              const QueryExecutionTree& qet) {
  auto limit = parsedQuery._limitOffset;
  if (qet.handlesLimitOffset() != LimitOffsetHandling::NONE) {
    limit._offset = 0;
  }
  return limit;
}

}  // namespace

ArrowColumns QleverWrapper::select_arrow(
    const std::string& sparql,
    const std::vector<std::string>& scope_graphs) const {
  auto planned = engine_->parseAndPlanQuery(sparql, datasetClauses(scope_graphs));
  const auto& parsedQuery = planned.parsedQuery();
  const auto& qet = planned.queryExecutionTree();
  if (!parsedQuery.hasSelectClause()) {
    throw std::invalid_argument("select_arrow expects a SELECT query");
  }
  auto limit = exportLimit(parsedQuery, qet);
  const auto& selectClause = parsedQuery.selectClause();
  auto columnIndices = qet.selectedVariablesToColumnIndices(selectClause, true);

  ArrowColumns out;
  for (auto& variable : selectClause.getSelectedVariablesAsStrings()) {
    out.names.push_back(variable.substr(1));  // strip the '?'
  }

  // One utf8 builder per projected variable — decoded values go straight into
  // Arrow buffers (offsets + data + validity); unbound cells become nulls.
  std::vector<arrow::StringBuilder> builders(columnIndices.size());
  std::shared_ptr<const Result> result = qet.getResult(true);
  const auto& index = planned.queryExecutionContext().getIndex();
  uint64_t resultSize = 0;
  for (const auto& tableWithRange :
       ExportQueryExecutionTrees::getRowIndices(limit, *result, resultSize)) {
    const auto& table = tableWithRange.tableWithVocab_;
    for (uint64_t row : tableWithRange.view_) {
      for (size_t j = 0; j < columnIndices.size(); ++j) {
        if (!columnIndices[j].has_value()) {
          checkStatus(builders[j].AppendNull());  // variable never bound
          continue;
        }
        Id id = table.idTable()(row, columnIndices[j]->columnIndex_);
        // csv-mode decode: plain lexical values (IRIs bare, literals
        // unquoted, datatype dropped) — triplets are all-string
        auto value = ql::exportIds::idToStringAndType<true>(
            index, id, table.localVocab());
        checkStatus(value.has_value() ? builders[j].Append(value->first)
                                      : builders[j].AppendNull());
      }
    }
  }
  for (auto& builder : builders) out.columns.push_back(finish(builder));
  return out;
}

ArrowColumns QleverWrapper::construct_arrow(
    const std::string& sparql,
    const std::vector<std::string>& scope_graphs) const {
  auto planned = engine_->parseAndPlanQuery(sparql, datasetClauses(scope_graphs));
  const auto& parsedQuery = planned.parsedQuery();
  const auto& qet = planned.queryExecutionTree();
  if (!parsedQuery.hasConstructClause()) {
    throw std::invalid_argument(
        "construct_arrow expects a CONSTRUCT/DESCRIBE query");
  }
  auto limit = exportLimit(parsedQuery, qet);
  const auto& constructTriples = parsedQuery.constructClause().triples_;

  std::vector<arrow::StringBuilder> builders(3);
  uint64_t resultSize = 0;
  auto handle = std::make_shared<ad_utility::CancellationHandle<>>();
  // Mirrors the (private) ExportQueryExecutionTrees::
  // constructQueryResultToStringTriples; `result` stays alive for the whole
  // consumption of the lazy triple range below.
  std::shared_ptr<const Result> result = qet.getResult(true);
  auto rowIndices = ExportQueryExecutionTrees::getRowIndices(
      limit, *result, resultSize, constructTriples.size());
  auto triples = qlever::constructExport::ConstructTripleGenerator::
      generateStringTriples(constructTriples, qet.getVariableColumns(),
                            planned.queryExecutionContext().getIndex(),
                            std::move(handle), std::move(rowIndices),
                            limit._offset);
  for (auto& triple : triples) {
    checkStatus(builders[0].Append(triple.subject_));
    checkStatus(builders[1].Append(triple.predicate_));
    checkStatus(builders[2].Append(triple.object_));
  }
  ArrowColumns out;
  out.names = {"subject", "predicate", "object"};
  for (auto& builder : builders) out.columns.push_back(finish(builder));
  return out;
}
