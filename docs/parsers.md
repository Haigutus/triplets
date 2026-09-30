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
|-- utils.py                 # find_all_xml, _split_prefixed_name, RDF constants (ID/VALUE shortening: triplets.iri)
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

## The ID / KEY / VALUE contract

Every engine produces the same *short* form; `triplets.iri` is the one
definition of the rules (scalar functions, `iri_pandas` / `iri_polars`
flavors, the cython engine mirrors them in C++ — all held to the case table
in `tests/test_iri.py`).

| column | shape | rule (`triplets.iri`) |
|---|---|---|
| `ID` | bare UUID / bare name | `local_id`: strip exactly **one** of `urn:uuid:`, `#_`, `_` (longest first) |
| `KEY` | `Class.attr` or `Type` | element tag local name (N-Quads / SPARQL: `local_key`, the `split_iri` local name — `dcterms:issued` → `issued`) |
| `VALUE` (Type) | `Breaker` | tag local name (N-Quads / SPARQL: `local_value(…, "class")`) |
| `VALUE` (reference) | bare UUID or `EnumKind.value` | `local_value`: ID rule, then `http(s)…#frag` → `frag` (`#` only, never `/`) |
| `VALUE` (literal) | text verbatim | — |
| `INSTANCE_ID` | bare UUID | fresh `uuid4()` per parsed file |

The absolute form (N-Quads, SPARQL stores, SHACL reports) is the inverse,
driven by the export schema: `absolute_id`, `absolute_key`, `absolute_value` with the
flat maps (`namespaces`, `value_types`, `datatypes`) built from `rdf_map`; a name the
schema does not declare takes the exporter's `undefined_namespace` (`http://triplets#`;
CIM100 on the SPARQL / validation side). `local_resources=False` is the absolute
form of IDs and resource values: resolved against `xml:base` at parse time.

The schema entry type decides the RDF form: an Attribute (`xsd:anyURI` included)
is a literal written verbatim, checked by `sh:datatype`; an Association or
Enumeration is a real IRI, with IRI-unsafe text percent-encoded
(`a b` → `urn:uuid:a%20b`, `iri.encode_iri`). The N-Quads / SPARQL readers
reverse those escapes (`iri.decode_iri`) and shorten a VALUE IRI that is also a
subject of the same data like its `ID`, so the reference still joins.

### Types: `Type` vs `rdf:type`

RDF/XML can state a type two ways. Only one of them can be written back as the
element name, so the triplet form keeps them apart:

```xml
<cim:Breaker rdf:about="urn:uuid:b1">                         <!-- typed-node shorthand -->
  <rdf:type rdf:resource="http://iec.ch/TC57/CIM100#Switch"/>  <!-- explicit statement -->
</cim:Breaker>
<rdf:Description rdf:about="urn:uuid:d1">                     <!-- no shorthand type -->
  <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Ontology"/>
</rdf:Description>
```

| KEY | meaning | CIM XML export | N-Quads / SPARQL |
|---|---|---|---|
| `Type` (`iri.TYPE_KEY`) | the element name: the one type written as the typed-node shorthand. CIM data has exactly one per object and every CIM tool assumes it (`type_key="Type"`) | the object element (`<cim:Breaker …>`) | `rdf:type <class IRI>` |
| `Type` = `Description` (`iri.DESCRIPTION_TYPE`) | an `rdf:Description` element: no shorthand, not a class | `<rdf:Description …>` | no `rdf:type` |
| `type` | an explicit `rdf:type` child — the local name, like any other property | see *Known limitations* | see *Known limitations* |

- **Reading RDF back** (`read_nquads(type_key=…)`, CONSTRUCT). N-Quads has no
  shorthand: every type is an `rdf:type` triple, so the reader cannot tell the
  element type from an extra one. `type_key` picks the KEY: `"Type"` (default,
  CIM data, one type per object) or e.g. `"type"` (every type as an ordinary
  statement). The tools that select by type take the same `type_key`
  (`type_tableview`, `filter_triplets_by_type`, `filter_triplets_by_value`;
  `export_to_cimxml` calls it `class_KEY`).
- **The local form is for people working on imported data.** A local name drops
  its namespace, so two predicates with one local name become one KEY. Exact,
  collision-free handling is the absolute form (`local_resources=False` on the
  XML side today; an absolute import everywhere later).

