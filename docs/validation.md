# SHACL Validation Architecture

## Status & known limitations (alpha)

The module is new — APIs may still shift. Know these before relying on it:

- **Vectorized engines walk `sh:targetClass` and `sh:targetSubjectsOf`.**
  Shapes reached solely through `sh:targetNode` / `sh:targetObjectsOf` /
  `sh:target` (or using `sh:xone`) are invisible to polars/pandas/duckdb —
  `compile()` logs a warning naming them; use `engine="pyshacl"` for full
  spec coverage.
- **Property paths**: direct, `sh:inversePath`, and the two-step sequence
  `sh:path ( assoc rdf:type )` (the ENTSO-E "valueType" pattern — the
  constraint applies to the referenced object's type; a dangling reference
  yields no value node per SHACL path semantics). Longer sequences and the
  `*OrMorePath` forms are skipped with a compile warning; `engine="pyshacl"`
  covers them.
- **Lexical datatype checks**: with `lexical=True` (the default) datatype
  checks judge the raw *lexical form* of values — see the dedicated section
  below. `engine="reference"` always gives the pure pyshacl view.
- `sh:nodeKind` BlankNode(+combo) cases are intentionally not implemented in
  the vectorized engines (triplets data has no blank nodes).
- The duckdb engine streams/spills by design, but a true larger-than-RAM
  validation has not been exercised yet.

## Engines

Registry dispatch (mirroring the parser), so compiled engines plug in without
touching the public API:

| Engine | File | Requires | Role |
|--------|------|----------|------|
| `pyshacl` | `validation/shacl_pyshacl.py` | pyshacl + rdflib (`pip install triplets[validation]`) | **reference** — spec-complete, rdflib-based. `store="oxigraph"` loads the data graph through the oxigraph SPARQL engine's cached store (identical results; opt in only when the store is already loaded for SPARQL) |
| `pandas` | `validation/shacl_pandas.py` | core (+`sparql` extra for sh:sparql rules) | compiled-IR executor for debugging; **complete registry** — `sh:sparql` delegated to `triplets.sparql` (`max_workers` parallelizes those queries), `sh:node` expanded at compile time and run against the referenced value nodes, `sh:nodeKind` decided by the rdf_map schema (value form when schema is silent). Explicit `engine="pandas"` |
| `polars` | `validation/shacl_polars.py` | polars | compiled-IR executor for performance: one LazyFrame plan per constraint, single `polars.collect_all` (parallel, common subplans eliminated). Same semantics as pandas; nested/query components delegate to the pandas implementations. The fast in-memory default |
| `duckdb` | `validation/shacl_duckdb.py` | duckdb | compiled-IR executor for **larger-than-memory** data: one SQL query per constraint against the connection's triplets table (streams/spills via DuckDB's executor). Defaults come from the connection (`duckdb.connect(table=..., schema=...)`); call kwargs `table`/`schema`/`table_name` override. Accepts a connection or registers any frame. Constraints batch 100-per-`UNION ALL` statement — explicit choice (`engine="duckdb"`, not in auto) when the data does not fit in memory |

Auto order: `polars → pandas → pyshacl` (first importable).
`engine="reference"` always gives the pure pyshacl view. Custom
engines via `triplets.validation.register_engine(name, module)`.

## Compile Once (`shacl_ir.py`)

Shapes are parsed by rdflib **exactly once** into `CompiledShapes`, cached by
content hash of the shape sources:

```
shapes.ttl ──rdflib──► CompiledShapes
                        ├── graph  (rdflib shapes graph — what pyshacl consumes)
                        ├── ir     (flat constraint table — what the vectorized engines consume)
                        ├── hash   (content hash, the cache key)
                        └── plans  {engine name → compiled artifact, filled lazily}
```

The IR is a pandas DataFrame, one row per shape × target × constraint component.
`compiled.ir` is inspectable (see `examples/shacl_validation.py`); the engine
registries that consume it are internal.

