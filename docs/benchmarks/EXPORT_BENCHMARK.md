# CIM XML Export Benchmark Results

**Date**: 2026-04-06
**Dataset**: CGMES v2.4.15 RealGrid Test Configuration (4 instances from 1,146,215 triplet rows, ~88 MB XML output)
**System**: Linux 6.19.9, Python 3.13, pandas 3.0, Polars 1.38, lxml 6.0.2, pyarrow 23.0, pugixml (source)

## Summary

| Exporter | Avg (s) | Min (s) | vs Original | Tech Stack |
|----------|---------|---------|-------------|------------|
| **arrow_pugixml** | **0.54** | **0.48** | **11.0x** | Arrow zero-copy read → pugixml C++ DOM (Cython) |
| cython_pugixml | 0.76 | 0.66 | 7.9x | numpy object arrays → pugixml C++ DOM (Cython) |
| cython_string | 0.82 | 0.82 | 7.3x | numpy object arrays → C++ std::string (Cython) |
| polars_string | 0.95 | 0.93 | 6.4x | Polars iter_rows → Python string builder |
| string_builder | 4.83 | 4.81 | 1.2x | numpy object arrays → Python string builder |
| **lxml_original** | **6.01** | **5.94** | **1.0x** | **pandas itertuples → lxml ElementMaker (baseline)** |
| lxml_optimized | 8.11 | 8.09 | 0.74x | lxml with pre-built tag cache (worse!) |

All variants verified correct (size ratio ~1.0, same file count and content).

## Key Findings

### The real bottleneck is pandas iteration, not XML generation

The original export spends most of its time in Python-level DataFrame iteration (`itertuples()`), not in lxml element creation. This was confirmed by two observations:

1. **Polars string builder (pure Python, no C extensions) is 6.4x faster** — simply replacing `itertuples()` with Polars `iter_rows()` + Python string concatenation (no XML library at all) runs in under 1 second.

2. **The "lxml optimized" variant is actually slower** — pre-building a tag cache and using numpy array access with lxml is 0.74x of baseline. lxml Element creation overhead dominates when you're still creating Python objects per row.

### Where time goes (original, 6.0s for 4 instances)

| Step | Approx. | Source |
|------|---------|--------|
| pandas `itertuples()` overhead | ~3.5s | 58% — Python object creation per row |
| lxml Element/QName creation | ~1.5s | 25% — `ElementMaker`, `QName()`, `attrib[]` |
| `etree.tostring()` serialization | ~0.5s | 8% — pretty-print + encoding |
| Profile detection, namespace map | ~0.5s | 8% — DataFrame filtering |

### Eliminating Python objects in the inner loop

Each speedup tier removes a layer of Python overhead:

| Tier | What changes | Effect |
|------|-------------|--------|
| **Polars** (6.4x) | Replace `itertuples()` with `iter_rows()` | Polars returns raw tuples, not named objects |
| **Cython string** (7.3x) | Read numpy arrays, build C++ strings | No Python string concat, f-string, or join |
| **Cython pugixml** (7.9x) | Build DOM in C++ memory | pugixml serializer is faster than string concat |
| **Arrow pugixml** (11.0x) | Read Arrow buffers with `GetString()` | Zero-copy: pointer arithmetic instead of Python unboxing |

### What didn't help

- **lxml tag/QName cache** (`lru_cache` on `_get_qname()`): Already in the original. Expanding it to a full pre-built dict made things worse because lxml Element creation itself is the bottleneck, not QName lookup.

- **lxml with numpy array access**: Avoiding `itertuples()` helps with data access, but lxml `ElementMaker()(qname)` still creates a Python Element object per call. The Element creation cost outweighs the iteration improvement.

- **Threading** (`ThreadPoolExecutor`): GIL blocks Python-heavy code. All variants show ~0% improvement with 4 threads (some are slightly slower due to contention). True parallelism requires multiprocessing, but DataFrame pickling overhead makes `ProcessPoolExecutor` impractical for this data size.

