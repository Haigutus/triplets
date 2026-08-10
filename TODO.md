# TODO — SHACL / SPARQL engine work

SHACL/SPARQL state as of 2026-07-16 (branch `feat/shacl-sparql`, since merged); engine/duckdb items below dated 2026-07-31..08-03 are from `feat/arrow-string-type` (phases A–D + embedded qlever + oxigraph
complete; code-review fixes, shared engine registry and explicit cache lifecycle
(`triplets.clear_caches()` / `cache_scope()`) landed 2026-07-14..16).
Engines: SHACL — pyshacl (reference) / pandas (debugging) / polars (auto, speed) / duckdb
(larger-than-memory); SPARQL — rdflib (reference) / oxigraph (pip performance) /
qlever (auto when built, performance).

## Correctness / coverage

- [ ] **Advanced SHACL targets are partially invisible to the vectorized engines.**
  NodeShapes in the CGMES SHACL library use `sh:target` (SPARQLTarget) or
  `sh:targetNode` instead of `sh:targetClass`; `shacl_ir.parse_ir` does not walk
  those. *Compile-time warning shipped 2026-07-13* (names the features and counts,
  points at engine="pyshacl"; called out in docs/validation.md status section).
  **`sh:targetSubjectsOf` supported since 2026-07-16** (IR target_kind="subjectsOf",
  focus = subjects carrying the KEY, all three engines + pyshacl parity tests).
  Still open: SPARQLTarget can ride on `triplets.sparql` now that qlever/oxigraph
  make it cheap; `targetNode`/`targetObjectsOf` are similarly small IR additions.
- [ ] `sh:xone` (2 uses in the library) — not in the IR component table (covered by
  the same compile warning; pyshacl-only).
- [x] qlever SELECT typing: all values are lexical strings **by design** (arrow decode,
  all-string triplets convention; consumers cast) — rdflib reference stays python-typed.
- [x] per-query engine-state keying costs a content_hash of the input (~260 ms per
  1.14M-row *pandas* frame; polars ~5 ms) — resolved with an explicit
  `data_unchanged=True` flag on `query()`: reuses the digest remembered for that exact
  object (id-keyed, weakref-evicted on GC), warm ASK ~25–370 ms → ~0.4 ms flat. By
  default the hash always runs; no automatic identity fast-path (in-place mutation
  staleness stays an explicit user assertion), no cross-flavor hash tricks (pandas does
  not route through polars).
- [ ] `sh:nodeKind` BlankNode / `*Or*` combinations — not expressible over triplets;
  skipped with a debug log (documented, likely permanent).
- [x] qlever index build fed by zero-copy Arrow (no N-Quads text round-trip): vendored
  patch exposes `createFromParser` (fork branch `libqlever-parser-injection`, rebased
  onto upstream master ecc04798, **upstream issue
  https://github.com/ad-freiburg/qlever/issues/3075 + draft PR
  https://github.com/ad-freiburg/qlever/pull/3074** — re-pin to ad-freiburg once
  merged), and `ArrowTripleParser` yields TurtleTriples straight from the triplet
  columns. Cold build 1.46 s → 1.01 s per 1.14M rows (Python side ~370 ms → ~3 ms);
  characters beyond the old escape set (`\t`, `\r`) now ingest losslessly; salt bumped
  to `triplets-qlever-2`. Scope rides qlever's native SPARQL-protocol dataset clauses
  (upstream added them while we were on the old pin), so the query text is never
  rewritten (protocol semantics: scope overrides a query's own FROM).
- [ ] Upstream `LibQlever.buildIndexAndRunQuery` segfaults on *pristine* master in our
  environment (exit 139, also without our patch — likely the `addWordsFromLiterals`
  part their own comment marks broken); our `buildIndexFromInjectedParser` test passes.
  Watch whether qlever CI agrees on PR #3074; report upstream if reproducible there.
- [x] `ArrowTripleParser` conversion parallelized (the old text path parsed in
  parallel; the first arrow version was single-threaded): row ranges of 100k convert
  on qlever's own TaskQueue/ThreadSafeQueue machinery (RdfMultifileParser pattern —
  bounded batch queue, feeder thread, exceptions through the queue with deterministic
  global row numbers). Index build 1.01 s → 0.62 s per 1.14M rows (95k: 271 → 188 ms);
  vs the original text path (serialize+parse) the build is now 2.4x faster.
