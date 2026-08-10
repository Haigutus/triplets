# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0rc1] - 2026-08-10

Everything below was developed across the 0.2.0a1/a2 alphas and this
release candidate; 0.2.0rc1 is the first API-frozen cut of the 0.2 line.

### Changed
- **Shared flavor conversion** (`triplets._engine_detect`): `to_pandas` /
  `to_arrow` / `to_polars` / `as_frame` / `match_flavor` are the single choke
  point for materializing pandas/polars/pyarrow/DuckDB inputs (rdflib loader,
  SHACL, SPARQL, cgmes_tools, export, DuckDB export wrappers). Prefer these
  over local if-flavor blocks.
- **One engine registry everywhere** (`triplets._registry.EngineRegistry`):
  parser, sparql, validation, nquads, cimxml, csv and tools dispatch now share
  a single mechanism — eager `find_spec` availability probing at import, lazy
  module import on first use. The hand-rolled cimxml engine map and the
  ad-hoc nquads/csv dispatch in `triplets.export` are folded in; all public
  names and behavior unchanged.
- **CIM XML export consumes polars input directly** (cython engine): the
  compiled extension reads Arrow `utf8` / `large_utf8` / `string_view` /
  dictionary string columns through the shared accessor `triplets/_arrow/string_column.h`
  (lifted from the qlever Arrow ingest), so polars frames export without a
  pandas hop; per-instance splitting runs in the input's own flavor. Output is
  byte-identical across input flavors. The `python_lxml` engine remains
  pandas-only (auto-picked for `datatypes=True`).

### Fixed
- **Out-of-core DuckDB N-Quads export**: `con.export_to_nquads` streams the
  table through duckdb's record-batch reader into the polars formatter, one
  ~1M-row batch at a time — peak memory stays flat regardless of table size
  (measured: ~350-400 MB delta from 1.1M to 4.6M rows vs linear growth
  before), same speed. `export_to_nquads` also accepts a
  `pyarrow.RecordBatchReader` directly (polars engine required). Line order
  follows the table scan; N-Quads consumers are order-independent.
- **DuckDB exports fetch arrow, not pandas**: `con.export_to_nquads/csv/
  cimxml/excel` used to materialize the table as a pandas DataFrame
  (~383 ms/1.14M rows) before exporting — they now use duckdb's native arrow
  result (~101 ms), and the exporters accept pyarrow input directly (the
  nquads polars engine adopts it in ~9 ms). Exports are still whole-table
  in-memory; chunked export remains a recorded follow-up.
- **DuckDB `regex=True` semantics**: `filter_triplets` /
  `filter_triplets_by_value` used SQL `SIMILAR TO` (full-match); they now use
  `regexp_matches()` — search semantics anywhere in the value, matching the
  pandas/polars `str.contains` engines. Covered by new regex parity tests.
- **DuckDB type-name quoting**: `filter_triplets_by_type` interpolated the
  type name unescaped into SQL; it now goes through the shared literal quoting
  (a name containing `'` filters correctly instead of breaking the query).
- **Empty-parse schema**: `parse([], return_type="arrow"/"polars")` now
  returns the same string / dictionary-encoded column schema as a non-empty
  parse, so empty results concatenate cleanly with real ones.
- **Tools dispatcher engine mismatches**: `triplets.tools.<fn>(df,
  engine="duckdb")` and other input/engine mismatches now raise a clear
  `TypeError` at the boundary (previously they silently ran the pandas engine
  and failed deep inside); unknown engine names raise `ValueError`. Likewise
  `cgmes_tools` data functions reject `engine=` outside pandas/polars with a
  `ValueError` (duckdb/arrow input still runs via the pandas boundary).
- **`parse()` rejects unknown keyword arguments**: the unused `**kwargs`
  swallowed typos silently (a misspelled option looked like it worked while
  doing nothing); mistakes now raise `TypeError`.
