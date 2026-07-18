# SPARQL Architecture

## Status & known limitations (alpha)

The module is new — APIs may still shift. Know these before relying on it:

- **qlever is a local source build** (no wheel, no CI — see
  [building.md](building.md)); the pip performance path is
  `pip install triplets[oxigraph]`.
- **All SELECT values are lexical strings in every engine** (triplets are
  all-string; consumers cast) — swapping engines never changes result
  dtypes. `rdf_map` still types the *loaded graph* (drives comparisons and
  ORDER BY inside the query), not the returned representation.
- **oxigraph caveats** (details under "Behavioral caveats" below):
  a multi-instance `scope` yields one solution per instance for shared
  triples (`DISTINCT` dedupes); the CSV SELECT decode nulls empty-string
  literals; its parser accepts some queries qlever rejects (bare `HAVING`).
- Engine state caches (indexes, stores, datasets) are unbounded by design —
  long-lived processes over many distinct datasets grow memory/disk. Manage
  the lifecycle explicitly: `triplets.clear_caches()` drops all in-memory
  engine state, `with triplets.cache_scope():` drops the state created
  inside the block on exit.

## Engines

Registry dispatch (mirroring the parser), auto = first importable:

| Engine | File | Requires | Role |
|--------|------|----------|------|
| `qlever` | `sparql/sparql_qlever.py` | compiled extension (`pixi run -e qlever build-qlever`) | **performance** — embedded C++ engine, in-process, no server; auto-preferred when built |
| `oxigraph` | `sparql/sparql_oxigraph.py` | pyoxigraph (`pip install triplets[oxigraph]`) | **portable performance** — embedded Rust engine, plain pip wheel; auto-preferred when qlever is not built |
| `rdflib` | `sparql/sparql_rdflib.py` | rdflib (`pip install triplets[sparql]`) | **reference** — built-in SPARQL 1.1, always available with the extra |

Engine aliases: `reference` → `rdflib`, `performance` → `qlever`.

Role split: qlever is the fastest engine but requires the compiled extension
(no pip wheel); the oxigraph engine gives every pip-only install Rust speed
over rdflib, while qlever keeps the overall lead and the auto priority.

The oxigraph engine keeps loaded data in an in-memory `pyoxigraph.Store`,
fed by `Store.bulk_load` from the N-Quads export and cached by content key
like the other engines. At load time the deduplicated union of the named
graphs is projected into the store's default graph
(`INSERT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } }`), so unscoped queries
match rdflib's `default_union` set semantics — oxigraph's own
union-of-named-graphs keeps one solution per graph (see caveats). SELECT
results decode from oxigraph's SPARQL-CSV serializer (lexical forms — the
same all-strings convention as qlever) straight into pandas/polars;
CONSTRUCT/DESCRIBE results serialize to N-Quads Rust-side and come back
through `triplets.read_nquads` — the same round-trip that loaded the data.

## The qlever C++ Boundary

qlever natively ships a server (SPARQL-over-HTTP + UI); we use the **engine**.
The boundary is qlever's embedding facade — `src/libqlever` (`qlever::Qlever`,
"use QLever as an embedded database, without the HTTP server"), plus a small
vendored patch that adds two things the facade lacked: programmatic index
building via an injected `RdfParserBase` factory, and SPARQL-protocol dataset
clauses on `parseAndPlanQuery`. Three boundaries cross it, all zero-copy Arrow
for data:

- **Arrow in (index build)** — the triplet columns go to the index builder as
  Arrow batches (`pyarrow_unwrap_batch`), consumed by `ArrowTripleParser`
  (`_qlever_arrow_parser.cpp`): an `RdfParserBase` that yields `TurtleTriple`s
  straight from the Arrow buffers (utf8 / large_utf8 / dictionary-encoded,
  offset-aware). No N-Quads serialization, no text re-parsing, and the
  conversion runs in parallel using qlever's own worker/queue machinery
  (the `RdfMultifileParser` pattern: 100k-row ranges on a `TaskQueue`,
  finished `TurtleTriple` batches through a bounded `ThreadSafeQueue`,
  worker exceptions propagated to the consumer with deterministic row
  numbers). The term mapping reproduces the N-Quads export rules exactly —
  typed literals run through qlever's own
  `literalAndDatatypeToTripleComponent` (the very code its N-Quads parser
  calls), and `build_key_metadata` stays the single Python source of truth
  for `rdf_map` interpretation.
