# Cython Performance Ideas (parser/cython_pugixml_arrow)

Additional performance enhancement ideas for the `cython_pugixml_arrow` engine in `triplets/parser/cython_pugixml_arrow.pyx`, **beyond the existing mmap optimization**.

See also:
- `TARGET_ARCHITECTURE.md` (engine fallback, naming, module map)
- `docs/benchmarks/` (RealGrid numbers, cat vs no-cat, mmap reports)
- `triplets/parser/__init__.py` (the `_finalize_arrow` post-processing that calls `pa.compute.dictionary_encode`)
- Current RealGrid numbers (cat=ON): cython ~0.157s (pandas) / ~0.180s (polars) vs python_lxml ~1.46–1.51s → **~9× speedup**

The implementation is already excellent:
- pugixml C++ with `PARSE_MINIMAL | PARSE_EMBED_PCDATA`
- All ID cleaning (`clean_id`, `clean_ref_value`, `local_name`) in pure C++ (no Python objects per row)
- Direct `CStringBuilder` (C-level Arrow API) for zero-copy string columns
- Mmap + `load_buffer` for str paths (kernel page cache, no extra copy)
- Reusable `std::string` temps, single DOM walk, proper RAII-style cleanup in `finally`

---

## High-Impact Ideas (Prioritized)

### 1. In-builder Dictionary Encoding for KEY + INSTANCE_ID (Highest ROI)
**Problem**: Categorical/dict encoding is done *after the fact* in `parser/__init__.py:_finalize_arrow` (and `_finalize_pandas`):
```python
if categorical_columns:
    for col_name in categorical_columns:
        dict_arr = pa.compute.dictionary_encode(table[col_name])
```
This is a full extra scan + hash after combining batches from all files.

**Idea**: Inside the Cython hot loop (or at RecordBatch creation time), build `KEY` and `INSTANCE_ID` as Arrow `DictionaryArray` / dict-encoded columns directly.

- `KEY` (property names + "Type") has extremely low cardinality (hundreds of uniques).
- `INSTANCE_ID` is repeated verbatim for every row of a file.

**Benefits**:
- Much lower memory *during* accumulation (small indices + dictionary instead of full string copies until the end).
- Eliminates the Python `pa.compute.dictionary_encode` post-pass (and the equivalent `.astype("category")` for pandas path).
- The returned RecordBatch is already "categorical ready".
- Downstream `type_tableview` etc. become faster for free (as seen in existing cat=ON benchmarks).

**Implementation sketch**:
- Use `pyarrow.includes.libarrow` for `CDictionaryBuilder` / `CDictionaryArray` (or implement a tiny string→index map + indices builder + `MakeDictionaryArray`).
- Selective: only for the columns listed in a `categorical_columns` param passed down (default `("INSTANCE_ID", "KEY")`).
- Still fall back to plain utf8 StringBuilder for `ID` and `VALUE` (high cardinality).

**Difficulty**: Medium (Arrow C++ dict APIs from Cython). High value.

### 2. Walk Attributes Only Once Per Node (Easy Win)
Current code (hot path, millions of times on RealGrid):
```cython
raw_id = rdf_object.attribute(b"rdf:ID").value()
if raw_id[0] == 0:
    raw_id = rdf_object.attribute(b"rdf:about").value()
if raw_id[0] == 0:
    raw_id = rdf_object.attribute(b"rdf:nodeID").value()
...
ref_val = element.attribute(b"rdf:resource").value()
if ...:
    ref_val = element.attribute(b"rdf:nodeID").value()
```

**Idea**: Single `attr = rdf_object.first_attribute(); while not attr.empty(): ... attr = attr.next_attribute()`

Same for every child `element`.

This matches the style already used for root namespace extraction (lines 264-281).

**Benefits**: Fewer pugixml calls + memcmps per row. Pure C++, trivial to implement, zero risk.

**Difficulty**: Trivial.

### 3. Release the GIL During Hot C++ Work (`with nogil:`)
The expensive parts (pugixml `load_buffer` + full while loops over rdf_objects/elements + all `Append` + `clean_*` + string ops) can and should run without the GIL.

