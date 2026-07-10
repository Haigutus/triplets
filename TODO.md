# TODO — SHACL / SPARQL engine work

State as of 2026-07-06, branch `feat/shacl-sparql` (phases A–D + embedded qlever complete).
Engines: SHACL — pyshacl (reference) / pandas (debugging) / polars (auto, speed) / duckdb
(larger-than-memory); SPARQL — rdflib (reference) / qlever (auto when built, performance).

## Correctness / coverage

- [ ] **Advanced SHACL targets are invisible to the vectorized engines.** 30 NodeShapes
  across the CGMES SHACL library use `sh:target` (SPARQLTarget), `sh:targetNode` or
  `sh:targetSubjectsOf` instead of `sh:targetClass`; `shacl_ir.parse_ir` walks only
  `targetClass` shapes and does not log the skip. Minimum: warn at compile time.
  Real fix: `targetSubjectsOf` is trivial over triplets (focus = subjects carrying the
  KEY); SPARQLTarget can ride on `triplets.sparql` now that qlever makes it cheap.
- [ ] `sh:xone` (2 uses in the library) — not in the IR component table (kept + logged;
  pyshacl-only).
- [x] qlever SELECT typing: all values are lexical strings **by design** (arrow decode,
  all-string triplets convention; consumers cast) — rdflib reference stays python-typed.
- [ ] per-query engine-state keying costs a content_hash of the input — ~0.7 s for a
  1.14M-row *pandas* frame (polars ~35 ms), which dominates small queries (ASK ~0.7 s).
  Options: cheaper pandas hash, or a WeakKeyDictionary identity fast-path per object.
- [ ] `sh:nodeKind` BlankNode / `*Or*` combinations — not expressible over triplets;
  skipped with a debug log (documented, likely permanent).

## Build / packaging / CI

- [ ] **Distribution wheels for the qlever extension — packaging decision pending.**
  Measured (2026-07-06): `_qlever.so` 53 MB (37.5 MB stripped, ~10 MB compressed);
  auditwheel graft closure 48.9 MB, dominated by `libicudata` 31.6 MB → a
  qlever-enriched linux wheel ≈ **30–35 MB compressed vs ≈ 3–4 MB today** (~10x).
  ICU analysis: qlever uses ICU only for vocabulary collation
  (`src/index/StringSortComparator.h`), one configurable locale (default en_US) —
  a filtered ICU data build (`ICU_DATA_FILTER_FILE`, Node small-icu style) cuts
  icudata to ~2–4 MB → wheel ≈ 18–20 MB. libssl/libcrypto (8 MB) are referenced
  only via qlever's HTTP/websocket code the embedded facade never runs — possibly
  excludable from the link (experiment). Options:
  1. bundle into the single per-OS wheel — rejected on size (every parser/export
     user pays ~10x download for an engine they may not use);
  2. separate `triplets-qlever` distribution on PyPI (wheel = just the extension,
     importable as `triplets_qlever`) + extra `qlever = ["triplets-qlever"]` so
     `pip install triplets[qlever]` pulls it; engine import falls back
     in-package → companion; heavy wheel re-releases only on qlever bumps;
  3. status quo — source build only (pixi locally, build-qlever.yml in CI).
  Build mechanics whichever way: cibuildwheel `CIBW_BEFORE_ALL` qlever compile
  (boost ≥ 1.81 must be built in the manylinux container; repos ship ≤ 1.75),
  strip before `auditwheel repair`.
- [ ] macOS: the pixi `qlever` env claims `osx-arm64` but the qlever compile and the
  extension link flags are untested off Linux.
- [ ] qlever index cache (`$TMPDIR/triplets-qlever/<hash>/`) has no eviction — one index
  per distinct data+schema+scope, grows unboundedly.
- [x] duckdb engine in-memory overhead — fixed: constraints batch 100-per-`UNION ALL`
  statement (28 s → 10 s on the real profiles; polars remains the in-memory engine).
- [ ] No true larger-than-memory validation test for the duckdb engine (on-disk DB
  larger than RAM).

## Process

- [ ] Full-suite run + PR of `feat/shacl-sparql` to main; CHANGELOG entries for the
  SHACL/SPARQL work (phases A–D, qlever, the lexical-form deviation, auto-engine
  orders); version bump.
- [ ] Archive `dev_shacl` (everything worth porting has been ported).
- [ ] `performance` pytest marker is not deselected by default (full suite ≈ 35 min);
  decide on `-m "not performance"` addopts or keep the benchmark-heavy default.
- [ ] Pre-existing tools follow-ups (unrelated to SHACL): duckdb `multivalue` no-op,
  polars eval in `tableview_to_triplets`, lossy `INSTANCE_ID` round-trip.

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