- **Arrow out (query results)** — SPARQL text in, decoded Arrow string columns
  out. The C++ shim runs `parseAndPlanQuery`, walks the raw result `IdTable`
  and decodes each id with qlever's own `exportIds::idToStringAndType`
  straight into `arrow::StringBuilder`s (unbound → null), with the GIL
  released; Cython wraps the finished arrays zero-copy (`pyarrow_wrap_array`,
  same pattern as the `cython_pugixml_arrow` parser). No serialize-to-text →
  re-parse round trip — the Python side is reduced to buffer wrapping.
- **strings (ASK + diagnostics)** — SPARQL text in, spec-stable
  serializations out (SPARQL 1.1 JSON / Turtle / CSV).

```
triplets/sparql/
|-- _qlever_wrapper.h/.cpp     # shim: build_index_from_arrow(), query(text, media_type,
|                              #   scope), select_arrow() / construct_arrow() -> Arrow columns
|-- _qlever_arrow_parser.h/.cpp# ArrowTripleParser: Arrow batches -> TurtleTriples
|-- _qlever.pyx                # dumb Cython binding (zero-copy both ways, GIL released)
'-- sparql_qlever.py           # engine: index cache, conventions, output flavor
```

Build (one-time; the pixi `qlever` environment pins the whole toolchain —
compilers, cmake, boost, icu, openssl, zstd, jemalloc — via pixi.lock):

```bash
git clone --recursive https://github.com/ad-freiburg/qlever ../qlever
pixi run -e qlever build-qlever-lib   # compile qlever with PIC (long, once)
pixi run -e qlever build-qlever       # build triplets.sparql._qlever
```

Without the extension nothing changes — auto falls back to oxigraph when
installed, else rdflib. qlever ships in no wheel (source build only, local);
how the fork/branch is pinned and reproduced is documented in
[building.md](building.md).

**Engine state lifecycle.** Flavor-blind by construction — the input (pandas /
polars DataFrame or DuckDB connection) carries every needed capability as a
registered method, so the engine never inspects types: `content_hash`
(row-order-invariant by default; `order_sensitive=True` makes row order part
of the digest) keys the state, and `export_to_arrow` feeds the index build —
which runs **only on a cache miss**, zero-copy. All three engines share the
same lifecycle (`triplets._content_key`, engine-neutral so no engine imports
another's package): the oxigraph engine caches loaded stores and the rdflib
engine loaded datasets in-process the same way, each under its own salt.
`scope` is not a data operation and does not key the state — the
scoped instances' named graphs travel beside each query as SPARQL-protocol
dataset clauses (`default-graph-uri`), so the query text is never modified,
one index serves every scope, and per the protocol the scope takes precedence
over any `FROM` inside the query; rdflib selects the scoped named graphs
after loading. One on-disk index per content key (data + rdf_map) lives under
`$TRIPLETS_QLEVER_DIR` (point it at `/dev/shm` for fully RAM-backed indexes)
or the temp dir; index files are memory-mapped, so hot pages sit in the OS
page cache either way, and loaded engines are cached in-process (unbounded by
design — datasets per process are few). Nothing evicts automatically:
`triplets.clear_caches()` drops every in-memory cache (loaded indexes,
stores, datasets, compiled SHACL shapes), and `with triplets.cache_scope():`
bounds the state created inside the block to it — entries that existed
before survive. Only in-memory state is dropped; qlever's on-disk indexes
stay (delete `$TRIPLETS_QLEVER_DIR` to reclaim disk).

Digests are engine-specific (each engine hashes with its native row-hash
primitive for speed), so an index is shared across row order, scopes, and
repeated calls within a flavor, not across flavors. The per-query hash is the
dominant cost of small warm queries — `data_unchanged=True` on `query()`
asserts the data object has not been mutated since it was last hashed, reusing
the digest remembered for that exact object (id-keyed with a weakref eviction
callback, so a garbage-collected frame can never leak its digest to a new
object at the same address). Without the flag the hash always runs; with the
flag on a never-hashed object it runs once and is remembered. In-place
mutation after asserting `data_unchanged` returns stale results — that is the
contract.

Cross-*parse* sharing is limited by the parser generating fresh INSTANCE_IDs
per parse (they name the graphs, so they must be in the key — see TODO.md).
Every engine accepts bare pyarrow `Table`/`RecordBatch` input: non-registered
input (no `content_hash` method) is routed through `_to_loadable`, which
converts arrow/duckdb to pandas before loading. Custom engines register via
`triplets.sparql.register_engine(name, module)`.

**Parallelism.** rdflib query evaluation is GIL-bound pure Python, so threads
don't help — batch workloads (the sh:sparql constraints the SHACL engines
delegate here) use `ProcessPoolExecutor` fork on the rdflib path. The qlever
binding and pyoxigraph both release the GIL during queries, so plain threads
parallelize; the SHACL engines skip the fork pool automatically whenever the
auto engine is not rdflib (both are orders of magnitude faster sequentially
anyway).

