// Thin C++ shim around qlever's official embedding facade — see the header.

#include "_qlever_wrapper.h"

#include <stdexcept>

#include <iostream>

#include "libqlever/Qlever.h"
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

QleverWrapper::QleverWrapper(const std::string& index_basename, int memory_gb) {
  qlever::EngineConfig config;
  config.baseName_ = index_basename;
  config.memoryLimit_ = ad_utility::MemorySize::gigabytes(memory_gb);
  config.persistUpdates_ = false;  // read-only usage
  engine_ = std::make_unique<qlever::Qlever>(config);
}

QleverWrapper::~QleverWrapper() = default;

std::string QleverWrapper::query(const std::string& sparql,
                                 const std::string& media_type) const {
  return engine_->query(sparql, mediaTypeFromString(media_type));
}
