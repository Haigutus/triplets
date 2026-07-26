# Development Notes

Engine-implementation and contributor notes live here — the design rules a new
engine or a change to an existing one must respect. User-facing behavior is
documented in the reference guides ([validation.md](validation.md),
[sparql.md](sparql.md), ...).

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
