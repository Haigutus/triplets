// Thin C++ shim around qlever's official embedding facade (src/libqlever) for
// the Cython binding. Two boundaries:
//  - strings: SPARQL text in, a spec-stable serialization out (SPARQL 1.1
//    JSON / Turtle / CSV) — used for ASK and diagnostics.
//  - Arrow: SPARQL text in, decoded Arrow string columns out (select_arrow /
//    construct_arrow) — the data path. All heavy lifting (query execution,
//    vocabulary decode, buffer building) happens here on the C++ side; the
//    Cython layer only wraps the finished arrays zero-copy.

#ifndef TRIPLETS_QLEVER_WRAPPER_H
#define TRIPLETS_QLEVER_WRAPPER_H

#include <memory>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace qlever {
class Qlever;
}
namespace arrow {
class Array;
class RecordBatch;
}

// Decoded result columns for the Cython layer to wrap (zero-copy).
struct ArrowColumns {
  std::vector<std::shared_ptr<arrow::Array>> columns;
  std::vector<std::string> names;
};

// Flattened export-schema metadata for the Arrow ingest, built by
// triplets.export.nquads_utils.build_key_metadata on the Python side (the
// single source of truth for rdf_map interpretation).
struct TermMapping {
  std::unordered_set<std::string> enumKeys;
  std::unordered_map<std::string, std::string> keyNamespaces;
  // KEY → full xsd datatype IRI; "" means schema-typed as plain xsd:string.
  std::unordered_map<std::string, std::string> keyDatatypes;
  // Namespace for bare predicates / Type classes / enum values (CIM_NS).
  std::string defaultNamespace;
};

class QleverWrapper {
 public:
  // Silence (or restore) qlever's own INFO logging on stdout.
  static void set_quiet(bool quiet);

  // Build an on-disk index. input_file: RDF file path; filetype: "nq"
  // (N-Quads/N-Triples) or "ttl" (Turtle); index_basename: base path for the
  // index files; memory_gb: build memory limit.
  static void build_index(const std::string& input_file,
                          const std::string& index_basename,
                          const std::string& filetype = "nq",
                          int memory_gb = 4);

  // Build an on-disk index directly from triplet Arrow columns (ID, KEY,
  // VALUE, INSTANCE_ID — resolved by name in each batch): the batches feed
  // qlever's index builder through an injected parser, no RDF text
  // serialization or re-parsing anywhere.
  static void build_index_from_arrow(
      std::vector<std::shared_ptr<arrow::RecordBatch>> batches,
      TermMapping mapping, const std::string& index_basename,
      int memory_gb = 4);

  // Load an existing index for querying (read-only).
  explicit QleverWrapper(const std::string& index_basename, int memory_gb = 4);
  ~QleverWrapper();

  // `scope_graphs` on the query methods below are named-graph IRIs passed as
  // SPARQL-protocol dataset clauses (`default-graph-uri`): the query is
  // evaluated against exactly the union of these graphs, and per the
  // protocol they take precedence over any FROM inside the query. Empty =
  // the full default dataset (union of all graphs).

  // Execute a SPARQL query. media_type: "sparqljson" (SELECT/ASK),
  // "turtle" (CONSTRUCT/DESCRIBE), "tsv" or "csv".
  std::string query(const std::string& sparql,
                    const std::string& media_type = "sparqljson",
                    const std::vector<std::string>& scope_graphs = {}) const;

  // Execute a SELECT query and decode the result directly into Arrow utf8
  // columns (one per projected variable; unbound values become nulls).
  ArrowColumns select_arrow(const std::string& sparql,
                            const std::vector<std::string>& scope_graphs = {}) const;

  // Execute a CONSTRUCT/DESCRIBE query into three Arrow utf8 columns
  // (subject / predicate / object, N-Triples-form terms).
  ArrowColumns construct_arrow(const std::string& sparql,
                               const std::vector<std::string>& scope_graphs = {}) const;

 private:
  std::unique_ptr<qlever::Qlever> engine_;
};

#endif  // TRIPLETS_QLEVER_WRAPPER_H
