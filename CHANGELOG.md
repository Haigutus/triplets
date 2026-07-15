# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **SHACL validation** (`df.shacl.validate(shapes)`, `triplets.validation`):
  shapes compile once into a constraint IR (content-hash cached), executed by
  four engines — `pyshacl` (spec reference), `pandas` (complete constraint
  registry incl. sh:sparql and sh:node), `polars` (lazy plans, the auto
  performance path — real Equipment profiles in ~2 s vs pyshacl minutes) and
  `duckdb` (larger-than-memory, explicit). One deliberate deviation: datatype
  checks judge the raw lexical form (`lexical=True`). See docs/validation.md.
- **SPARQL querying** (`df.sparql.query(q)`, `triplets.sparql`): SELECT/ASK/
  CONSTRUCT over triplet data with three engines — `qlever` (embedded C++,
  local source build), `oxigraph` (embedded Rust, `pip install
  triplets[oxigraph]` — the portable performance path, ~3x faster import and
  2–5x faster warm queries than rdflib) and `rdflib` (reference). One result
  contract across engines: all SELECT values are lexical strings (consumers
  cast) and `return_type` is honored everywhere. Engine state is
  content-hash cached; `scope=` restricts to instances'
  named graphs; `data_unchanged=True` skips re-hashing in hot loops.
  sh:sparql constraints in the SHACL engines ride the same auto engine
  (38.5 s → 74 ms on a constraint-heavy shape). See docs/sparql.md.
- **Engine-state lifecycle** (`triplets.clear_caches()`,
  `triplets.cache_scope()`): the in-process engine caches (rdflib datasets,
  oxigraph stores, qlever index handles, compiled SHACL shapes) never evict on
  their own; long-running processes drop them explicitly, or scope them to a
  `with` block. Only in-memory state is dropped — qlever's on-disk indexes
  stay (reload ~4 ms).
- **SARIF 2.1.0 export** (`violations.shacl.to_sarif()`,
  `triplets.validation.export_to_sarif`): violations → SARIF log for GitHub /
  SonarQube / any SARIF viewer. Grouped by default — one result per rule with
  `occurrenceCount` and first-3/last-3 sample instances (`group=False` for
  one result per violation).
- **Context enrichment** (`validate(..., context=True)`,
  `violations.shacl.enrich(...)`): optional slower pass adding instance/file,
  object type/name, shape name/description (sh:name/sh:description, inherited
  from the node shape) and export-schema definitions (attribute description,
  multiplicity, class description) to the violations report.
- **`read_nquads`** (`triplets.read_nquads`, `pandas.read_nquads`,
  `polars.read_nquads`): N-Quads/N-Triples → triplet DataFrame, the vectorized
  inverse of `export_to_nquads`.
- The pyshacl engine accepts `store="oxigraph"` to load its data graph through
  the oxigraph engine's cached store (identical results; Memory remains the
  measured-faster default — see docs/sparql.md caveats).

### Fixed
- `pathlib.Path` inputs are accepted everywhere: `read_rdf`/`parse` (all
  engines, incl. the compiled Arrow parser — closes #75), `export_to_excel`
  (crashed on `path.endswith`), `export_to_csv`, `export_to_nquads`.
- `export_to_nquads()` without a path no longer crashes — defaults to
  `export.nq` in the working directory.

### Changed
- **Python 3.14 supported**: wheels build for cp314, CI tests it (full suite
  verified incl. the compiled Arrow parser).
- Performance benchmarks are deselected by default (`pytest -m performance`
  runs them); the plain suite runs in minutes.

### Removed
- `build-qlever.yml` CI workflow — the qlever engine is a local source build
  (decision record in TODO.md; build + fork pinning in docs/building.md).

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
