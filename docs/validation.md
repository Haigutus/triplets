# SHACL Validation Architecture

## Engines

Registry dispatch (mirroring the parser), so compiled engines plug in without
touching the public API:

| Engine | File | Requires | Role |
|--------|------|----------|------|
| `pyshacl` | `validation/shacl_pyshacl.py` | pyshacl + rdflib (`pip install triplets[validation]`) | **reference** — spec-complete, rdflib-based |
| `pandas` | `validation/shacl_pandas.py` | core (+`sparql` extra for sh:sparql rules) | compiled-IR executor for debugging; **complete registry** — `sh:sparql` delegated to `triplets.sparql` (`max_workers` parallelizes those queries), `sh:node` expanded at compile time and run against the referenced value nodes, `sh:nodeKind` decided by the rdf_map schema (value form when schema is silent). Explicit `engine="pandas"` |
| `polars` | `validation/shacl_polars.py` | polars | compiled-IR executor for performance: one LazyFrame plan per constraint, single `polars.collect_all` (parallel, common subplans eliminated). Same semantics as pandas; nested/query components delegate to the pandas implementations. Real Equipment profiles on Svedala EQ: **1.9 s vs pandas 22.6 s vs pyshacl minutes** |
| `duckdb` | `validation/shacl_duckdb.py` | duckdb | compiled-IR executor for **larger-than-memory** data: one SQL query per constraint against the `triplets` table (streams/spills via DuckDB's executor). Accepts a connection (`table_name=` selectable) or registers any frame. Slowest vectorized engine in-memory (per-query overhead) — it is the explicit choice (`engine="duckdb"`, not in auto) when the data does not fit |

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
  The vectorized engines delegate them to `triplets.sparql` (whichever engine
  is available — rdflib today, qlever later): the data is loaded into one
  dataset, each constraint runs as a single SELECT with the focus nodes bound
  via `VALUES ?this {...}` and `$PATH` substituted from the IR, and
  `max_workers=N` runs the constraint queries in parallel processes (fork
  gives copy-on-write sharing of the dataset; threads don't help rdflib — it
  is GIL-bound pure Python; qlever handles concurrency natively).
  Real-profile scale (Svedala EQ, 48k triples, Simple+Complex Equipment SHACL
  = 4,857 IR rows of which 148 sh:sparql): vectorized components ~24 s;
  the sparql queries ~3.5 min with `max_workers=8` vs ~25 min sequential —
  always pass `max_workers` for sh:sparql-heavy profiles until qlever lands.

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
| `VIOLATION_TYPE` | constraint component (`sh:minCount`, `sh:datatype`, `triplets:lexicalForm`, ...) |
| `MESSAGE` | message from the shape |
| `SEVERITY` | `Violation` / `Warning` / `Info` |
| `SOURCE_SHAPE` | shape URI that produced the result |

Violations DataFrames export like any other DataFrame (`export_to_csv`, Excel, ...).

## Polars Engine Guidance (phase C)

Recorded now so the lazy engine is built right. Speed always wins over memory:

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
| `load_dataset(data, rdf_map=None)` | `rdflib.Dataset(default_union=True)`, one named graph per `INSTANCE_ID` (`urn:uuid:{INSTANCE_ID}`) |
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
    '-- shacl_report.py      # ValidationReport graph -> violations DataFrame
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

# engines: pyshacl reference / partial pandas; works on any input flavor
data.shacl.validate("shapes.ttl", engine="pyshacl")
triplets.validation.validate(con, "shapes.ttl")          # DuckDB connection
violations = data.shacl.validate("shapes.ttl", lexical=False)   # pure pyshacl report
```

pyshacl pass-through options (`inference`, `advanced`, `abort_on_first`) are
forwarded as keyword arguments. A runnable end-to-end demo lives in
`examples/shacl_validation.py`.

## Naming Convention

Engine files follow `{purpose}_{engine}.py`, mirroring the parser and export
modules. Shared loading lives in `_rdflib_loader.py`; the IR compiler in
`shacl_ir.py`; the report normalizer in `shacl_report.py`.
