# Development Notes

Engine-implementation and contributor notes live here — the design rules a new
engine or a change to an existing one must respect. User-facing behavior is
documented in the reference guides ([validation.md](validation.md),
[sparql.md](sparql.md), ...).

## Engine model

The one rule that decides where automatic engine selection is allowed:

> **Auto-selection of the fastest available engine applies only to operations
> whose result is flavor-independent** (parsed files, exported bytes/XML, query
> results, violation reports). **Frame-in → frame-out operations always run in
> the input object's engine** — never auto-hop pandas → polars (measured: the
> round-trip conversion ≈16 ms/1M rows exceeds typical op savings ≈8 ms).

All engine dispatch goes through **`triplets._registry.EngineRegistry`** — do
not hand-roll module maps or local try-import blocks. Registries are
constructed at import time and probe availability with `find_spec`
(microseconds, imports nothing); the chosen module imports lazily on first
use. An engine module must raise `ImportError` on import when its backend is
missing — that is what makes `"auto"` fall through.

Kind names carry a role prefix when the subsystem is format-specific
(`parser_`/`exporter_`), so "cimxml" is never ambiguous between the parser
and the exporter:

| Kind | Policy | Auto order | Availability probes (`requires`) |
|------|--------|-----------|----------------------------------|
| parser_cimxml | auto | cython_pugixml_arrow → python_lxml_arrow → python_lxml_pandas | compiled ext, pyarrow |
| sparql | auto | qlever → oxigraph → rdflib | `._qlever`+pyarrow, pyoxigraph, rdflib |
| validation | auto | polars → pandas → pyshacl (duckdb explicit-only) | polars, duckdb, pyshacl+rdflib |
| exporter_nquads | auto | polars → pandas | polars |
| exporter_cimxml | auto | cython_pugixml → python_lxml | compiled ext, pyarrow |
| exporter_csv | **input** | — (engine = input flavor) | polars |
| tools | **input** | — (engine = input flavor) | polars, duckdb |

`policy="input"` marks the frame-bound subsystems: csv exists as two engines
because each is fastest for its own input flavor, and tools operates on the
caller's frame — neither may be steered by a global override, and their
`engines()` row reports `engine: None, source: "input"`.

`cgmes_tools` is deliberately NOT a registry: its data functions run natively
for pandas and polars input, while duckdb/arrow input crosses the pandas
boundary (`to_pandas(plain=True)` → pandas engine → `match_flavor` back) —
the engines mutate VALUE in place, which needs a plain materialized frame.
That is a design decision, not an unfinished migration.

User-facing controls: `triplets.engines()` reports what `"auto"` resolved to
per subsystem (plus available alternatives); `triplets.set_engine(parser_cimxml=...,
sparql=..., ...)` overrides it globally (loads eagerly, fails fast).
Precedence: per-call `engine=` > `set_engine()` > auto probe order.
`set_engine` is process-global startup configuration, not a per-thread
control — concurrent code passes `engine=` per call. Per-call capability
constraints still apply regardless of overrides (cimxml `datatypes=True`
requires python_lxml; validation keeps duckdb out of auto because it is the
explicit larger-than-memory choice).

## Flavor conversion

Triplet data arrives as pandas, polars, pyarrow, or a DuckDB connection. Convert
at subsystem boundaries with **`triplets._engine_detect`** — do not add local
`if polars / if duckdb` materialization blocks:

| Helper | Role |
|--------|------|
| `flavor(data)` | `"pandas"` / `"polars"` / `"pyarrow"` / `"duckdb"` |
| `to_pandas(data, plain=False, …)` | any → pandas (`plain=True` for mutation-safe cgmes frames) |
| `to_arrow(data, columns=…, …)` | any → pyarrow (DuckDB uses native arrow, no pandas) |
| `to_polars(data, …)` | any → polars (Arrow path for duckdb/pyarrow) |
| `as_frame(data)` | pandas/polars unchanged; arrow/duckdb → pandas |
| `match_flavor(result, template)` | pandas result → template's flavor (cgmes dispatch) |
| `to_return_type(frame, return_type)` | pandas frame → "pandas"/"polars"/"arrow" (explicit return_type params) |

DuckDB table/schema defaults stay in `tools.duckdb_engine`; converters pass
optional `table` / `schema` / `table_name` through `_resolve_table`.

## Polars Engine Guidance

The lazy validation engine's design rules. Speed always wins over memory:

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
