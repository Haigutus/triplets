// RdfParserBase implementation that yields TurtleTriples straight from
// triplet Arrow columns (ID, KEY, VALUE, INSTANCE_ID) — the zero-copy ingest
// counterpart of the Arrow result decode in _qlever_wrapper.cpp. Term
// mapping reproduces the N-Quads export rules (triplets/export/
// nquads_polars.py + nquads_utils.py) exactly, so the qlever engine sees the
// same graph as the rdflib reference engine, without serializing N-Quads
// text or re-parsing it.

#ifndef TRIPLETS_QLEVER_ARROW_PARSER_H
#define TRIPLETS_QLEVER_ARROW_PARSER_H

#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "arrow/array.h"
#include "arrow/record_batch.h"
#include "parser/RdfParser.h"

#include "_qlever_wrapper.h"  // TermMapping

class ArrowTripleParser : public RdfParserBase {
 public:
  ArrowTripleParser(std::vector<std::shared_ptr<arrow::RecordBatch>> batches,
                    TermMapping mapping,
                    const EncodedIriManager* encodedIriManager);

  bool getLineImpl(TurtleTriple* triple) override;
  std::optional<std::vector<TurtleTriple>> getBatch() override;
  // Rows consumed so far — only used for progress diagnostics.
  size_t getParsePosition() const override { return rowsConsumed_; }

 private:
  // How the object term of a row is built, resolved once per KEY.
  enum class ObjectRule { Type, Enum, Typed, PlainString, Default };
  struct KeyInfo {
    TripleComponent predicate;
    ObjectRule rule;
    std::optional<TripleComponent::Iri> datatype;  // only for Typed
  };

  // One column of the current batch: utf8 / large_utf8, optionally
  // dictionary-encoded (any index width), offset- and null-aware.
  struct Column {
    std::shared_ptr<arrow::Array> array;         // owns; also the null source
    const arrow::StringArray* utf8 = nullptr;    // exactly one of these two
    const arrow::LargeStringArray* large = nullptr;
    const arrow::DictionaryArray* dict = nullptr;  // set when dictionary-encoded

    bool isNull(int64_t row) const { return array->IsNull(row); }
    std::string_view value(int64_t row) const;
    // Dictionary code for this row, or -1 for non-dictionary columns.
    int64_t code(int64_t row) const { return dict ? dict->GetValueIndex(row) : -1; }
  };

  bool next(TurtleTriple* triple);
  void openBatch(size_t index);
  TripleComponent iriComponent(std::string_view iri) const;
  const KeyInfo& keyInfo(std::string_view key);
  const TripleComponent& graphComponent(std::string_view instanceId);
  TripleComponent makeObject(const KeyInfo& key, std::string_view value) const;

  std::vector<std::shared_ptr<arrow::RecordBatch>> batches_;
  TermMapping mapping_;

  // Cursor.
  size_t batchIndex_ = 0;
  int64_t row_ = 0;
  int64_t batchRows_ = 0;
  size_t rowsConsumed_ = 0;
  Column id_, key_, value_, instance_;

  // Term caches. The maps are node-based, so pointers into them stay valid;
  // the per-dictionary-code vectors short-circuit the string lookup for
  // dictionary-encoded KEY / INSTANCE_ID columns.
  std::unordered_map<std::string, KeyInfo> keyInfos_;
  std::unordered_map<std::string, TripleComponent> graphs_;
  std::vector<const KeyInfo*> keyByCode_;
  std::vector<const TripleComponent*> graphByCode_;
  std::string previousId_;
  TripleComponent previousSubject_;
};

#endif  // TRIPLETS_QLEVER_ARROW_PARSER_H