| field | type | meaning |
|-------|------|---------|
| `shape_id` | str | full shape IRI (blank-node id for anonymous property shapes) — becomes `SOURCE_SHAPE` in reports and the join key for `enrich` |
| `target_class` | str | local name: the class for `target_kind="class"`, the property KEY for `"subjectsOf"` |
| `target_kind` | `"class"` / `"subjectsOf"` | which target declaration produced the row |
| `path` | str \| None | the property as one triplet KEY (local name); None for node-level constraints (`sh:closed`, node-level `sh:sparql`) |
| `inverse` | bool | `sh:inversePath` — engines swap the FOCUS/VALUE direction |
| `via_type` | bool | the `( assoc rdf:type )` sequence path: the value nodes are the referenced objects' *types* |
| `component` | str | the dispatch key (`"sh:minCount"`, …) — see the matrix below |
| `params` | object | component parameter, shape varies (next table) |
| `severity` | str | local name of `sh:severity`, default `"Violation"` applied at compile time |
| `message` | str \| None | `sh:message`; engines substitute their own default text when None |
| `name`, `description` | str \| None | `sh:name`/`sh:description` with parent-NodeShape inheritance — read only by `context.enrich`, never by engines |

`params` per component:

| components | `params` shape |
|------------|----------------|
| `sh:minCount` `sh:maxCount` `sh:minLength` `sh:maxLength` | int |
| `sh:minInclusive` `sh:maxInclusive` `sh:minExclusive` `sh:maxExclusive` | float (integer bounds are coerced) |
| `sh:datatype` (`"xsd:float"`), `sh:class`, `sh:nodeKind`, `sh:pattern`, `sh:hasValue`, `sh:equals` `sh:disjoint` `sh:lessThan` `sh:lessThanOrEquals` (the other path's KEY) | str |
| `sh:in` | list[str] (IRIs shortened to local names) |
| `sh:closed` | list[str] — the **fully resolved** allowed KEY list: `sh:ignoredProperties` + every direct non-inverse `sh:property` path of the shape, resolved at compile time (paths reached through `sh:node` are not included) |
| `sh:or` / `sh:and` | list[list[dict]] — one inner list of nested IR row dicts per alternative |
| `sh:not` | list[dict] — nested IR row dicts |
| `sh:node` | `{"shape": local name, "rows": [nested IR row dicts]}` — the referenced shape expanded at compile time |
| `sh:sparql` | `{"select": SELECT text with `$this`/`$PATH` placeholders, "prefixes": resolved `PREFIX` header, "path": full IRI of the owning `sh:path` or None}` |

Compile-time behaviors worth knowing: a NodeShape with several `sh:targetClass`
(the ENTSO-E profiles do this) emits every row once per target class; shape
reference cycles through `sh:node` / `sh:or` / `sh:and` / `sh:not` are detected,
warned about, and dropped; supported `sh:path` forms are a direct IRI,
`sh:inversePath`, the `( assoc rdf:type )` sequence, and `sh:alternativePath`
only when one member carries a nested inverse (any other path form warns and
skips the property shape — pyshacl covers it). Unknown components are kept and
logged — engines skip what they don't implement.

`CompiledShapes.plans` is where each engine caches its own compiled artifact
(the polars/duckdb `split_rules` partition) — re-validating new data against
the same shapes never recompiles anything.

### Component coverage per engine

The shared contract lives in `shacl_ir`: `KNOWN_COMPONENTS` (all 24 keys) and
`FALLBACK_COMPONENTS` (`sh:or/and/not/node/sparql` — the nested/query components
every vectorized engine delegates to the pandas implementations via
`split_rules`). A test (`test_shacl_ir.py::test_component_registries_agree`)
pins the registries together:

| engine | registry | coverage |
|--------|----------|----------|
| pandas | `CONSTRAINT_VALIDATORS` | all 24 (the fallback target) |
| polars | `PLAN_BUILDERS` (+ `BATCH_BUILDERS` fast path) | 19 vectorized + 5 delegated |
| duckdb | `SQL_BUILDERS` | 19 vectorized + 5 delegated |
| pyshacl | consumes `compiled.graph`, not the IR | full spec; report vocabulary mapped back via `shacl_report._COMPONENT_MAP` |

The compile cache participates in the shared engine-state lifecycle:
`triplets.clear_caches()` drops cached `CompiledShapes` (with their plans)
together with the SPARQL engines' loaded state, and
`with triplets.cache_scope():` bounds the state created inside the block —
see [sparql.md](sparql.md).

```python
compiled = triplets.validation.compile(["equipment.ttl", "topology.ttl"])
triplets.validation.validate(data_a, compiled)
triplets.validation.validate(data_b, compiled)   # shapes parsed once, plans reused
```

## Engine Contract

Every engine module implements:

```
validate(data, compiled: CompiledShapes, rdf_map=None, scope=None, **kwargs) → violations DataFrame
```

- **pyshacl** consumes `compiled.graph` (data goes through `_rdflib_loader`).
- **pandas/polars/duckdb** consume `compiled.ir` — they never touch rdflib and
  read the raw string `VALUE`s directly (`rdf_map` matters only for their
  sh:sparql delegation, where it types the queried graph).
- **sh:sparql IR rows**: pyshacl evaluates them natively (`advanced=True`).
  The vectorized engines delegate them to `triplets.sparql` (auto order:
  qlever when built, else oxigraph when installed, else rdflib): the data is
  loaded into one dataset, each constraint runs as a single SELECT with the
  focus nodes bound via `VALUES ?this {...}` and `$PATH` substituted from
  the IR, and `max_workers=N` runs the constraint queries in parallel
  processes on the rdflib path only (fork gives copy-on-write sharing of the
  dataset; threads don't help rdflib — it is GIL-bound pure Python; qlever
  and oxigraph are ms-scale sequentially).
  For sh:sparql-heavy profiles build the qlever extension or
  `pip install triplets[oxigraph]`; the rdflib fallback runs in minutes.
  **No query fixing**: constraint queries run exactly as authored. A
  constraint query a strict engine rejects is reported as a
  `triplets:invalidSparql` Warning row naming the shape and is still
  evaluated on the lenient rdflib engine.

## The Lexical-Form Datatype Deviation

The one deliberate divergence from pyshacl. rdflib compares a literal's
*declared* datatype, so `"1"^^xsd:float` is simply valid — the vectorized
engines see the raw lexical form and can judge it. Two levels:

| Finding | Example (declared `xsd:float`) | `VIOLATION_TYPE` | `SEVERITY` |
|---------|-------------------------------|------------------|------------|
| outside the lexical space | `"abc"` | `sh:datatype` | shape's declared severity |
| valid but narrower/non-canonical form | `"1"` (integer form) | `triplets:lexicalForm` | `Warning` |

`validate(..., lexical=True)` (the default) appends these findings to any
engine's report (duplicates dropped on
`[ID, KEY, VALUE, VIOLATION_TYPE, SOURCE_SHAPE, SEVERITY]`).
Parity tests treat `triplets:lexicalForm` rows as documented extras: engines
must never *lose* a violation pyshacl reports.

