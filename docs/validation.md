# SHACL Validation Architecture

## Status & known limitations (alpha)

The module is new — APIs may still shift. Know these before relying on it:

- **Vectorized engines walk `sh:targetClass` and `sh:targetSubjectsOf`.**
  Shapes reached solely through `sh:targetNode` / `sh:targetObjectsOf` /
  `sh:target` (or using `sh:xone`) are invisible to polars/pandas/duckdb —
  `compile()` logs a warning naming them; use `engine="pyshacl"` for full
  spec coverage.
- **Deliberate deviation from pyshacl**: datatype checks judge the raw
  *lexical form* of values (`lexical=True`, the default) — see the dedicated
  section below. `engine="reference"` always gives the pure pyshacl view.
- `sh:nodeKind` BlankNode(+combo) cases are intentionally not implemented in
  the vectorized engines (triplets data has no blank nodes).
- The duckdb engine streams/spills by design, but a true larger-than-RAM
  validation has not been exercised yet.

## Engines

Registry dispatch (mirroring the parser), so compiled engines plug in without
touching the public API:

| Engine | File | Requires | Role |
|--------|------|----------|------|
| `pyshacl` | `validation/shacl_pyshacl.py` | pyshacl + rdflib (`pip install triplets[validation]`) | **reference** — spec-complete, rdflib-based. `store="oxigraph"` loads the data graph through the oxigraph SPARQL engine's cached store (identical results; slower than the default Memory store because pyshacl clones the graph into Memory regardless — opt in only when the store is already loaded for SPARQL) |
| `pandas` | `validation/shacl_pandas.py` | core (+`sparql` extra for sh:sparql rules) | compiled-IR executor for debugging; **complete registry** — `sh:sparql` delegated to `triplets.sparql` (`max_workers` parallelizes those queries), `sh:node` expanded at compile time and run against the referenced value nodes, `sh:nodeKind` decided by the rdf_map schema (value form when schema is silent). Explicit `engine="pandas"` |
| `polars` | `validation/shacl_polars.py` | polars | compiled-IR executor for performance: one LazyFrame plan per constraint, single `polars.collect_all` (parallel, common subplans eliminated). Same semantics as pandas; nested/query components delegate to the pandas implementations. Real Equipment profiles on Svedala EQ: **1.9 s vs pandas 22.6 s vs pyshacl minutes** |
| `duckdb` | `validation/shacl_duckdb.py` | duckdb | compiled-IR executor for **larger-than-memory** data: one SQL query per constraint against the `triplets` table (streams/spills via DuckDB's executor). Accepts a connection (`table_name=` selectable) or registers any frame. Constraints batch 100-per-`UNION ALL` statement (~10 s on the real profiles vs polars 2 s in-memory) — it is the explicit choice (`engine="duckdb"`, not in auto) when the data does not fit |

Auto order: `polars → pandas → pyshacl` (first importable). The vectorized
engines share the deliberate deviations (lexical datatype, schema-driven
nodeKind); `engine="reference"` always gives the pure pyshacl view. Custom
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

The IR is a pandas DataFrame, one row per shape × path × constraint component:

```
shape_id, target_class, path, inverse, component, params, severity, message, name, description
```

`params` holds the component's parameter — a scalar (`sh:minCount`), a list
(`sh:in`, `sh:closed` ignored properties), the SELECT text (`sh:sparql`), or
nested row lists (`sh:or` / `sh:and` / `sh:not`). A NodeShape with several
`sh:targetClass` (the ENTSO-E profiles do this) emits every row once per target
class. Unknown components are kept and logged — engines skip what they don't
implement; pyshacl still covers the full spec.

`CompiledShapes.plans` is where each future engine caches its own compiled
artifact (polars LazyFrame builders, duckdb SQL) — re-validating new data
against the same shapes never recompiles anything.

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
  Backend comparison on a 3-constraint sh:sparql shape over Svedala (95k
  rows, `shacl-sparql-backend` benchmark group in
  `tests/test_sparql_oxigraph.py`): **rdflib ~38.5 s, oxigraph ~74 ms,
  qlever ~72 ms** — the pip-installable oxigraph engine closes ~all of the
  qlever gap for sh:sparql workloads (~520x over rdflib).
  Real-profile scale (Svedala EQ, 48k triples, Simple+Complex Equipment SHACL
  = 4,857 IR rows of which 148 sh:sparql, 50 with focus nodes): with the
  embedded **qlever** engine built, the **complete validation — polars +
  qlever — takes ~0.6 s warm** (~0.4 s constraint queries + ~0.16 s
  vectorized components; the on-disk index is content-hashed and reused, and
  within one validation run the data is hashed **once** — the constraint
  queries after the first assert ``data_unchanged``). Cold (first contact
  with the dataset: parallel Arrow index build + first hash) adds a few
  seconds. The vectorized components run as **batched per-component plans**
  (the rules become a frame joined against the data on KEY + class
  membership, per-rule messages riding as columns), so ~4,300 rules execute
  as a handful of plans. On the rdflib fallback the same constraint queries
  cost ~3.3 min sequential; the **pyshacl reference exceeds 10 minutes** on
  the same profiles — build the qlever extension (or
  `pip install triplets[oxigraph]`) for sh:sparql-heavy profiles.
  **No query fixing**: constraint queries run exactly as authored. When a
  strict engine rejects one (e.g. the ENTSO-E `HAVING`-without-`GROUP BY`
  defect, upstream PR entsoe/application-profiles-library#82 — rejected by
  qlever; oxigraph accepts it as implicit grouping but rejects e.g.
  ungrouped projections), the constraint is still evaluated on the lenient
  rdflib engine and the report carries a `triplets:invalidSparql` Warning
  row naming the shape — broken rules get reported and fixed upstream, not
  auto-patched.

## The Lexical-Form Datatype Deviation

The one deliberate divergence from pyshacl. rdflib compares a literal's
*declared* datatype, so `"1"^^xsd:float` is simply valid — the vectorized
engines see the raw lexical form and can judge it. Two levels:

| Finding | Example (declared `xsd:float`) | `VIOLATION_TYPE` | `SEVERITY` |
|---------|-------------------------------|------------------|------------|
| outside the lexical space | `"abc"` | `sh:datatype` | shape's declared severity |
| valid but narrower/non-canonical form | `"1"` (integer form) | `triplets:lexicalForm` | `Warning` |

`validate(..., lexical=True)` (the default) appends these findings to any
engine's report (duplicates dropped on `[ID, KEY, VALUE, VIOLATION_TYPE]`).
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

Violations DataFrames export like any other DataFrame (`export_to_csv`, Excel, ...).

## Polars Engine Guidance

The lazy engine's design rules. Speed always wins over memory:

- Build **one LazyFrame plan per IR constraint** against a shared `.lazy()`
  base; execute everything with a single `polars.collect_all(plans)` (parallel
  execution + common-subplan elimination). Pre-materialize shared indices once
  (per-Type row index, the set of all IDs) and reuse them across plans.
- Use expressions only (`polars.col`), `Categorical`/`Enum` dtype for KEY and
  Type, `.cast(strict=False)` + null-check for datatype casts,
  `str.contains(literal=True)` when no regex is needed, join-based membership
  for large `sh:in` lists (`is_in` only for small ones).
- Avoid `map_elements`/`map_rows` (Python UDFs serialize execution),
  per-constraint eager `.collect()`, `.to_pandas()` round-trips mid-pipeline,
  `iter_rows`, object dtype, eager `pivot` on large frames.
- No streaming collect — that trades speed for memory, which is the duckdb
  engine's job. Keep the base frame and indices materialized; rechunk once
  after load.

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
    |-> get_engine("auto")                     # pyshacl (reference)
    |   |-> load_dataset(data, rdf_map)        # in-memory N-Quads -> rdflib.Dataset
    |   |-> scoped_graph(dataset, scope)       # union, or just scoped instances
    |   |-> pyshacl.validate(data_graph, shacl_graph=compiled.graph, ...)
    |   '-> report_to_violations(report_graph) # ValidationReport -> DataFrame
    |
    '-> + shacl_pandas datatype/lexical findings (the documented deviation)
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
```

pyshacl pass-through options (`inference`, `advanced`, `abort_on_first`,
`store`) are forwarded as keyword arguments. A runnable end-to-end demo lives
in `examples/shacl_validation.py`.

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
  zips or file-likes) locates the reported instances in the text at export
  time and adds `physicalLocation.region.startLine` on the violated
  property element (or the object definition) — what GitHub code scanning
  needs to annotate lines. One grep-style pass per file over exactly the
  reported IDs (`validation/locations.py`); the parse/validate hot paths
  are untouched. Without `sources=`, `artifactLocation.uri` falls back
  to the enrichment-traced file label, region-less.
- Everything domain-specific (triplet coordinates, schema descriptions,
  sample IDs) rides in the `properties` bags.

## Naming Convention

Engine files follow `{purpose}_{engine}.py`, mirroring the parser and export
modules. Shared loading lives in `_rdflib_loader.py`; the IR compiler in
`shacl_ir.py`; the report normalizer in `shacl_report.py`.