- **Pure Python string builder with numpy arrays** (1.2x): numpy object arrays still unbox to Python `str` on each access. The string concatenation is fast, but `ids_arr[i]` returning a Python object is not.

### What helped

1. **Polars as data backend** (6.4x): Polars `iter_rows()` returns raw tuples without named-tuple overhead. The combination of fast iteration + Python string building gives excellent results with zero compilation required.

2. **Cython + C++ string building** (7.3x): Moving the string concatenation loop into Cython with `libcpp.string` avoids all Python string allocation. Data is read from numpy object arrays (still Python unboxing) but assembled in C++.

3. **pugixml DOM in C++** (7.9x): Instead of building strings manually, let pugixml's C++ DOM handle element creation and serialization. pugixml's internal memory pool is more efficient than `std::string::append()` for tree-structured output.

4. **Arrow zero-copy reads** (11.0x): The biggest single improvement over cython_pugixml (+40%). Instead of numpy object arrays that unbox to Python `str` on each `arr[i]`, Arrow `CStringArray::GetString(i)` returns a `std::string` directly in C++ via pointer arithmetic into contiguous buffers. No Python objects touched in the inner loop.

## Architecture of Best Variant (arrow_pugixml, 11.0x)

```
pandas DataFrame (ID, KEY, VALUE columns)
  → Python: pa.Table.from_pandas() + cast(string)
    → Python: RecordBatch (contiguous Arrow buffers)
      → Cython: unwrap Arrow arrays to C++ CStringArray*
        → C++: for i in range(n):
            id  = id_arr->GetString(i)    // pointer into Arrow buffer
            key = key_arr->GetString(i)   // no Python objects
            val = val_arr->GetString(i)   // no allocation
            → pugixml: append_child / append_attribute / set_value
          → pugixml: doc.save(string_writer)  // single-pass serialize
            → Cython: return writer.result as Python bytes
```

The pandas→Arrow conversion is a one-time cost (~50ms for 300K rows). After that, the entire export loop runs in C++ with zero Python object creation.

### Cost breakdown (arrow_pugixml, ~0.5s for 4 instances)

| Step | Approx. | Notes |
|------|---------|-------|
| `from_pandas()` + `cast()` | ~150ms | One-time Arrow conversion |
| Arrow unwrap + C++ loop | ~250ms | GetString() + pugixml DOM build |
| pugixml serialize | ~80ms | Single-pass pretty-print |
| Python wrapper overhead | ~20ms | Profile detection, namespace map |

## Build Requirements

### Cython variants (cython_string, cython_pugixml, arrow_pugixml)
- `pugixml.hpp` + `pugixml.cpp` source files (for pugixml variants)
- `pyarrow` (for Arrow C++ headers + shared libs, arrow_pugixml only)
- `Cython`
- Build: `python setup_cython_export.py build_ext --inplace`

### Polars variant (no build required)
- `pip install polars`

## Files

| File | Description |
|------|-------------|
| `triplets/cimxml_export_arrow_pugixml.pyx` | Best variant: Arrow read → pugixml DOM (Cython) |
| `triplets/cimxml_export_arrow_pugixml_wrapper.py` | Python wrapper for arrow_pugixml |
| `triplets/cimxml_export_pugixml.pyx` | pugixml DOM from numpy arrays (Cython) |
| `triplets/cimxml_export_pugixml_wrapper.py` | Python wrapper for pugixml |
| `triplets/cimxml_export_cython.pyx` | C++ string builder from numpy arrays (Cython) |
| `triplets/cimxml_export_cython_wrapper.py` | Python wrapper for cython_string |
| `triplets/cimxml_export_polars.py` | Polars iteration + Python string builder |
| `triplets/cimxml_export_string.py` | numpy arrays + Python string builder |
| `triplets/cimxml_export_lxml_optimized.py` | lxml with tag cache (slower than original) |
| `setup_cython_export.py` | Build script for Cython export extensions |
| `benchmark_export.py` | Export benchmark script |