**Plan**:
- Move core parsing + building logic into a `cdef` nogil function (or `with nogil:` block).
- Keep GIL for: uuid generation (see #4), debug printing/timing, final `pyarrow_wrap_batch(...)`, and any Python object creation for the return.
- Pass the buffer pointer + length + pre-generated instance_id bytes from Python caller.

**Benefits**: When the high-level `parse(..., max_workers=N)` uses `ThreadPoolExecutor` over files (see `triplets/parser/__init__.py:134`), the threads can truly run in parallel instead of contending for the GIL.

Especially valuable for the common "many small XML files inside one ZIP" workload (RealGrid, CGMES packages).

**Difficulty**: Medium (careful with which Arrow C APIs and pugixml calls are safe nogil; the builders take a `CMemoryPool*`).

### 4. Hoist INSTANCE_ID / Meta UUID Generation
`uuid.uuid4()` + `.encode('utf-8')` happens inside every call to `load_rdf_to_dataframe` (for `instance_id`, `meta_id`, `nsmap_id`).

**Idea**: Generate the `instance_id` (and optionally the meta/nsmap ids) once in the Python `parse()` dispatcher (or per logical "batch of files from one source"), and pass the `bytes` down to the Cython function.

The Cython function can accept an optional `instance_id: bytes = None` parameter.

**Benefits**: Removes Python calls from the per-file path. Cleaner separation (the "instance" concept is really a parse-session thing).

**Difficulty**: Easy.

### 5. Pre-Reserve Builder Capacity + Allocation Tuning
Before the main `rdf_object` loop:
```cython
cdef size_t estimated_rows = buf_len // 60   # very rough heuristic
id_b.Reserve(estimated_rows)
# etc. for other builders
```

CStringBuilder (and the underlying Arrow arrays) grow, but explicit `Reserve` reduces realloc/copy traffic on large files.

Also consider:
- Passing a custom `CMemoryPool` if we want to track or limit usage.
- Using `LargeString` / `large_utf8()` for very large documents (rare).

**Difficulty**: Easy.

---

## Other / Medium Ideas

- **More length-aware / string_view paths (C++17)**: Many `strlen` + `assign` sequences. Where lengths are already known (or can be obtained from pugixml), use `std::string_view` temps and the `Append(const char*, size_t)` path on the builders to avoid extra scans/copies.
- **Fewer `child_value()` + attribute double-lookup on elements**: `child_value()` internally walks children for PCDATA. For many elements we can inspect `first_child()` directly or do a small unified walk of attrs + text children.
- **Better debug / timing without Python datetime on every file**: Use `std::chrono` for internal timings when `debug=True`, or make debug a compile-time / no-op path in release builds of the extension.
- **Compilation flags** (in `setup.py` / `setup_cython_parser.py` and pixi build env):
  - Local/dev: `-march=native -mtune=native -flto`
  - Keep conservative flags for cibuildwheel manylinux/macos wheels.
- **Input handling improvements (complements mmap)**:
  - Detect `BytesIO` / already-memory-mapped fileobjs and avoid the `read()` copy.
  - For the ZIP case (very common): the cost is often in Python's `zipfile` + `BytesIO` creation *before* we reach the cython function (see `parser/utils.py:find_all_xml`). A C++ zip path (miniz) would be a bigger lift.

---

## Harder / Usually Not Worth It (for Now)

- Full streaming/SAX parse that never builds the pugixml DOM tree at all. (pugixml is a very lightweight DOM parser. A pure push parser would reduce peak memory on enormous single files by feeding the four builders directly. Would require either a different XML lib or deep use of pugixml's lower-level parse APIs. Probably overkill.)
- In-C++ ZIP extraction for inner files so they can also benefit from mmap-like behavior. Adds a non-trivial native dependency.
- Intra-file parallelism (chunked XML). XML is inherently sequential and the parent ID → child relationship makes independent chunks painful.

---

## Suggested Immediate Experiments / Next Steps

1. Implement #2 (single attribute walk) + #4 (hoisted instance id) + #5 (Reserve) — quick wins, easy to measure.
2. Prototype #3 (nogil) on the main walk + builders. Measure RealGrid with `max_workers=4` or `8` before/after.
3. Tackle #1 (in-builder dict encoding). This is the biggest remaining "post-processing" cost when `categorical_columns` is enabled (the default).
4. Add a small internal timing block (under `if debug:`) that prints "XML parse: X ms, Extraction: Y ms, Arrow finalize: Z ms" using C++ chrono so we can see where time actually goes without Python overhead.
5. Run `cython -a cython_pugixml_arrow.pyx` and inspect the generated HTML to find any remaining yellow (Python-interacting) lines inside the hot loops.
6. Use Linux `perf record -g --call-graph dwarf` + `perf report` (or hotspot) on a cython parse of a large file to find CPU hotspots in the `.so`.

---

## References / Related Code

- `triplets/parser/cython_pugixml_arrow.pyx` (the hot loop, builders, mmap logic, `load_rdf_to_dataframe`)
- `triplets/parser/__init__.py` (`parse`, `get_engine`, `_finalize_arrow` with `dictionary_encode`, ThreadPool)
- `triplets/parser/utils.py` (`find_all_xml` — note the zip handling that produces BytesIOs)
- `setup_cython_parser.py` + `setup.py` (Extension flags, include dirs for pyarrow + pugixml)
- `pixi.toml` (build environment with cython + compilers)
- `documents/parsers_performance*.json` + `realgrid_mmap_benchmark.json` + `memory_and_speed_cat_comparison.txt`
- `tests/test_benchmarks_realgrid.py` (the benchmark harness)

**Status**: Ideas captured from analysis of the current (post-mmap) implementation + benchmark data. Prioritize by impact on the ~9× advantage we already have vs pure Python lxml.

---

*This document lives next to `TARGET_ARCHITECTURE.md` for easy discovery by anyone working on engine performance or the parser module.*
