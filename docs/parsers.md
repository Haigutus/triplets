# Parser Architecture

> **Single source of truth:** edit this file only. The published docs include it
> from `docs/source/guides/parsers.md` via MyST `{include}`.

## Engines

Three parser engines with automatic fallback (fastest available):

| Engine | File | Requires | Speed | Peak Memory (RealGrid) |
|--------|------|----------|-------|------------------------|
| `python_lxml_pandas` | `parser/python_lxml_pandas.py` | lxml + pandas (core) | 1x baseline, **always works** | 314 MB |
| `python_lxml_arrow` | `parser/python_lxml_arrow.py` | + pyarrow (`pip install triplets[arrow]`) | ~1x parse, better interop | 145 MB |
| `cython_pugixml_arrow` | `parser/cython_pugixml_arrow.pyx` | + C++ build + pyarrow | 9.8x | 145 MB |

Fallback order: `cython_pugixml_arrow` -> `python_lxml_arrow` -> `python_lxml_pandas`

All three engines expose the same interface: `load_rdf_to_dataframe(path_or_fileobject, debug=False)`
(the cython engine additionally accepts `string_type`, see below).

Engine aliases: `performance` / `pugixml` -> `cython_pugixml_arrow`, `native` -> `python_lxml_pandas`

## Dialects — `parse(..., dialect=)`

Two parser kinds share the upper abstraction; the `dialect` parameter selects
the registry, `engine="auto"` resolves within it:

| Dialect | Registry kind | Contract |
|---------|--------------|----------|
| `"cimxml"` (default) | `parser_cimxml` | CIM-ergonomic, deliberately lossy: local names, no term metadata. Byte-identical to historic behavior. |
| `"rdfxml"` | `parser_rdfxml` | Fully RDF/XML-compliant capture: same base columns (cleaned identically, familiar to CIM tooling) **plus a `context` struct column** that makes every cleaning reversible. |

`dialect="rdfxml"` accepts `clean_rules=` (per-column ordered prefix tuples,
default = the CIM `clean_ID` chain); cimxml-only params (`string_type`,
`categorical_columns`) raise there, and vice versa.

**Parse = pure capture, zero policy.** The rdfxml parser never warns about or
drops RDF constructs — everything lands in the base columns or the context
struct; interpretation/validation is downstream work. Context fields
(UPPERCASE reconstructs a base column: `PREFIX + column` == full term
byte-exact; lowercase `rdf_*` is term metadata): `ID_PREFIX`, `KEY_PREFIX`,
`VALUE_PREFIX` (`"_:"` marks blank nodes), `rdf_value_kind`
{iri, blank, literal}, `rdf_language`, `rdf_datatype`, `rdf_id_source`
{ID, about, nodeID, minted}, `rdf_node_id` (original author nodeID label;
subjects are remapped to minted uuids for collision-free scoping),
`rdf_parse_type`, `rdf_attributes` (JSON catch-all — withdrawn constructs
like `rdf:aboutEach` parse without error and are captured raw), `source_line`.

**Drop-at-boundary contract.** Context is guaranteed on parse output and
honored by context-aware consumers (`export_to_nquads` writes `_:` subjects,
prefix-reconstructed IRIs and `@lang`/`^^datatype` literals from it;
`read_nquads(context=True)` captures it back). Everything else may drop it;
operations a struct column would break drop it deliberately via
`_engine_detect.drop_context` (pandas update/dedup paths, CSV export, cgmes
boundary). No promise that context survives transformations.

Acceptance oracle: reconstructing an `rdflib.Graph` from base columns +
context is isomorphic with rdflib's own parse of the same file
(`tests/test_rdfxml_parser.py`), including nested/blank nodes, containers,
collections, `parseType=Literal` (exclusive-C14N, byte-exact vs rdflib) and
reification. Performance on RealGrid (1.15M rows): ~1.5x the cimxml lxml
engine, ~9.6x faster than `rdflib.Graph.parse`.

Out of scope (true non-capture items only): exact XMLLiteral exclusive-C14N
*equality semantics* (the serialized XML is captured verbatim), semantic
*processing* of withdrawn constructs (captured raw, never interpreted),
additional rdfxml engines (a cython port is a registry drop-in later),
cimxml writers consuming context (nquads only for now), BCP47 *validation*
(tags captured verbatim), source *column* numbers (lxml exposes lines only).

## Streaming — `parse_batches()`

`parse_batches(paths, engine="auto", ...)` returns a `pyarrow.RecordBatchReader`
producing one batch per XML file as the reader is consumed — the dataset is
never materialized in Python. Fixed all-utf8 schema (no dictionary encoding, no
`string_type` — per-file dictionaries would differ and database consumers
re-encode internally); requires an arrow engine (no pandas fallback).
`max_workers` parses up to that many files ahead — a bounded, in-order
prefetch, so memory stays bounded by max_workers+1 batches. This is the ingest path
behind the DuckDB `con.read_rdf(...)` / `append=True`. File discovery goes
through the lazy `iter_all_xml()` generator (zip members are read one at a
time and handles are closed); `find_all_xml()` is its eager list form.

## String layout — `parse(..., string_type=...)`

The Arrow layout of the ID and VALUE columns is selectable: `"utf8"` (32-bit
offsets, the stable default), `"large_utf8"` (64-bit) or `"string_view"`
(polars'/duckdb's native 16-byte view layout, adopted zero-copy by polars;
needs pyarrow >= 16). `"auto"` picks the layout the return_type adopts
zero-copy: `string_view` for polars, `utf8` otherwise. KEY and INSTANCE_ID
stay dictionary-encoded regardless — consumers use the indices.