## Shared Loading (`_rdflib_loader.py`)

SPARQL and SHACL reach rdflib through the same two helpers, so a query and a
validation over the same data see an identical graph:

| Function | Produces |
|----------|----------|
| `load_dataset(data, rdf_map=None, data_unchanged=False, store="memory")` | `rdflib.Dataset` seeing the deduplicated union, one named graph per `INSTANCE_ID` (`urn:uuid:{INSTANCE_ID}`). `store="oxigraph"` backs it with the oxigraph engine's cached store via oxrdflib (`"auto"`: oxigraph when installed) |
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
    |-> get_engine("auto")                     # qlever when built, else oxigraph, else rdflib
    |
    |-> qlever path (performance)
    |   |-> _index_for: content_key -> cached index | build (export_to_arrow -> ArrowTripleParser)
    |   |-> scope -> SPARQL-protocol dataset clauses (one index serves every scope)
    |   '-> C++ decode -> Arrow columns (GIL released) -> zero-copy wrap
    |
    |-> oxigraph path (portable performance)
    |   |-> _store_for: content_key -> cached store | bulk_load(export_to_nquads)
    |   |   '-> default graph := deduplicated union of the named graphs
    |   |-> scope -> SPARQL-protocol default graphs (one store serves every scope)
    |   '-> SELECT: SPARQL-CSV -> read_csv | CONSTRUCT: N-Quads -> read_nquads
    |
    |-> rdflib path (reference)
    |   |-> load_dataset(data, rdf_map)        # cached by content_key
    |   |   '-> export_to_nquads(..., export_to_memory=True)
    |   |       '-> rdflib.Dataset.parse(nquads)   # INSTANCE_ID -> named graph
    |   |-> scoped_graph(dataset, scope)       # union, or just scoped instances
    |   '-> graph.query(q)
    |
    '-> result by query form (finalized to return_type)
        |-> ASK                -> bool
        |-> SELECT             -> DataFrame (columns = projected vars)
        '-> CONSTRUCT/DESCRIBE -> triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID]
