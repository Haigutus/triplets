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
- **oxigraph caveats** (details under "Caveats and dead ends" below):
  a multi-instance `scope` yields one solution per instance for shared
  triples (`DISTINCT` dedupes); the CSV SELECT decode nulls empty-string
  literals; its parser accepts some queries qlever rejects (bare `HAVING`).
- Engine state caches (indexes, stores, datasets) are unbounded by design —
  long-lived processes over many distinct datasets grow memory/disk.

## Engines

Registry dispatch (mirroring the parser), auto = first importable:

| Engine | File | Requires | Role |
|--------|------|----------|------|
| `qlever` | `sparql/sparql_qlever.py` | compiled extension (`pixi run -e qlever build-qlever`) | **performance** — embedded C++ engine, in-process, no server; auto-preferred when built |
| `oxigraph` | `sparql/sparql_oxigraph.py` | pyoxigraph (`pip install triplets[oxigraph]`) | **portable performance** — embedded Rust engine, plain pip wheel; auto-preferred when qlever is not built |
| `rdflib` | `sparql/sparql_rdflib.py` | rdflib (`pip install triplets[sparql]`) | **reference** — built-in SPARQL 1.1, always available with the extra |

Engine aliases: `reference` → `rdflib`, `performance` → `qlever`.

Role split: qlever is the fastest engine but requires the compiled extension
(no pip wheel); the oxigraph engine gives every pip-only install Rust speed —
~3x faster import and 2–5x faster warm queries than rdflib (measured table
below) — while qlever keeps the overall lead and the auto priority.

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
The boundary is qlever's official embedding facade — `src/libqlever`
(`qlever::Qlever`, "use QLever as an embedded database, without the HTTP
server"), which upstream maintains and CI-tests, plus a small vendored patch
(fork branch `libqlever-parser-injection`, shaped as an upstream PR) that adds
two things the facade lacked: programmatic index building via an injected
`RdfParserBase` factory, and SPARQL-protocol dataset clauses on
`parseAndPlanQuery`. Three boundaries cross it, all zero-copy Arrow for data:

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
  for `rdf_map` interpretation. Index build ~0.62 s per 1.14M rows, with
  ~3 ms of Python-side cost.
- **Arrow out (query results)** — SPARQL text in, decoded Arrow string columns
  out. The C++ shim runs `parseAndPlanQuery`, walks the raw result `IdTable`
  and decodes each id with qlever's own `exportIds::idToStringAndType`
  straight into `arrow::StringBuilder`s (unbound → null), with the GIL
  released; Cython wraps the finished arrays zero-copy (`pyarrow_wrap_array`,
  same pattern as the `cython_pugixml_arrow` parser). No serialize-to-text →
  re-parse round trip: ~2.6 s end-to-end on a 1M-row SELECT (RealGrid), the
  Python side reduced to buffer wrapping.
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
design — datasets per process are few).

Digests are engine-specific (each engine hashes with its native row-hash
primitive for speed: polars ~5 ms, duckdb ~6 ms streaming, pandas ~260 ms per
1.14M rows), so an index is shared across row order, scopes, and repeated
calls within a flavor, not across flavors. The per-query hash is the dominant
cost of small warm queries — `data_unchanged=True` on `query()` asserts the
data object has not been mutated since it was last hashed, reusing the digest
remembered for that exact object (id-keyed with a weakref eviction callback,
so a garbage-collected frame can never leak its digest to a new object at the
same address): a warm ASK drops from ~25–370 ms (hash-bound, size-dependent)
to ~0.4 ms flat. Without the flag the hash always runs; with the flag on a
never-hashed object it runs once and is remembered. In-place mutation after
asserting `data_unchanged` returns stale results — that is the contract.

Cross-*parse* sharing is limited by the parser generating fresh INSTANCE_IDs
per parse (they name the graphs, so they must be in the key — see TODO.md).
pyarrow input is not supported (convert with `polars.from_arrow` first).
Custom engines register via `triplets.sparql.register_engine(name, module)`.

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
| `load_dataset(data, rdf_map=None, store="memory")` | `rdflib.Dataset` seeing the deduplicated union, one named graph per `INSTANCE_ID` (`urn:uuid:{INSTANCE_ID}`). `store="oxigraph"` backs it with the oxigraph engine's cached store via oxrdflib (`"auto"`: oxigraph when installed) |
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
| `"pandas"` | `pandas.DataFrame` (arrow-backed dtypes, zero-copy) |
| `"polars"` | `polars.DataFrame` (`from_arrow`, zero-copy) |
| `"arrow"` | `pyarrow.Table` |

## File Layout

```
triplets/
|-- _content_key.py          # engine-neutral state keying (content_hash + rdf_map + salt,
|                            #   data_unchanged digest memo with weakref eviction)
|-- _rdflib_loader.py        # shared with validation: load_dataset(), scoped_graph()
|-- parser/nquads.py         # read_nquads: N-Quads -> triplets (inverse of the export);
|                            #   terms_to_triplets shared by the engines' CONSTRUCT decode
'-- sparql/
    |-- __init__.py          # query() dispatcher + engine registry
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
    data.sparql.query(q, data_unchanged=True)   # ~0.4 ms overhead instead of the hash
```

A runnable end-to-end demo lives in `examples/sparql_query.py`; the store
backend comparison in `examples/sparql_backend_benchmark.py`.