- [x] Decision **reversed** (2026-07-12; originally 2026-07-10 "no oxigraph engine",
  when qlever measured 3.5–216x faster and heavy agg was 0.2 ms vs 51.7 ms): the
  qlever wheel packaging is still unresolved (see below), so a pip-only install was
  stuck on rdflib — the `oxigraph` engine (pyoxigraph wheel, `sparql_oxigraph.py`)
  now upgrades those installs to Rust speed (~3x import, 2–5x warm queries vs
  rdflib) and slots between qlever and rdflib in auto order. qlever keeps the
  performance crown and auto priority; measured numbers in docs/sparql.md.
- [ ] oxigraph engine future optimization: pyoxigraph releases the GIL during
  queries — the SHACL sh:sparql batch path could thread over constraints
  (today it runs sequentially; the fork pool stays rdflib-only).
- [x] Decision recorded (2026-07-12): **pyshacl stays on the Memory store** —
  `load_dataset(store="oxigraph")` (oxrdflib wrapper over the engine's cached
  store, `default_union=False` because the projection is the union) gives
  identical violations, but pyshacl's forced Memory clone through the wrapper
  costs more than the Rust parse saves (95k warm 1.8 s vs 1.2 s; 1.14M
  load+clone 40 s vs 33 s). The `store=` parameter on `shacl_pyshacl.validate`
  is the explicit opt-in for callers whose store is already loaded for SPARQL.
  Revisit if pyshacl gains a no-clone path or oxrdflib gets a bulk iterator.
- [ ] qlever binary result formats (`octetStream` raw Ids; `binaryQleverExport`
  Ids+string-sidecar, stub at pin 9ec88a0) are HTTP-boundary features — not useful
  embedded (we decode the IdTable directly). The StringMapping *idea* maps to emitting
  Arrow dictionary columns for repetitive result columns — possible future decode
  optimization. qlever's `IndexRebuilder`/`materializeToIndex` (delta updates without
  re-parse) is the machinery for a future incremental-update story.
- [x] `cimxml_cython_pugixml.pyx` reuses the offset/dictionary/large_utf8-aware Arrow
  column accessor, lifted into the shared header `triplets/_arrow/string_column.h`
  (2026-07-31) — polars input now exports without a pandas hop; the header gained the
  string_view branch and the parser emits it (parse(string_type=...), 2026-08-01).
  Still open: the sequential export path's per-row Python dict lookups hold the GIL —
  the GIL-free C++ ExportTables from the threaded-export experiment (issue #87,
  branch explore/cimxml-export-threading) would fix that with order preserved.

## Build / packaging / CI

- [x] **qlever packaging — decided (2026-07-13): source build only (local).**
  No wheels, no CI (build-qlever.yml deleted): the pip-installable performance
  path is the `oxigraph` extra; qlever stays the local power path, verified by
  `pixi run -e qlever test-qlever` before/after qlever bumps (build + pinning
  documented in docs/building.md). Measured basis (2026-07-06): `_qlever.so`
  53 MB (37.5 MB stripped, ~10 MB compressed); auditwheel graft closure
  48.9 MB dominated by `libicudata` 31.6 MB → a qlever-enriched linux wheel
  ≈ 30–35 MB compressed vs ≈ 3–4 MB today (~10x). Pre-analyzed revisit path
  if demand appears: separate `triplets-qlever` PyPI distribution (wheel =
  just the extension) + extra `qlever = ["triplets-qlever"]`; a filtered ICU
  data build (`ICU_DATA_FILTER_FILE`, qlever uses ICU only for vocabulary
  collation in `src/index/StringSortComparator.h`) cuts the wheel to
  ≈ 18–20 MB; libssl/libcrypto (8 MB) only feed the HTTP code the embedded
  facade never runs. Build mechanics: cibuildwheel `CIBW_BEFORE_ALL` qlever
  compile (boost ≥ 1.81 must be built in the manylinux container), strip
  before `auditwheel repair`.
- [ ] macOS: the pixi `qlever` env claims `osx-arm64` but the qlever compile and the
  extension link flags are untested off Linux.
- [ ] qlever *on-disk* index cache (`$TMPDIR/triplets-qlever/<hash>/`) has no eviction —
  one index per distinct data+schema, grows unboundedly (delete the dir to reclaim;
  reload ~4 ms). The *in-memory* engine caches got an explicit lifecycle 2026-07-16:
  `triplets.clear_caches()` / `with triplets.cache_scope():`.
- [x] duckdb engine in-memory overhead — fixed: constraints batch 100-per-`UNION ALL`
  statement (28 s → 10 s on the real profiles; polars remains the in-memory engine).