### Known limitations

Deviations between the importers and exporters that are known and not planned:

- **Local names can collide: `type` is both `rdf:type` and `dcterms:type`.** An
  explicit `rdf:type` child parses to the KEY `type` (its local name), and every
  shipped schema declares `type` as `dcterms:type` (header). On export such a row is
  written as `dcterms:type` with a schema, or as `<http://triplets#type>` without one
  — not as `rdf:type` — and the CIM XML exporters write no `<rdf:type>` children.
  Only the export is affected; the absolute form is the exact one.

- **CIM XML does not percent-encode.** Both CIM XML engines write `rdf:about` /
  `rdf:resource` text as is (`rdf:resource="urn:uuid:a b"`), while N-Quads and the
  SPARQL stores write `<urn:uuid:a%20b>`. CIM IDs and references are not expected
  to hold spaces or ``<>"{}|^`\``.
- **The CIM XML parsers do not decode `%XX`** (every XML engine):
  `rdf:resource="#_a%20b"` reads as `a%20b`; `read_nquads` returns `a b`.
- **`http(s)` IRIs as CIM XML IDs do not join.** `rdf:about="http://x#L1"` keeps the
  whole IRI as `ID`, `rdf:resource="http://x#L1"` shortens to `L1` (the parser sees
  one file at a time, not every subject). `read_nquads` and CONSTRUCT keep such
  references whole. CIM IDs are `urn:uuid:` / `#_` / `_` forms.
- **Blank nodes are not supported.** `_:b0` reads as the ID `b0` and exports again
  as `<urn:uuid:b0>`, a named node. With `local_resources=False`, `rdf:nodeID`
  labels stay as written.
- **The NamespaceMap `xml_base` row still records the document location** when no
  `xml:base` is declared (a file path or the file name). It is metadata only: the
  absolute form never resolves against it (see `default_base`).
- **`%XX` in reference text reads back decoded.** A reference whose own text holds
  `%20` exports unchanged and reads back with a space. The exported file
  round-trips byte for byte; only the frame value changes.
- **`read_nquads` drops datatypes and language tags** — values keep their lexical
  form (everything in a triplet frame is a string).
- **The undefined namespace depends on the direction.** `export_to_nquads` writes
  undeclared names under `http://triplets#`; `sparql.query` / `validate` load the
  same frame under CIM100. Pass `undefined_namespace=` to align them. See
  [sparql.md](sparql.md) for what SELECT returns.

## Options

`parse()` / `read_RDF` accept (see `triplets/parser/__init__.py`):

- `local_resources` (default `True`) — resource values in local form
  (`iri.local_value`: ID prefix stripped, http(s) IRIs cut to their `#fragment`,
  the CIM instance-data convention). Enumerations are stored as
  `ControlAreaTypeKind.Interchange`; a filter on the full CIM URI will not match.
  `False` is the absolute form: IDs and references are resolved at parse time with
  `iri.resolve_iri` against the document's declared absolute `xml:base`, so
  `rdf:about="#ACLineSegment"` becomes `http://iec.ch/TC57/CIM100#ACLineSegment` and
  nothing downstream needs the base again (e.g. RDFS schema parsing). Without one,
  against `default_base` (`http://triplets#` unless passed) — never the file location,
  which is no identity and differs per machine; **not supported
  by the `cython_pugixml_arrow` engine — it raises `ValueError`**, use a python engine.
- `categorical_columns` (default `("INSTANCE_ID", "KEY")`) — columns to
  dictionary-encode (Arrow) / categorize (pandas) for memory savings; `None`
  disables.

  **groupby footgun**: on an ArrowDtype dictionary column, pandas
  `groupby("INSTANCE_ID")` yields a group for EVERY dictionary value — on a
  *filtered* frame that includes empty phantom groups for the filtered-out
  instances, and `observed=True` does not help (it only applies to pandas
  Categorical, not ArrowDtype dictionary). If your pipeline iterates groups,
  either skip empty ones (`if len(frame)`), cast first
  (`df["INSTANCE_ID"].astype(str)`), or parse with
  `categorical_columns=("KEY",)`. triplets' own exports skip empty groups.
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