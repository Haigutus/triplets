// Thin C++ wrapper around libqlever for Cython binding.
// Simplifies the complex C++20 API into plain types that Cython can handle.

#ifndef QLEVER_WRAPPER_H
#define QLEVER_WRAPPER_H

#include <string>
#include <vector>
#include <memory>

// Forward declare the qlever namespace types
namespace qlever {
    class Qlever;
}

class QleverWrapper {
public:
    // Build an index from an input file (Turtle or NTriples format).
    // input_file: path to the RDF file
    // index_basename: base path for index files (e.g., "/tmp/myindex")
    // filetype: "turtle" or "ntriples"
    // memory_gb: memory limit in GB (default 1)
    static void build_index(const std::string& input_file,
                           const std::string& index_basename,
                           const std::string& filetype = "turtle",
                           int memory_gb = 1);

    // Create a QleverWrapper by loading an existing index.
    // index_basename: base path used during build_index
    // memory_gb: memory limit in GB
    explicit QleverWrapper(const std::string& index_basename, int memory_gb = 1);
    ~QleverWrapper();

    // Execute a SPARQL query and return results as JSON string.
    std::string query(const std::string& sparql) const;

    // Execute a SPARQL query and return results as TSV string.
    std::string query_tsv(const std::string& sparql) const;

private:
    std::unique_ptr<qlever::Qlever> engine_;
};

#endif // QLEVER_WRAPPER_H
