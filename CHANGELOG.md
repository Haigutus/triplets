# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-29

First packaged release of the restructured library. The codebase was reorganised
into focused modules (`parser`, `tools`, `cgmes_tools`, `export`) with multi-engine
support (pandas, polars, DuckDB) behind a shared `.triplets` accessor namespace.

See [docs/migration_0.0_to_0.1.md](docs/migration_0.0_to_0.1.md) for full upgrade details.

### Added
- **Multiple parser engines** with automatic fallback to the fastest available:
  `python_lxml_pandas` (pure-Python baseline), `python_lxml_arrow`, and the compiled
  `cython_pugixml_arrow` engine (~12x faster, shipped in published wheels).
- **polars support**: `polars.read_rdf(...)` and the `df.triplets.*` accessor namespace.
- **DuckDB support**: `con.read_rdf(...)`, the `con.triplets.*` namespace, and direct SQL
  over the `triplets` table.
- **Accessor namespace** `df.triplets.*` / `con.triplets.*` shared across pandas, polars
  and DuckDB.
- CLI tools `cim-spreadsheet` and `cim-diff`.
- CIM XML export via `export_to_cimxml`, plus CSV, Excel, n-quads and networkx exporters.
- Compiled wheels for CPython 3.11–3.13 on Linux x86_64, macOS arm64 and Windows AMD64.

### Changed
- **Python 3.11+ required** (3.10 dropped).
- `to_networkx()` renamed to `export_to_networkx()`.
- Visualization helpers renamed from `draw_relations_*` to `draw_references_*`.
- With the arrow engines, `KEY`/`INSTANCE_ID` columns are dictionary-encoded and string
  columns use `string[pyarrow]` dtype (~60% less memory). Use the `python_lxml_pandas`
  engine for plain `str` dtypes.
- Triplet values are always strings (or null).
- `export_to_cimxml` exports schema-defined content only by default.

### Deprecated
- All `rdf_parser.py` functions now emit `DeprecationWarning` and delegate to the new
  modules. Several `tools` and `cgmes_tools` functions were renamed; the old names keep
  working with a warning. All of these will be removed in 0.2.

[0.1.0]: https://github.com/Haigutus/triplets/releases/tag/0.1.0
