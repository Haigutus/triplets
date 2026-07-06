// Thin C++ shim around qlever's official embedding facade (src/libqlever) for
// the Cython binding. The boundary stays string-only: SPARQL text in, a
// spec-stable serialization out (SPARQL 1.1 JSON / TSV / Turtle) — qlever's
// internal API churn is absorbed upstream by the facade, and here by ~50 lines.

#ifndef TRIPLETS_QLEVER_WRAPPER_H
#define TRIPLETS_QLEVER_WRAPPER_H

#include <memory>
#include <string>

namespace qlever {
class Qlever;
}

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

 private:
  std::unique_ptr<qlever::Qlever> engine_;
};

#endif  // TRIPLETS_QLEVER_WRAPPER_H
