// Shared Arrow string-column accessor for the compiled extensions.
//
// One string column of triplet data: utf8 / large_utf8, optionally
// dictionary-encoded (any index width), offset- and null-aware. Read-only
// after resolve(), so safe to share across threads. Header-only, arrow + std
// includes only (the cimxml build has no absl/qlever toolchain).
//
// Consumers: sparql/_qlever_arrow_parser (index ingest) and
// export/cimxml_cython_pugixml.pyx (XML export).

#ifndef TRIPLETS_ARROW_STRING_COLUMN_H
#define TRIPLETS_ARROW_STRING_COLUMN_H

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "arrow/array.h"
#include "arrow/util/config.h"

// arrow::StringViewArray (the "German string" layout, polars/duckdb native)
// exists since Arrow C++ 16; older toolchains compile without the branch.
#define TRIPLETS_ARROW_HAS_STRING_VIEW (ARROW_VERSION_MAJOR >= 16)

namespace triplets_arrow {

struct StringColumn {
  std::shared_ptr<arrow::Array> array;           // owns; also the null source
  const arrow::StringArray* utf8 = nullptr;      // exactly one of utf8/large/view set
  const arrow::LargeStringArray* large = nullptr;
#if TRIPLETS_ARROW_HAS_STRING_VIEW
  const arrow::StringViewArray* view = nullptr;
#endif
  const arrow::DictionaryArray* dict = nullptr;  // set when dictionary-encoded

  static StringColumn resolve(std::shared_ptr<arrow::Array> array,
                              const std::string& name) {
    StringColumn column;
    column.array = std::move(array);
    const arrow::Array* values = column.array.get();
    if (values->type_id() == arrow::Type::DICTIONARY) {
      column.dict = static_cast<const arrow::DictionaryArray*>(values);
      values = column.dict->dictionary().get();
    }
    if (values->type_id() == arrow::Type::STRING) {
      column.utf8 = static_cast<const arrow::StringArray*>(values);
    } else if (values->type_id() == arrow::Type::LARGE_STRING) {
      column.large = static_cast<const arrow::LargeStringArray*>(values);
#if TRIPLETS_ARROW_HAS_STRING_VIEW
    } else if (values->type_id() == arrow::Type::STRING_VIEW) {
      column.view = static_cast<const arrow::StringViewArray*>(values);
#endif
    } else {
      throw std::runtime_error(
          "column '" + name +
          "' must be a (dictionary-encoded) string column, got " +
          column.array->type()->ToString());
    }
    return column;
  }

  bool is_null(int64_t row) const { return array->IsNull(row); }

  // Zero-copy view into the Arrow buffer (dictionary-aware).
  std::string_view value(int64_t row) const {
    int64_t index = dict ? dict->GetValueIndex(row) : row;
    if (utf8) return std::string_view{utf8->GetView(index)};
#if TRIPLETS_ARROW_HAS_STRING_VIEW
    if (view) return std::string_view{view->GetView(index)};
#endif
    return std::string_view{large->GetView(index)};
  }

  // Dictionary code for this row, or -1 for non-dictionary columns.
  int64_t dict_code(int64_t row) const { return dict ? dict->GetValueIndex(row) : -1; }
};

}  // namespace triplets_arrow

#endif  // TRIPLETS_ARROW_STRING_COLUMN_H