`rdf_map` still matters for the pyshacl path: without it every `VALUE` is an
untyped string, so `sh:datatype xsd:float` trips on everything; with it the
N-Quads export attaches real `xsd` types and only genuinely broken values fail.

## Result Structure

`validate` always returns the canonical violations DataFrame — **empty =
conforms** — identical across all engines:

| Column | Meaning |
|--------|---------|
| `ID` | focus node (instance UUID, `urn:uuid:` stripped) |
| `KEY` | property path (CIM short name, e.g. `IdentifiedObject.name`) |
| `VALUE` | offending value |
| `VIOLATION_TYPE` | constraint component (`sh:minCount`, `sh:datatype`, `triplets:lexicalForm`, `triplets:invalidSparql`, ...) |
| `MESSAGE` | message from the shape |
| `SEVERITY` | `Violation` / `Warning` / `Info` |
| `SOURCE_SHAPE` | shape URI that produced the result |

Violations DataFrames export like any other DataFrame (`export_to_csv`, Excel, ...),
as a standard `sh:ValidationReport` (`violations.shacl.to_shacl_report(...)` — the
exact inverse of the pyshacl report mapping in `shacl_report.py`) or as SARIF 2.1.0
(`violations.shacl.to_sarif(...)`, see below). Source positions come from one shared
pass — `violations.shacl.locate(sources=...)` stamps `LOCATION_COLUMNS`
(`SOURCE_URI`, `SOURCE_LINE`, `SOURCE_COLUMN`) onto the frame; both exports run it
automatically when given `sources=`, or reuse the columns when already present.

