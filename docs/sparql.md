# SPARQL Architecture

## Engines

One reference engine today, with registry dispatch (mirroring the parser) so a
performance engine plugs in without touching the public API:

| Engine | File | Requires | Role |
|--------|------|----------|------|
| `rdflib` | `sparql/sparql_rdflib.py` | rdflib (`pip install triplets[sparql]`) | reference, built-in SPARQL 1.1, **always available with the extra** |
| `qlever` (future) | — | C++ build | performance option; would take auto priority once added |

Fallback order: auto = first importable engine.

Engine aliases: `reference` -> `rdflib`

No oxigraph engine: the native tooling is C/C++/Cython and qlever is the chosen
performance path (benchmarked 3.5–216x faster than oxigraph on CGMES data).

**Engine lifecycle seam (qlever-ready).** The dispatcher contract is just
`query(data, query_string, rdf_map=None, scope=None, return_type="pandas")`.
Engines with expensive setup own their lifecycle internally: qlever will cache
its index keyed by data content, exactly like the SHACL engines cache compiled
plans in `CompiledShapes.plans` — nothing in the dispatcher changes when it
lands. Custom engines register via `triplets.sparql.register_engine(name, module)`.

**Parallelism.** rdflib query evaluation is GIL-bound pure Python, so threads
don't help. Batch workloads (e.g. the sh:sparql constraints the future SHACL
engines delegate here) use `ProcessPoolExecutor(max_workers=...)` following the
`export_to_cimxml` pattern — fork gives copy-on-write graph sharing on Linux.
qlever handles concurrent queries natively.

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
