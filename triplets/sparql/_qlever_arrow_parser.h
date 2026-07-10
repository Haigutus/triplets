// RdfParserBase implementation that yields TurtleTriples straight from
// triplet Arrow columns (ID, KEY, VALUE, INSTANCE_ID) — the zero-copy ingest
// counterpart of the Arrow result decode in _qlever_wrapper.cpp. Term
// mapping reproduces the N-Quads export rules (triplets/export/
// nquads_polars.py + nquads_utils.py) exactly, so the qlever engine sees the
// same graph as the rdflib reference engine, without serializing N-Quads
// text or re-parsing it.
//
// Conversion runs in parallel, mirroring qlever's own RdfMultifileParser:
// the rows are chunked into ranges, worker tasks convert each range into a
// TurtleTriple batch and push it into a bounded thread-safe queue, and
// getBatch() pops finished batches (in no particular order — the index build
// sorts everything anyway). Exceptions from workers travel through the queue
// to the consumer.

#ifndef TRIPLETS_QLEVER_ARROW_PARSER_H
#define TRIPLETS_QLEVER_ARROW_PARSER_H

#include <atomic>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "arrow/array.h"
#include "arrow/record_batch.h"
#include "index/ConstantsIndexBuilding.h"
#include "parser/RdfParser.h"
#include "util/TaskQueue.h"
#include "util/ThreadSafeQueue.h"
#include "util/jthread.h"

#include "_qlever_wrapper.h"  // TermMapping

class ArrowTripleParser : public RdfParserBase {
 public:
  ArrowTripleParser(std::vector<std::shared_ptr<arrow::RecordBatch>> batches,
                    TermMapping mapping,
                    const EncodedIriManager* encodedIriManager);

  // Clean up the background conversion even when the consumer stops early
  // (e.g. on an exception in the index build).
  ~ArrowTripleParser() override;

  // Batch-only parser (like RdfMultifileParser): getBatch() must be used.
  bool getLineImpl(TurtleTriple* triple) override;
  std::optional<std::vector<TurtleTriple>> getBatch() override;
  // Rows converted so far — only used for progress diagnostics.
  size_t getParsePosition() const override { return rowsConverted_.load(); }

 private:
  // One column of a batch: utf8 / large_utf8, optionally dictionary-encoded
  // (any index width), offset- and null-aware. Read-only after construction,
  // so safe to share across worker tasks.
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

  // The four resolved columns of one RecordBatch plus the batch's global
  // starting row (for deterministic row numbers in error messages).
  struct Columns {
    Column id, key, value, instance;
    int64_t firstRow = 0;
  };

  // A conversion work item: rows [begin, end) of one batch.
  struct RowRange {
    const Columns* columns;
    int64_t begin;
    int64_t end;
  };

  static Columns resolveColumns(const arrow::RecordBatch& batch,
                                int64_t firstRow);
  // Convert one row range into a TurtleTriple batch (runs on a worker task;
  // all mutable term caches are task-local inside).
  std::vector<TurtleTriple> convertRange(const RowRange& range) const;
  void convertRangeAndPush(const RowRange& range);

  std::vector<std::shared_ptr<arrow::RecordBatch>> batches_;
  TermMapping mapping_;
  std::vector<Columns> columns_;   // one per batch, resolved up front
  std::vector<RowRange> ranges_;
  std::atomic<size_t> rowsConverted_ = 0;

  // The buffer for the finished batches.
  ad_utility::data_structures::ThreadSafeQueue<std::vector<TurtleTriple>>
      finishedBatchQueue_{QUEUE_SIZE_BEFORE_PARALLEL_PARSING};

  // This queue manages its own worker threads; each task converts one row
  // range and pushes the result to `finishedBatchQueue_` above. Declared
  // *after* the `finishedBatchQueue_`, s.t. when destroying the parser the
  // worker threads are joined before the queue they use is destroyed (same
  // reasoning as in RdfMultifileParser).
  ad_utility::TaskQueue<false> conversionQueue_{
      QUEUE_SIZE_BEFORE_PARALLEL_PARSING, NUM_PARALLEL_PARSER_THREADS};

  // A thread that feeds the row ranges to the worker tasks (feeding blocks
  // when the task queue is full, so it must not run on the constructing
  // thread — the consumer has to be able to start popping).
  ad_utility::JThread feederThread_;
};

#endif  // TRIPLETS_QLEVER_ARROW_PARSER_H
