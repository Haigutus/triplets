# SPARQL Architecture

## Engines

Registry dispatch (mirroring the parser), auto = first importable:

| Engine | File | Requires | Role |
|--------|------|----------|------|
| `qlever` | `sparql/sparql_qlever.py` | compiled extension (`pixi run -e qlever build-qlever`) | **performance** — embedded C++ engine, in-process, no server; auto-preferred when built |
| `rdflib` | `sparql/sparql_rdflib.py` | rdflib (`pip install triplets[sparql]`) | **reference** — built-in SPARQL 1.1, always available with the extra |

Engine aliases: `reference` → `rdflib`, `performance` → `qlever`.

No oxigraph engine: the native tooling is C/C++/Cython and qlever is the chosen
performance path (benchmarked 3.5–216x faster than oxigraph on CGMES data;
index build ~2.3 s per 892k triples, index load from disk ~4 ms).

## The qlever C++ Boundary

qlever natively ships a server (SPARQL-over-HTTP + UI); we use the **engine**.
The boundary is qlever's official embedding facade — `src/libqlever`
(`qlever::Qlever`, "use QLever as an embedded database, without the HTTP
server"), which upstream maintains and CI-tests. Everything crosses it as
strings: SPARQL text in, spec-stable serializations out (SPARQL 1.1 JSON for
SELECT/ASK, Turtle for CONSTRUCT/DESCRIBE). qlever's internal API churn is
absorbed upstream by the facade; what remains is absorbed by a ~50-line shim:

```
triplets/sparql/
|-- _qlever_wrapper.h/.cpp   # ~50-line shim: build_index(), query(text, media_type)
|-- _qlever.pyx              # dumb Cython binding (strings across, GIL released)
'-- sparql_qlever.py         # engine: index cache, result shaping, scope
```

Build (one-time; the pixi `qlever` environment pins the whole toolchain —
compilers, cmake, boost, icu, openssl, zstd, jemalloc — via pixi.lock):

```bash
git clone --recursive https://github.com/ad-freiburg/qlever ../qlever
pixi run -e qlever build-qlever-lib   # compile qlever with PIC (long, once)
pixi run -e qlever build-qlever       # build triplets.sparql._qlever
```

Without the extension nothing changes — auto falls back to rdflib.

**Index lifecycle.** The engine owns it internally: the data is exported to
N-Quads, content-hashed, and indexed on disk under the temp dir keyed by that
hash — re-querying the same data (or re-running a validation) loads the index
in milliseconds instead of rebuilding. Loaded engines are additionally cached
in-process. `scope` filters the data before export (each scope = its own
index). Custom engines register via `triplets.sparql.register_engine(name, module)`.

**Parallelism.** rdflib query evaluation is GIL-bound pure Python, so threads
don't help — batch workloads (the sh:sparql constraints the SHACL engines
delegate here) use `ProcessPoolExecutor` fork on the rdflib path. The qlever
binding releases the GIL during queries, so plain threads parallelize; the
SHACL engines skip the fork pool automatically when qlever is the auto engine
(it is orders of magnitude faster sequentially anyway).

## Shared Loading (`_rdflib_loader.py`)

SPARQL and SHACL reach rdflib through the same two helpers, so a query and a
validation over the same data see an identical graph:

| Function | Produces |
|----------|----------|
| `load_dataset(data, rdf_map=None)` | `rdflib.Dataset(default_union=True)`, one named graph per `INSTANCE_ID` (`urn:uuid:{INSTANCE_ID}`) |
| `scoped_graph(dataset, scope=None)` | full union when `scope is None`; otherwise a concrete `Graph` holding just the scoped instances' named graphs |

`rdf_map` is optional. Without it, every `VALUE` is an untyped string literal
(`xsd:string`); with it, the N-Quads export attaches `xsd` datatypes, so numeric
and date literals carry their real type into the query result.

## Call Sequence

```
data.sparql.query(q)
|
'-> sparql.query(data, q, engine="auto")
    |
    |-> get_engine("auto")
    |   try rdflib -> always works with the extra
    |
    |-> load_dataset(data, rdf_map)
    |   '-> export_to_nquads(..., export_to_memory=True)
    |       '-> rdflib.Dataset.parse(nquads)   # INSTANCE_ID -> named graph
    |
    |-> scoped_graph(dataset, scope)           # union, or just scoped instances
    |
    '-> graph.query(q)                         # shape the result by query type
        |-> ASK                -> bool
        |-> SELECT             -> DataFrame (columns = projected vars)
        '-> CONSTRUCT/DESCRIBE -> triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID]
```

## Result Structure

The result is shaped by the SPARQL query form:

| Query form | Returns | Structure |
|------------|---------|-----------|
| `SELECT` | `pandas.DataFrame` | one column per projected variable (`?name` -> column `name`); IRIs as full strings, literals python-typed via `rdf_map` |
| `ASK` | `bool` | `True` / `False` |
| `CONSTRUCT` / `DESCRIBE` | triplet `DataFrame` | `[ID, KEY, VALUE, INSTANCE_ID]`; `urn:uuid:` stripped from `ID`, CIM namespace shortened on `KEY`, `rdf:type` -> `Type`, `INSTANCE_ID` is `None` (constructed graph has no source instance) |

`SELECT` keeps full IRIs (raw bindings); `CONSTRUCT`/`DESCRIBE` apply the triplets
naming conventions so the output drops straight back into the pipeline.

## File Layout

```
triplets/
|-- _rdflib_loader.py        # shared with validation: load_dataset(), scoped_graph()
'-- sparql/
    |-- __init__.py          # query() dispatcher + engine registry
    '-- sparql_rdflib.py     # rdflib engine: result -> DataFrame / bool / triplets
```

## Usage

```python
import pandas
import triplets
from triplets.export_schema import schemas

data = pandas.read_RDF(["grid.zip"])

PREFIXES = """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX cim: <http://iec.ch/TC57/CIM100#>
"""

# SELECT -> DataFrame (columns = projected variables)
lines = data.sparql.query(PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name }")

# ASK -> bool
has_substation = data.sparql.query(PREFIXES + "ASK { ?s rdf:type cim:Substation }")

# CONSTRUCT -> triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID]
constructed = data.sparql.query(
    PREFIXES + "CONSTRUCT { ?s rdf:type cim:ACLineSegment } WHERE { ?s rdf:type cim:ACLineSegment }")

# rdf_map types the literals — numeric values come back as python floats
typed = data.sparql.query(
    PREFIXES + "SELECT ?l WHERE { ?s cim:Conductor.length ?l }",
    rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)

# scope restricts the queried graphs; all data stays loaded for reference resolution
one_instance = str(data["INSTANCE_ID"].astype(str).iloc[0])
scoped = data.sparql.query(PREFIXES + "SELECT ?s WHERE { ?s rdf:type cim:ACLineSegment }", scope=[one_instance])

# explicit engine selection, and use on any input (e.g. a DuckDB connection)
data.sparql.query(q, engine="rdflib")
triplets.sparql.query(con, q)
```

A runnable end-to-end demo lives in `examples/sparql_query.py`.

## Naming Convention

Engine files follow `{purpose}_{engine}.py`, mirroring the parser and export modules:

- **purpose**: what is produced (`sparql`)
- **engine**: what does the work (`rdflib`)

Shared loading lives in `_rdflib_loader.py`.