The cython engine builds the requested layout natively (a layout-selecting
`StringColBuilder` — zero measured cost on the hot loop); `python_lxml_arrow`
gets a single cast on the combined table at finalize. Measured on RealGrid
(1.14M rows): parse→arrow identical across layouts; parse→polars ~2-4% faster
with string_view — the polars import is currently bounded by the
dictionary→Categorical conversion of KEY/INSTANCE_ID (~11 ms/column), not the
plain string columns, so the zero-copy adoption win is small until that
bottleneck moves.

## Call Sequence

```
pd.read_RDF([paths])
|
'-> parser.parse(paths, engine="auto")
    |
    |-> get_engine("auto")
    |   try cython_pugixml_arrow    -> ImportError (not compiled)
    |   try python_lxml_arrow       -> ImportError (no pyarrow)
    |   fall back python_lxml_pandas -> always works
    |
    |-> find_all_xml(paths)
    |   |-> open .xml/.rdf files
    |   |-> extract from .zip (nested zips supported)
    |   '-> returns [file_obj, file_obj, ...]
    |
    |-> for each xml:
    |   '-> engine.load_rdf_to_dataframe(xml)
    |       |
    |       |  python_lxml_pandas          python_lxml_arrow          cython_pugixml_arrow
    |       |  -----------------           -----------------          --------------------
    |       |  etree.parse(xml)            etree.parse(xml)           mmap(xml) or read bytes
    |       |  iterate lxml tree           iterate lxml tree          pugixml C++ parse
    |       |  build Python list           Arrow StringBuilders       Arrow C++ builders
    |       |  pd.DataFrame(tuples)        pa.RecordBatch             pa.RecordBatch
    |       |        |                           |                          |
    |       |        v                           v                          v
    |       |   pd.DataFrame              pa.RecordBatch              pa.RecordBatch
    |       |
    |       '-> returns result
    |
    |-> combine:
    |   |-> pandas engine:  pd.concat(dataframes)
    |   '-> arrow engines:  pa.Table.from_batches(batches)
    |
    |-> categorical encoding:
    |   |-> pandas engine:  df[col].astype("category")
    |   '-> arrow engines:  pa.compute.dictionary_encode(col)
    |
    '-> convert to return_type:
        |-> "pandas"  -> df or table.to_pandas()
        |-> "arrow"   -> pa.Table
        '-> "polars"  -> pl.from_arrow(table)
```

## File Layout

```
triplets/parser/
|-- __init__.py              # parse() dispatcher, get_engine(), find_all_xml re-export
|-- utils.py                 # find_all_xml, clean_ID, _split_prefixed_name, RDF constants
|-- python_lxml_pandas.py    # lxml -> list of tuples -> pd.DataFrame (default)
|-- python_lxml_arrow.py     # lxml -> Arrow StringBuilders -> pa.RecordBatch
'-- cython_pugixml_arrow.pyx # pugixml C++ -> Arrow C++ builders -> pa.RecordBatch
```

## Usage

```python
import pandas
import polars
import triplets

# auto (best available engine)
data = pandas.read_RDF(["grid_EQ.xml", "data.zip"])

# explicit engine selection
data = pandas.read_RDF(path, engine="python_lxml_pandas")
data = pandas.read_RDF(path, engine="python_lxml_arrow")
data = pandas.read_RDF(path, engine="cython_pugixml_arrow")

# polars (return_type defaults to "polars")
data = polars.read_rdf(["grid_EQ.xml"])

# return Arrow or Polars directly
table = triplets.parser.parse(path, return_type="arrow")
data = triplets.parser.parse(path, return_type="polars")
```

## Options

`parse()` / `read_RDF` accept (see `triplets/parser/__init__.py`):

- `shorten_resources` (default `True`) — shorten http(s) resource values to
  their `#fragment` (CIM instance-data convention). `False` keeps lossless
  full URIs (e.g. for RDFS schema parsing); **not supported by the
  `cython_pugixml_arrow` engine — it raises `ValueError`**, use a python engine.
- `categorical_columns` (default `("INSTANCE_ID", "KEY")`) — columns to
  dictionary-encode (Arrow) / categorize (pandas) for memory savings; `None`
  disables.
- `max_workers` (default `None`) — when set and more than one XML file is
  found, files are parsed concurrently on a `ThreadPoolExecutor`.

## Debug Output

Debug output (file discovery, per-file parse timings, engine selection) follows the
Python logging level — no `debug=True` needed:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

data = pandas.read_RDF(["grid_EQ.xml"])  # debug output because logger is at DEBUG
```

Engine selection is logged at DEBUG level:

```
DEBUG triplets.parser: auto - test engine availability: cython_pugixml_arrow
DEBUG triplets.parser.cython_pugixml_arrow: [grid_EQ.xml] XML parse: 0:00:00.052368
```

## Building the Cython Engine

```shell
pixi install -e build
pixi run build-cython-pugixml-arrow
```

Or manually:

```shell
python setup_cython_parser.py build_ext --inplace
```

## Naming Convention

Engine files follow `{runtime}_{lib}_{output}`:

- **runtime**: `python` (pure Python) or `cython` (compiled)
- **lib**: XML library used (`lxml`, `pugixml`)
- **output**: what it produces (`pandas` DataFrame or `arrow` RecordBatch)