```

## Result Structure

The result is shaped by the SPARQL query form:

| Query form | Returns | Structure |
|------------|---------|-----------|
| `SELECT` | DataFrame (see `return_type`) | one column per projected variable (`?name` -> column `name`); IRIs as full strings. **All values are lexical strings in every engine** (triplets are all-string; consumers cast), unbound → null (the rdflib/oxigraph CSV decodes also null empty-string literals — the W3C CSV-results tradeoff; qlever distinguishes) |
| `ASK` | `bool` | `True` / `False` |
| `CONSTRUCT` / `DESCRIBE` | triplet DataFrame | `[ID, KEY, VALUE, INSTANCE_ID]`; `urn:uuid:` stripped from `ID`, CIM namespace shortened on `KEY`, `rdf:type` -> `Type`, `INSTANCE_ID` is `None` (constructed graph has no source instance) |

`SELECT` keeps full IRIs (raw bindings); `CONSTRUCT`/`DESCRIBE` apply the triplets
naming conventions so the output drops straight back into the pipeline.

**Output flavor** (`return_type`, honored by every engine):

| `return_type` | Result |
|---------------|--------|
| `"auto"` (default) | matches the input — polars in → polars out; pandas / DuckDB in → pandas out |
| `"pandas"` | `pandas.DataFrame` (qlever: arrow-backed dtypes, zero-copy; oxigraph/rdflib: plain pandas) |
| `"polars"` | `polars.DataFrame` (qlever: `from_arrow`, zero-copy; oxigraph/rdflib: plain polars) |
| `"arrow"` | `pyarrow.Table` |

Only **qlever** returns zero-copy / arrow-backed frames (it decodes the result
`IdTable` straight into Arrow columns). oxigraph decodes its SPARQL-CSV
serializer into a plain pandas/polars frame; rdflib builds the frame row-wise
from the result bindings — neither is Arrow-backed.

## File Layout

```
triplets/
|-- _content_key.py          # engine-neutral state keying (content_hash + rdf_map + salt,
|                            #   data_unchanged digest memo with weakref eviction)
|-- _caches.py               # clear_caches() / cache_scope(): in-process engine-state lifecycle
|-- _rdflib_loader.py        # shared with validation: load_dataset(), scoped_graph()
|-- parser/nquads.py         # read_nquads: N-Quads -> triplets (inverse of the export);
|                            #   terms_to_triplets shared by the engines' CONSTRUCT decode
'-- sparql/
    |-- __init__.py          # query() dispatcher + engine registry
    |-- sparql_qlever.py     # qlever engine (primary/auto): index cache, zero-copy Arrow
    |-- sparql_oxigraph.py   # oxigraph engine: store cache, CSV/N-Quads decode
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

# rdf_map types the literals in the loaded graph (numeric comparisons and
# ORDER BY inside the query work) — returned values stay lexical strings
typed = data.sparql.query(
    PREFIXES + "SELECT ?l WHERE { ?s cim:Conductor.length ?l }",
    rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)

# scope restricts the queried graphs; all data stays loaded for reference resolution
one_instance = str(data["INSTANCE_ID"].astype(str).iloc[0])
scoped = data.sparql.query(PREFIXES + "SELECT ?s WHERE { ?s rdf:type cim:ACLineSegment }", scope=[one_instance])

# explicit engine selection, and use on any input (e.g. a DuckDB connection)
data.sparql.query(q, engine="oxigraph")   # or "qlever" / "rdflib"
triplets.sparql.query(con, q)

# N-Quads round-trip: export and read back (also pandas/polars.read_nquads)
buffer = data.export_to_nquads(export_to_memory=True)
same = triplets.read_nquads(buffer)

# hot loop over unchanging data: skip the per-query content hash
for q in queries:
    data.sparql.query(q, data_unchanged=True)   # reuse the remembered digest, skip the hash
```

A runnable end-to-end demo lives in `examples/sparql_query.py`; the store
backend comparison in `examples/sparql_backend_benchmark.py`.

## Store and engine comparison

For measured import / warm-query / export numbers across the three engines,
run `examples/sparql_backend_benchmark.py`.

## Behavioral caveats

- oxigraph's union-of-named-graphs keeps duplicate solutions — one per graph
  holding the triple (rdflib and qlever dedupe). The `oxigraph` engine
  handles this by projecting the deduplicated union into the store's default
  graph at load time; the residual is a **multi-instance `scope`** (a
  SPARQL-protocol dataset union): a triple present in several scoped
  instances yields one solution per instance — `DISTINCT` dedupes. Pinned in
  `tests/test_sparql_oxigraph.py`.
- oxigraph's parser is *more permissive* than qlever's in places: the
  ENTSO-E bare-`HAVING` shapes that qlever rejects are accepted (implicit
  grouping per spec), so they execute instead of falling back to rdflib.
- The CSV SELECT decode (oxigraph) cannot distinguish an unbound variable
  from an empty-string literal — both come back null (the W3C CSV-results
  tradeoff). qlever distinguishes them.

## Naming Convention

Engine files follow `{purpose}_{engine}.py`, mirroring the parser and export modules:

- **purpose**: what is produced (`sparql`)
- **engine**: what does the work (`qlever`, `oxigraph`, `rdflib`)

Shared loading lives in `_rdflib_loader.py`.