## Store and engine comparison (measured)

Import (end-to-end from the triplets DataFrame to a queryable store,
including each path's serialization leg), warm queries (best of 3, engine
handle in-process), and export (full dataset out). Svedala IGM, 94 861 rows
/ 17 MB N-Quads; the ×12 column repeats the frame to 1.14M input rows:

| | import 95k | import 1.14M | select+join | group-by-type | 2-hop join | ASK | export (full dump) |
|---|---|---|---|---|---|---|---|
| pyoxigraph `bulk_load` (raw store) | 141 ms | 884 ms | 0.1 ms | 4.1 ms | 21 ms | 0.0 ms | 45 ms (N-Quads text) |
| rdflib Memory (default) | 1.15 s | ~14 s | 2.3 ms | 82 ms | 87 ms | 0.6 ms | — |
| rdflib + Oxigraph store | 1.24 s | 13.8 s | 0.6 ms | 4.7 ms | 39 ms | 0.1 ms | 541 ms (N-Quads text) |
| `oxigraph` engine (incl. union projection) | 412 ms | 2.1 s | 0.5 ms | 4.8 ms | 18 ms | 0.0 ms | 87 ms (DataFrame) |
| **qlever (parallel Arrow)** | 205 ms | **659 ms** | 1.3 ms | **0.2 ms** | **4.9 ms** | 0.4 ms | 154 ms (Arrow table) |

Readings: qlever wins the non-trivial queries (its heavy-aggregation lead is
~10–25x over oxigraph, ~260x over rdflib Memory) and, at scale, the import —
the parallel Arrow ingest skips the text serialize+parse the oxigraph paths
still pay, and produces a *persistent* on-disk index (4 ms reload in a later
process; the oxigraph memory store re-imports every process). The `oxigraph`
engine row is the raw `bulk_load` plus the default-graph union projection
(roughly half its import time) — the price of exact rdflib `default_union`
set semantics without touching query text; it beats rdflib ~3x on import and
2–5x on warm queries as a plain pip wheel. On a real 1.14M-unique-row model
(RealGrid) the oxigraph engine imports in ~4.8 s (2.2 s bulk_load + 2.0 s
projection, store ~1.2 GB RSS at 2.2M quads) and dumps a 1.08M-row SELECT in
~1.1 s (vs qlever's 2.6 s Arrow decode). The rdflib rows are bottlenecked by
rdflib's Python N-Quads parser, not the stores.

Caveats and dead ends, so nobody re-derives them:

- oxigraph's union-of-named-graphs keeps duplicate solutions — one per graph
  holding the triple (rdflib and qlever dedupe). The `oxigraph` engine
  handles this by projecting the deduplicated union into the store's default
  graph at load time; the residual is a **multi-instance `scope`** (a
  SPARQL-protocol dataset union): a triple present in several scoped
  instances yields one solution per instance — `DISTINCT` dedupes. Pinned in
  `tests/test_sparql_oxigraph.py`.
- Projecting via a second `bulk_load(to_graph=DefaultGraph())` is slower
  than the `INSERT WHERE GRAPH` update *and* produces a wrong quad count.
  `lenient=True` on `bulk_load` saves nothing (~1%) — strict validation
  stays.
- oxigraph's parser is *more permissive* than qlever's in places: the
  ENTSO-E bare-`HAVING` shapes that qlever rejects are accepted (implicit
  grouping per spec), so they execute instead of falling back to rdflib.
- The CSV SELECT decode cannot distinguish an unbound variable from an
  empty-string literal — both come back null (the W3C CSV-results tradeoff).
- The oxigraph store can back the *rdflib* dataset too
  (`load_dataset(..., store="oxigraph")` wraps the engine's cached store via
  oxrdflib — one bulk_load serves both; the wrapper uses
  `default_union=False` because the store's default graph *is* the projected
  union, and unioning again would count each triple once per graph). For the
  pyshacl engine this gives identical violations but is **slower** than the
  Memory store — pyshacl force-clones the data graph into rdflib Memory
  (`advanced=True`), and the per-triple clone through the oxrdflib wrapper
  costs more than the Rust N-Quads parse saves (95k warm: 1.8 s vs 1.2 s;
  1.14M load+clone: 40 s vs 33 s). So `shacl_pyshacl` defaults to
  `store="memory"`; the parameter is the explicit opt-in. `sparql_rdflib`
  never uses the oxigraph store (reference purity).
- `pyoxigraph.Store.bulk_extend(quads)` skips the text step but is ~3x
  *slower* end-to-end than the text path (~680 ms of Python `Quad` object
  construction per 95k rows dwarfs the serialization it saves); oxigraph's
  fast programmatic loader (`BulkLoader::load_quads`) is Rust-only and it has
  no Arrow interface.
- rdflib's `Concurrent` store is not usable as a Dataset backend (a legacy
  wrapper exposing only `add`/`remove`/`triples`, not a `Store` subclass).
- At ×12 scale every engine deduplicates the repeated quads, so the ×12
  *import* column measures ingesting 1.14M input rows while queries/export
  operate on 95k unique quads in all engines alike.

## Naming Convention

Engine files follow `{purpose}_{engine}.py`, mirroring the parser and export modules:

- **purpose**: what is produced (`sparql`)
- **engine**: what does the work (`qlever`, `oxigraph`, `rdflib`)

Shared loading lives in `_rdflib_loader.py`.