- **DuckDB mutators preserve extra columns**: the five mutating helpers run
  in-place DML (UPDATE/DELETE/INSERT) instead of full-table rewrites — user
  columns beyond ID/KEY/VALUE/INSTANCE_ID were previously dropped silently
  on the first mutation, and load order is now stable across mutations.
- **File-handle leaks in zip parsing**: `find_all_xml`/`iter_all_xml` close
  the zip handles they open; the test suite runs with `always::ResourceWarning`
  and zero warnings (rdflib 7.6.0's internal Dataset deprecation noise is
  filtered as a documented upstream issue).

### Added
- **Streaming out-of-core DuckDB ingest** (`parser.parse_batches(...)`,
  `con.read_rdf(paths, append=...)`): one Arrow RecordBatch per XML file
  flows straight into DuckDB — the dataset is never materialized in Python.
  `append=True` adds to the existing table (created if missing); the default
  replaces. Zip members are read lazily, one at a time; `max_workers`
  parses up to that many files ahead (bounded, in-order prefetch — memory
  stays bounded while multi-file ingest parallelizes). This path requires an
  arrow parser engine; `string_type` and `categorical_columns` no longer
  apply to `con.read_rdf` and raise instead of being accepted.
- **DuckDB config persists in the database**: explicitly configured
  table/schema (via `connect(table=/schema=)`, `set_triplets_table`,
  `read_rdf(table=/schema=)`) is stored in a tiny `main."_triplets_config"`
  table, so reopening a persisted file resolves it automatically (cursors
  too). A bare `connect()` writes nothing; read-only opens resolve stored
  config without writing.
- **DuckDB multivalue tableviews**: `type/key/id_tableview` and
  `triplets_to_tableviews` gain `multivalue=` (renders `['a', 'b']` cells,
  matching pandas/polars) and `string_to_number=` (accepted for signature
  parity; `True` raises — VARCHAR views, TRY_CAST is a follow-up);
  `tableview_to_triplets(multivalue=True)` decodes the list encoding.
- **DuckDB tools on views and registered frames**: tableviews, references
  and order-invariant `content_hash` now work when `table=` targets a VIEW
  or a registered frame (`row_number()` fallback where `rowid` doesn't
  exist); `content_hash(order_sensitive=True)` on such targets raises a
  clear error. Tableview views are created in the resolved schema.
- **Configurable Arrow string layout** (`parse(..., string_type=...)`): the
  ID/VALUE columns can be produced as `utf8` (32-bit offsets, default),
  `large_utf8` (64-bit) or `string_view` (polars'/duckdb's native layout,
  adopted zero-copy by polars; pyarrow >= 16). `"auto"` picks per
  return_type: string_view for polars, utf8 otherwise. The cython engine
  builds the layout natively (no measured hot-loop cost); the lxml arrow
  engine casts once at finalize. The shared Arrow accessor handles all
  layouts, so CIM XML export and qlever ingest accept every variant.
- **`triplets.engines()` / `triplets.set_engine(...)`**: inspect what
  `engine="auto"` resolves to per subsystem (selected engine, availability,
  aliases) and override it globally (`set_engine(parser_cimxml="python_lxml_arrow")`;
  `"auto"`/`None` restores). Precedence: per-call `engine=` > `set_engine` >
  auto. Input-bound subsystems (tools, csv) follow the input flavor and cannot
  be overridden.
- **DuckDB connections in `triplets.tools` module functions**:
  `triplets.tools.type_tableview(con, ...)` and friends now route to the
  duckdb engine (previously only bound methods `con.type_tableview(...)`
  worked; the module dispatcher fell through to pandas and raised
  `AttributeError`).
- **DuckDB per-connection table/schema** (`duckdb.connect(table=..., schema=...)`,
  `con.set_triplets_table(...)`, `con.read_rdf(..., table=..., schema=...)`):
  each connection stores the default triplets relation; tools/export/SHACL use
  it when call kwargs omit `table`/`schema` (legacy `table_name=` still works).
  Identifiers are always double-quoted in generated SQL.