- [ ] No true larger-than-memory validation test for the duckdb engine (on-disk DB
  larger than RAM).

## Process

- [x] Full-suite run + PR of `feat/shacl-sparql` to main — merged; the current open
  branch is `feat/arrow-string-type`
  (Unreleased section); version bump = release tag (versioneer), alpha as `0.2.0a1`.
- [ ] Archive `dev_shacl` (everything worth porting has been ported; the branch is
  checked out in the main worktree — retag/delete from there).
- [x] `performance` pytest marker deselected by default (`addopts = '-m "not
  performance"'`; run them with `pytest -m performance`). Plain suite ≈ 100 s serial / ≈ 40 s with `-n auto`.
- [ ] Pre-existing tools follow-ups (unrelated to SHACL): polars eval in
  `tableview_to_triplets`, lossy `INSTANCE_ID` round-trip. (duckdb `multivalue`
  implemented 2026-08-01 — encoding matches polars, parity-tested; duckdb
  `string_to_number=True` still raises, TRY_CAST implementation open.)
- [x] Chunked duckdb export — N-Quads DONE (2026-08-04, merged from
  `explore/duckdb-chunked-export`): `con.export_to_nquads` streams via
  `to_arrow_reader(1M rows)` + per-batch polars writer; peak-RSS delta flat
  (~350-400 MB) from 1.1M to 4.6M rows vs linear growth whole-table, same
  speed. No ORDER BY (N-Quads lines are row-local).
- [ ] Chunked duckdb export — cimxml still open: needs `ORDER BY INSTANCE_ID`
  (contiguity) + a batch accumulator yielding one instance frame at a time
  into the existing `generate_xml` + packaging loops (turn the
  `xml_documents` list into a generator); memory bound = largest instance.
  Sequential (streaming and `max_workers` process-parallelism don't compose).
- [ ] Streaming exports require the polars engine (clear ValueError otherwise);
  a per-batch pandas fallback writer is possible if a polars-free
  larger-than-RAM need ever appears.

## ENTSO-E SHACL findings (application-profiles-library)

Checked against the issue tracker on 2026-07-06:

- [x] **Turtle syntax error** in `61970-600-1_AllProfiles-AP-Con-Complex-SolvedMAS-SHACL.ttl`
  (line ~97: `.` instead of `;`, file unparseable) — filed as issue #63 and **FIXED at
  `entsoe/main` (f36bd97, which also adds riot syntax validation)**; the Haigutus fork
  is one commit behind → sync the fork, close #63.
- [x] **`HAVING` without `GROUP BY`** — 2 constraint queries (PowerTransformerEnd
  `ratedS` + `RegulatingControl-samePointSparql`), present on `entsoe/main` and the
  `cgmes-v3-0` release branch. SPARQL 1.1 defines HAVING only over grouped solutions
  (§11.3); with no aggregate and non-aggregate projection the construct has no
  conforming reading (§11.4), so engines diverge (rdflib = filter, QLever = reject).
  **Reported as a comment on #70** (issuecomment-4894091376) and **fixed upstream via
  PR entsoe/application-profiles-library#82** (HAVING → FILTER, semantics-preserving).
- [x] Query auto-fixing removed entirely (`_rewrite_bare_having` deleted): constraint
  queries run exactly as authored; a rejected query is evaluated on rdflib instead and
  flagged in the report as `triplets:invalidSparql` (Warning; Violation when every
  engine fails), deduplicated across the sh:targetClass fanout. Direct
  `sparql.query()` failures raise with qlever's message + the query text.
- [ ] **Portability note**: 17 shapes use SHACL-AF `sh:SPARQLTarget` (30 shapes total
  have no `sh:targetClass`) — constraints silently vanish on core-only validators;
  related to open issues #73/#58. Mention when commenting.
- [ ] **Observation for QoCDC**: `sh:datatype` on typed literals cannot flag
  non-canonical lexical forms (`"1"` under `xsd:float` — 2,187 occurrences in the
  Svedala grid alone); would need explicit `sh:pattern` on float paths. Our
  `triplets:lexicalForm` check covers it locally.
- [ ] Multithreaded pugixml CIM XML export — prototype on
  `explore/cimxml-export-threading` (~3x at 4 threads on RealGrid EQ, content
  identical, element order hash-grouped, FullModel pinned first). Kept as a
  documented experiment; open decisions in
  https://github.com/Haigutus/triplets/issues/87. The GIL-free C++ lookup
  tables from the prototype would also speed the sequential path (order kept).
