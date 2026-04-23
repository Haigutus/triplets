// Quick test: build qlever index from NQuads file, run a query.
#include <iostream>
#include <chrono>
#include "libqlever/Qlever.h"
#include "util/MemorySize/MemorySize.h"

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <input.nq> <index_basename> [query]" << std::endl;
        return 1;
    }
    std::string inputFile = argv[1];
    std::string indexBasename = argv[2];
    std::string queryStr = argc > 3 ? argv[3]
        : "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }";

    // Build index
    std::cout << "Building index from " << inputFile << " ..." << std::endl;
    auto t0 = std::chrono::high_resolution_clock::now();

    qlever::IndexBuilderConfig config;
    config.inputFiles_.push_back({inputFile, qlever::Filetype::NQuad, std::nullopt});
    config.baseName_ = indexBasename;
    config.memoryLimit_ = ad_utility::MemorySize::gigabytes(2);

    try {
        qlever::Qlever::buildIndex(config);
    } catch (const std::exception& e) {
        std::cerr << "Index build failed: " << e.what() << std::endl;
        return 1;
    }
    auto t1 = std::chrono::high_resolution_clock::now();
    auto build_ms = std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();
    std::cout << "Index built in " << build_ms << " ms" << std::endl;

    // Load index
    std::cout << "Loading index ..." << std::endl;
    t0 = std::chrono::high_resolution_clock::now();
    qlever::EngineConfig engineConfig{config};
    engineConfig.persistUpdates_ = false;
    qlever::Qlever engine{engineConfig};
    t1 = std::chrono::high_resolution_clock::now();
    auto load_ms = std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();
    std::cout << "Index loaded in " << load_ms << " ms" << std::endl;

    // Run query
    std::cout << "\nExecuting: " << queryStr << std::endl;
    t0 = std::chrono::high_resolution_clock::now();
    std::string result;
    try {
        result = engine.query(queryStr);
    } catch (const std::exception& e) {
        std::cerr << "Query failed: " << e.what() << std::endl;
        return 1;
    }
    t1 = std::chrono::high_resolution_clock::now();
    auto query_ms = std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();

    std::cout << "Query completed in " << query_ms << " ms" << std::endl;
    std::cout << "\nResult:\n" << result.substr(0, 2000) << std::endl;

    return 0;
}