- **SHACL validation** (`df.shacl.validate(shapes)`, `triplets.validation`):
  shapes compile once into a constraint IR (content-hash cached), executed by
  four engines — `pyshacl` (spec reference), `pandas` (complete constraint
  registry incl. sh:sparql and sh:node), `polars` (lazy plans, the auto
  performance path — real Equipment profiles in ~2 s vs pyshacl minutes) and
  `duckdb` (larger-than-memory, explicit). Targets: `sh:targetClass` and
  `sh:targetSubjectsOf` (other target kinds warn at compile; pyshacl covers
  them). One deliberate deviation: datatype checks judge the raw lexical form
  (`lexical=True`). See docs/validation.md.
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
  one result per violation). `sources=` locates the reported instances in
  the original XML (grep-style pass at export time) and emits exact
  `region.startLine` annotations for GitHub code scanning.
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
- **Versioned NCP export schemas** generated from the ENTSO-E
  [application-profiles-library](https://github.com/entsoe/application-profiles-library):
  `schemas.ENTSOE_NC_2_4_1_552_*` from release branch `ncp-v2-4-1` — the most recent
  official NCP publication (upstream commit pinned in `rdfs/<bundle>/SOURCE.json`;
  2.4.2 and the 2.5 draft are not onboarded due to their DatasetMetadata rdfs:domain
  defect, [entsoe/application-profiles-library#92](https://github.com/entsoe/application-profiles-library/issues/92)).
  Snapshots are fetched with `python -m triplets.rdfs_tools.fetch_profiles`; bundles
  regenerate with `python -m triplets.rdfs_tools.cim_rdfs_to_json` (registry-driven,
  replaces the three per-bundle scripts). ReliCapGrid NC roundtrip tests cover every
  (TSO, profile) example instance.
- `parse(..., shorten_resources=False)`: lossless resource URIs for schema-grade
  parsing (python engines; the RDFS tools use it).
- **`sh:ValidationReport` export** (`violations.shacl.to_shacl_report(...)`,
  `triplets.validation.export_to_shacl_report`): violations frame → standard
  SHACL report (any rdflib format — default from path suffix, or `format=`);
  report metadata: `prov:generatedAtTime`, `dcterms:creator`, optional
  `dcterms:source` / `dcterms:references` via `report_source=` / `report_references=`.
- **Shared validation-run metadata**: `validate()` stamps
  `violations.attrs["validation"]` (start/end timestamps + duration, engine,
  creator, data file names from the Distribution meta rows, shape file names,
  shape/constraint counts, and coverage: `skipped_shapes` /
  `skipped_components` — what the run did NOT evaluate, empty for pyshacl) —
  read by every report exporter, so SARIF (`invocations`/`properties`), the
  sh:ValidationReport and the new `violations.shacl.to_csv()` (writes a
  `<name>_meta.<ext>` sidecar) / `to_excel()` (metadata sheet) all carry the
  same facts. Explicit `report_source=` / `report_references=` still override.
  Internal engine API: `shacl_ir.split_rules` now returns a 3-tuple
  `(vectorized, fallback, skipped components)`.
- `examples/shacl_reports.py`: uv-runnable (PEP 723) end-to-end demo — Svedala
  EQ with three deliberately introduced issues validated against the official
  ENTSO-E Equipment SHACL (Simple + Complex, downloaded on first run), report
  exported as sh:ValidationReport and as SARIF with exact file:line locations.
- **Vectorized `sh:path ( assoc rdf:type )`** — the profile "valueType"
  pattern: the constraint applies to the referenced object's type (dangling
  references yield no value node, per SHACL path semantics). Compiles 428
  previously skipped constraint rows of the ENTSO-E Equipment SHACL; other
  property paths keep the compile warning + pyshacl coverage.
- **Standalone source-location pass** (`violations.shacl.locate(sources=...)`,
  `triplets.validation.locate_violations`): stamps `SOURCE_URI`/`SOURCE_LINE`/
  `SOURCE_COLUMN`/`SOURCE_COLUMN_END` onto the violations frame in one
  grep-style pass; SARIF and the sh:ValidationReport export run it
  automatically when given `sources=`. SARIF regions are fully bounded
  (`startLine`/`startColumn`/`endLine`/`endColumn`, UTF-16 columns — GitHub
  cannot display a start-only region and tries to load the whole file from
  the error onward); the SHACL report carries plain-text
  "Source: file line N column M" messages, alongside Description/Schema
  messages from the enrichment columns.

### Fixed
- `pathlib.Path` inputs are accepted everywhere: `read_rdf`/`parse` (all
  engines, incl. the compiled Arrow parser — closes #75), `export_to_excel`
  (crashed on `path.endswith`), `export_to_csv`, `export_to_nquads`.
- `export_to_nquads()` without a path no longer crashes — defaults to
  `export.nq` in the working directory.
- The compiled Arrow parser decodes XML entities (`&gt;`, `&amp;`, …) like the
  lxml engines — values containing them now roundtrip through export.
- Export schemas include externally-defined (`Description`-stereotype) classes,
  so instance objects referenced by `rdf:about` are no longer dropped on export
  (NC associations attached to EQ objects; CGMES `SSH.RegulatingCondEq`,
  `DY.EnergyConsumer`).
- Header entries are injected into each profile section only when the profile
  does not define them itself — the header no longer overwrites a profile's own
  definitions (e.g. each profile's `String` datatype namespace, the CGMES 2.4
  `FH` section's `FullModel`/`Model.profile`).
- An ill-typed value under an `rdf_map` (e.g. `"abc"` on an `xsd:float` key) no
  longer cripples sh:sparql validation silently: the qlever ingest error names
  the offending key/value/datatype ("fix the instance data or the schema
  datatype"), the failed engine-state build is cached per content hash instead
  of being re-paid by every rule, and the failure is reported as ONE
  `triplets:invalidSparql` Warning per run while the rules still validate via
  the rdflib fallback. Ill-typed data itself is never silently "fixed".
- N-Quads exporters escape `\r` (was emitted raw — grammar-invalid output that
  broke strict oxigraph ingest).
- The context pass (`enrich()` / `validate(..., context=True)`) appends the
  violated property to generic shape-authored messages — "Missing required
  property (attribute). — IdentifiedObject.name"; messages that already name
  their property stay untouched, and raw engine output keeps the authored
  text verbatim.
- The qlever SPARQL engine accepts a bare pyarrow `Table`/`RecordBatch` as
  input (routed through the shared loader before hashing), matching the
  pandas/polars/duckdb and oxigraph engines.

### Changed
- **Python 3.14 supported**: wheels build for cp314, CI tests it (full suite
  verified incl. the compiled Arrow parser).
- Performance benchmarks are deselected by default (`pytest -m performance`
  runs them); the plain suite runs in minutes.
- The RDFS→JSON schema generator (`rdfs_tools` / `cim_rdfs_to_json`) derives
  attribute→class binding from `schema:domainIncludes` as well as `rdfs:domain`
  — the non-inferential convention for reused external terms (dcterms:, prov:,
  …) that avoids hijacking their meaning (see
  [application-profiles-library#92](https://github.com/entsoe/application-profiles-library/issues/92)).
  Attributes with no class binding are now emitted as top-level schema entries
  (with a warning) instead of being dropped.

### Removed
- `build-qlever.yml` CI workflow — the qlever engine is a local source build
  (decision record in TODO.md; build + fork pinning in docs/building.md).
- **Breaking:** the versionless NC schemas `schemas.ENTSOE_NC_552_ED1/ED2`
  (shipped in 0.1.0; generated from an incoherent mix of profile versions) are
  replaced by the versioned `schemas.ENTSOE_NC_2_4_1_552_*` bundles — no alias.
  The stray `ENTSO-E_Object Registry vocabulary_2.1.1_2022-12-29.json`
  single-profile export is gone as well (the OR profile lives in the NC bundles).

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

[0.2.0rc1]: https://github.com/Haigutus/triplets/releases/tag/0.2.0rc1
[0.1.0]: https://github.com/Haigutus/triplets/releases/tag/0.1.0