## Shared Loading (`_rdflib_loader.py`)

SHACL and SPARQL reach rdflib through the same two helpers, so a validation and
a query over the same data see an identical graph:

| Function | Produces |
|----------|----------|
| `load_dataset(data, rdf_map=None, store="memory")` | `rdflib.Dataset` seeing the deduplicated union, one named graph per `INSTANCE_ID` (`urn:uuid:{INSTANCE_ID}`). `store="oxigraph"` backs it with the oxigraph SPARQL engine's cached store via oxrdflib (`"auto"`: oxigraph when installed) |
| `scoped_graph(dataset, scope=None)` | full union when `scope is None`; otherwise a concrete `Graph` holding just the scoped instances' named graphs |

## Call Sequence

```
data.shacl.validate(shapes)
|
'-> validation.validate(data, shapes, engine="auto", lexical=True)
    |
    |-> compile(shapes)                        # cached by content hash
    |   '-> _load_shapes -> parse_ir           # rdflib parses ONCE
    |
    |-> get_engine("auto")                     # polars (first importable)
    |   '-> shacl_polars.validate(data, compiled, rdf_map)
    |       # reads raw VALUEs; lexical findings emitted inline
    |       # (pyshacl path instead: load_dataset -> scoped_graph ->
    |       #  pyshacl.validate(compiled.graph) -> report_to_violations)
    |
    '-> + shacl_pandas datatype/lexical supplement   # ONLY for engine="pyshacl"
```

## File Layout

```
triplets/
|-- _rdflib_loader.py        # shared with sparql: load_dataset(), scoped_graph()
'-- validation/
    |-- __init__.py          # validate() + compile() dispatcher, engine registry
    |-- shacl_ir.py          # shapes -> CompiledShapes (IR compiler, content-hash cache)
    |-- shacl_pyshacl.py     # reference engine: data + compiled.graph -> report
    |-- shacl_pandas.py      # compiled-IR executor (full registry; eager, debugging)
    |-- shacl_polars.py      # compiled-IR executor (lazy plans + collect_all, performance)
    |-- shacl_duckdb.py      # compiled-IR executor (SQL per constraint, larger-than-memory)
    |-- shacl_report.py      # ValidationReport graph -> violations DataFrame
    |-- context.py           # optional enrichment pass (instance/object/shape/schema context)
    |-- locations.py         # violations -> source line/column (the sources= grep pass)
    '-- sarif.py             # violations -> SARIF 2.1.0 (grouped by default)
```

## Usage

```python
import pandas
import triplets
from triplets.export_schema import schemas

data = pandas.read_RDF(["grid.zip"])

# validate against a shapes file (format auto-detected by extension) — empty = conforms
violations = data.shacl.validate("shapes.ttl", rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)

# compile once, validate many
compiled = triplets.validation.compile(["equipment.ttl", "topology.ttl"])
violations = data.shacl.validate(compiled)

# scope restricts the validated graphs; all data stays loaded for reference resolution
one_instance = str(data["INSTANCE_ID"].astype(str).iloc[0])
violations = data.shacl.validate("shapes.ttl", scope=[one_instance])

# engines: auto is polars -> pandas -> pyshacl; works on any input flavor
data.shacl.validate("shapes.ttl", engine="pyshacl")      # or "polars" / "pandas" / "duckdb"
triplets.validation.validate(con, "shapes.ttl")          # DuckDB connection
violations = data.shacl.validate("shapes.ttl", lexical=False)   # pure pyshacl report

# pyshacl on the oxigraph engine's cached store (identical results; opt in
# when the store is already loaded for SPARQL — Memory is faster otherwise)
data.shacl.validate("shapes.ttl", engine="pyshacl", store="oxigraph")

# slower optional context pass — adds instance/file, object type/name,
# shape sh:name/sh:description and schema definition columns
violations = data.shacl.validate(compiled, rdf_map=..., context=True)
violations = violations.shacl.enrich(data=data, shapes=compiled, rdf_map=...)  # same, standalone

# SARIF 2.1.0 for GitHub / SonarQube / any SARIF viewer
violations.shacl.to_sarif(path="report.sarif")

# standard sh:ValidationReport (turtle) for SHACL tooling; sources= adds a
# "Source: file line N column M" message per result (SHACL has no location
# vocabulary — plain-text messages travel everywhere)
violations.shacl.to_shacl_report(path="report.ttl", sources=["grid.zip"])

# the location pass standalone — SOURCE_URI/SOURCE_LINE/SOURCE_COLUMN columns,
# reused by both report exports
violations = violations.shacl.locate(sources=["grid.zip"])
```

