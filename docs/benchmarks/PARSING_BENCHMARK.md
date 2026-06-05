# RDF/CIM XML Parsing Benchmark Results

**Date**: 2026-03-26
**Dataset**: CGMES v2.4.15 RealGrid Test Configuration (4 XML files from ZIP, 1,146,215 triplet rows, largest file 68 MB)
**System**: Linux 6.19.8, Python 3.14, Polars 1.38, lxml 6.0.1, pyarrow 19.x

## Summary

| Parser | Avg (s) | vs Original | Tech Stack |
|--------|---------|-------------|------------|
| **cython_arrow** | **0.116** | **12.9x** | pugixml C++ + Arrow StringBuilders (Cython) |
| **lxml_cython_arrow** | **0.471** | **3.1x** | lxml DOM + libxml2 C API + Arrow (Cython) |
| rust_arrow | 0.407 | 3.6x | quick-xml + arrow-rs + pyo3-arrow (Rust) |
| rust_polars | 0.434 | 3.4x | Same Rust, Polars output |
| cython_pugixml | 0.487 | 3.0x | pugixml C++ + Python list (Cython) |
| pygixml_tuned | 0.893 | 1.7x | pygixml + PARSE_MINIMAL + load_buffer |
| pygixml | 0.900 | 1.6x | pygixml + load_buffer |
| **lxml_original** | **1.471** | **1.0x** | **lxml + Python loop + pandas (baseline)** |
| lxml_tuned | 1.472 | 1.0x | lxml with all XMLParser flags tuned |
| lxml_polars_clean | 1.507 | 1.0x | lxml + deferred ID clean in Polars |

All variants verified to produce identical output (1,146,183 data rows after filtering metadata).

## Key Findings

### XML Parser Performance (parse only, single 68 MB file)

| Parser | Parse time | Notes |
|--------|-----------|-------|
| pugixml (via Cython) | 47-63 ms | PARSE_MINIMAL + PARSE_EMBED_PCDATA flags |
| lxml/libxml2 | 253 ms | collect_ids=False is the only flag that matters |
| pygixml load_buffer | 56 ms | Bytes in, no decode overhead |
| pygixml load_string | 91 ms | Requires Python str |
| pygixml decode+load | 131 ms | BytesIO → decode → load_string (old path) |

### Where Time Goes (breakdown for lxml original, 1.49s total)

| Step | Time | % |
|------|------|---|
| XML parse (lxml DOM) | ~340 ms | 23% |
| Python loop (iterate + attrib access) | ~380 ms | 25% |
| String ops + list.append | ~90 ms | 6% |
| DataFrame construction | ~330 ms | 22% |
| ZIP extraction + file I/O | ~50 ms | 3% |

### What Didn't Help

- **lxml tuned XMLParser flags** (huge_tree, resolve_entities, remove_pis, etc.): No measurable impact. The original already had the two flags that matter: `collect_ids=False` and `remove_blank_text=True`.
- **Polars deferred ID cleaning**: ID cleaning takes ~37ms for 1M rows — only ~3% of total time. The bottleneck was never string cleaning.
- **Python expat parser**: 1.94s — SAX callback overhead per element kills any C parser advantage.
- **pugixml Python bindings** (not pygixml): 2.46s — method call overhead (`.name()`, `.value()`) worse than pygixml's Cython properties.

### What Helped

1. **Eliminating Python per-element overhead** (Cython/Rust): The single biggest win. Going from Python property access to C-level struct traversal cuts extraction from 380ms to ~75ms.

2. **Arrow direct output** (skip DataFrame construction): Building Arrow StringBuilders in C++ instead of Python list-of-tuples eliminates the ~330ms DataFrame construction step entirely.

3. **pygixml load_buffer()**: For BytesIO data (zip extraction), using `load_buffer(bytes)` instead of `decode('utf-8') + load_string(str)` saves ~75ms per file (2.3x faster on I/O path).

4. **pygixml parse flags**: `PARSE_MINIMAL | PARSE_EMBED_PCDATA` saves ~16% on XML parse time by skipping escape processing, EOL normalization, and CDATA handling.

## Architecture of Best Variant (cython_arrow, 12.9x)

```
ZIP file
  → Python: zipfile.read() → BytesIO (bytes)
    → Cython: pugixml load_buffer(bytes, PARSE_MINIMAL | PARSE_EMBED_PCDATA)
      → Cython: iterate xmlNode*/xmlAttr* at C level
        → C++: Arrow StringBuilder.Append(string) per row
          → C++: RecordBatch.Make(schema, columns)
            → Cython: pyarrow_wrap_batch() → PyArrow RecordBatch (zero-copy)
              → Python: pa.Table.from_batches() → .to_pandas() or pl.from_arrow()
```

No Python objects created during extraction. The only Python↔C transitions are at the entry (file path/bytes) and exit (Arrow RecordBatch wrapper).

## Build Requirements

### Cython variants
- `pugixml.hpp` + `pugixml.cpp` source files (from [pugixml repo](https://github.com/zeux/pugixml))
- `pyarrow` (for Arrow C++ headers + shared libs)
- `Cython`
- Build: `python setup_cython.py build_ext --inplace`

### Rust variant
- Rust toolchain (rustup)
- `maturin` for building Python extension
- Build: `cd rdf_parser_rust && maturin develop --release`

## Files

| File | Description |
|------|-------------|
| `triplets/rdf_extract_cython_arrow.pyx` | Best variant: pugixml + Arrow direct (Cython) |
| `triplets/rdf_extract_lxml_arrow.pyx` | lxml DOM + libxml2 C API + Arrow (Cython) |
| `triplets/rdf_extract_cython.pyx` | pugixml + Python list output (Cython) |
| `triplets/rdf_parser_pygixml_tuned.py` | pygixml with tuned flags + load_buffer |
| `triplets/rdf_parser_pygixml.py` | pygixml default |
| `triplets/rdf_parser_cython_arrow.py` | Python wrapper for cython_arrow |
| `triplets/rdf_parser_lxml_arrow.py` | Python wrapper for lxml_arrow |
| `triplets/rdf_parser_arrow.py` | Python wrapper for Rust parser |
| `rdf_parser_rust/` | Rust maturin project (quick-xml + arrow-rs + pyo3) |
| `setup_cython.py` | Build script for all Cython extensions |
| `benchmark_parsers.py` | Parsing benchmark script |
