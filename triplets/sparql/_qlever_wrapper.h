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
#include <vector>

namespace qlever {
class Qlever;
}
namespace arrow {
class Array;
}

// Decoded result columns for the Cython layer to wrap (zero-copy).
struct ArrowColumns {
  std::vector<std::shared_ptr<arrow::Array>> columns;
  std::vector<std::string> names;
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

  // Load an existing index for querying (read-only).
  explicit QleverWrapper(const std::string& index_basename, int memory_gb = 4);
  ~QleverWrapper();

  // Execute a SPARQL query. media_type: "sparqljson" (SELECT/ASK),
  // "turtle" (CONSTRUCT/DESCRIBE), "tsv" or "csv".
  std::string query(const std::string& sparql,
                    const std::string& media_type = "sparqljson") const;

  // Execute a SELECT query and decode the result directly into Arrow utf8
  // columns (one per projected variable; unbound values become nulls).
  ArrowColumns select_arrow(const std::string& sparql) const;

  // Execute a CONSTRUCT/DESCRIBE query into three Arrow utf8 columns
  // (subject / predicate / object, N-Triples-form terms).
  ArrowColumns construct_arrow(const std::string& sparql) const;

 private:
  std::unique_ptr<qlever::Qlever> engine_;
};

#endif  // TRIPLETS_QLEVER_WRAPPER_H