pyshacl pass-through options (`inference`, `advanced`, `abort_on_first`,
`store`) are forwarded as keyword arguments. Runnable end-to-end demos live in
`examples/shacl_validation.py` (engine behavior walkthrough) and
`examples/shacl_reports.py` (uv-runnable: performance engines + both report
exports with source locations — `uv run examples/shacl_reports.py`).

## Context enrichment (`validation/context.py`)

`validate(..., context=True)` — or standalone
`enrich(violations, data=, shapes=, rdf_map=)` — appends `ENRICHMENT_COLUMNS`
to the report. Every source is optional; absent sources leave their columns
null, so the output schema is stable:

| Columns | Source | Content |
|---------|--------|---------|
| `INSTANCE_ID`, `INSTANCE_LABEL` | data | instance and its parsed file name (the `Distribution`/`label` meta rows) |
| `OBJECT_TYPE`, `OBJECT_NAME` | data | the focus object's `Type` and `IdentifiedObject.name` |
| `SHAPE_NAME`, `SHAPE_DESCRIPTION` | shapes | `sh:name` / `sh:description` (a property shape inherits the parent NodeShape's) |
| `SCHEMA_DESCRIPTION`, `SCHEMA_MULTIPLICITY` | rdf_map | the violated attribute's schema definition |
| `CLASS_DESCRIPTION` | rdf_map | the object's class description |

Pass the *same* shapes object the validation ran with — anonymous property
shapes get fresh blank-node ids per parse, so a re-parsed graph cannot be
matched. An object present in several instances is attributed to its first
occurrence.

## SARIF 2.1.0 export (`validation/sarif.py`)

`triplets.validation.export_to_sarif(violations, ...)` /
`violations.shacl.to_sarif(...)` — a spec-valid SARIF 2.1.0 log for GitHub,
SonarQube or any SARIF viewer. Passing `data=`/`shapes=`/`rdf_map=` runs the
enrichment pass first (an already-enriched frame is used as-is).

- **Grouped by default** (`group=True`): a model can carry 100k identical
  findings that are really *one* issue — each rule becomes one result with
  `occurrenceCount` and the first-3 + last-3 sample instances (all listed
  when ≤ 6). `group=False` emits one result per violation row.
- Severity maps `Violation/Warning/Info` → `error/warning/note`; rules carry
  the shape's `sh:name`/`sh:description`; `MESSAGE` falls back to a generated
  text (message is mandatory in SARIF).
- RDF objects carry no text coordinates through the triplets frame:
  results always point at the model via `logicalLocations` (`Type/ID` +
  object name). Passing `sources=` (the original CIM/XML files — paths,
  zips or file-likes) runs the shared `locate_violations` pass
  (`validation/locations.py`) and adds `physicalLocation.region.startLine`
  / `startColumn` on the violated property element (or the object
  definition) — what GitHub code scanning needs to annotate lines. One
  grep-style pass per file; the parse/validate hot paths are untouched.
  A frame already carrying `LOCATION_COLUMNS` (from
  `violations.shacl.locate(sources=...)`) is used as-is. Without either,
  `artifactLocation.uri` falls back to the enrichment-traced file label,
  region-less. Columns are 1-based byte columns.
- Everything domain-specific (triplet coordinates, schema descriptions,
  sample IDs) rides in the `properties` bags.

## Naming Convention

Engine files follow `{purpose}_{engine}.py`, mirroring the parser and export
modules. Shared loading lives in `_rdflib_loader.py`; the IR compiler in
`shacl_ir.py`; the report normalizer in `shacl_report.py`.
