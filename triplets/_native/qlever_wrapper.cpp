// Thin C++ wrapper around libqlever for Cython binding.

#include "qlever_wrapper.h"
#include "libqlever/Qlever.h"
#include "util/MemorySize/MemorySize.h"

void QleverWrapper::build_index(const std::string& input_file,
                                const std::string& index_basename,
                                const std::string& filetype,
                                int memory_gb) {
    qlever::IndexBuilderConfig config;
    config.baseName_ = index_basename;
    config.memoryLimit_ = ad_utility::MemorySize::gigabytes(memory_gb);

    // Determine file type
    qlever::Filetype ft = qlever::Filetype::Turtle;
    if (filetype == "ntriples" || filetype == "nt") {
        // NTriples files use NQuad parser (each line is a triple/quad)
        ft = qlever::Filetype::NQuad;
    }

    config.inputFiles_.push_back({input_file, ft, std::nullopt});
    qlever::Qlever::buildIndex(std::move(config));
}

QleverWrapper::QleverWrapper(const std::string& index_basename, int memory_gb) {
    qlever::EngineConfig config;
    config.baseName_ = index_basename;
    config.memoryLimit_ = ad_utility::MemorySize::gigabytes(memory_gb);
    config.persistUpdates_ = false;  // read-only for our use case
    engine_ = std::make_unique<qlever::Qlever>(config);
}

QleverWrapper::~QleverWrapper() = default;

std::string QleverWrapper::query(const std::string& sparql) const {
    return engine_->query(std::string(sparql),
                         ad_utility::MediaType::sparqlJson);
}

std::string QleverWrapper::query_tsv(const std::string& sparql) const {
    return engine_->query(std::string(sparql),
                         ad_utility::MediaType::tsv);
}
