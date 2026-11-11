# TODO

Open work only. Done items live in CHANGELOG.md; decisions in docs/.
Engines: SHACL — pyshacl (reference) / pandas / polars (auto) / duckdb (explicit, larger-than-memory);
SPARQL — rdflib (reference) / oxigraph (pip performance) / qlever (local build, auto when built).

## Correctness / coverage

- [ ] Custom `sh:target` without `sh:select` (`sh:SPARQLTargetType` instance, JS target)
  stays pyshacl-only; the vectorized engines warn at compile time and list the shape in
  `skipped_shapes`.
- [ ] Non-numeric values in range and pair comparisons: pandas / polars / duckdb report
  every date value under `sh:lessThan`, `sh:lessThanOrEquals` and the range checks, and
  skip a range constraint whose bound is a date
  (https://github.com/Haigutus/triplets/issues/216).
- [ ] duckdb `tableview_to_triplets(string_to_number=True)` raises
  (`tools/duckdb_engine.py:_check_string_to_number`); a TRY_CAST implementation is open.
  Related tools follow-ups: polars eval in `tableview_to_triplets`, lossy `INSTANCE_ID`
  round-trip.
- [ ] Chunked duckdb CIM XML export: needs `ORDER BY INSTANCE_ID` + a batch accumulator
  yielding one instance frame at a time into `generate_xml` (turn `xml_documents` into a
  generator); memory bound = largest instance; sequential only (`max_workers` process
  parallelism does not compose with streaming). N-Quads already streams
  (`_accessor.py`, `to_arrow_reader`).
- [ ] No larger-than-memory validation test for the duckdb engine (on-disk DB larger than RAM).
- [ ] Multithreaded pugixml CIM XML export — prototype on `explore/cimxml-export-threading`
  (content identical, element order hash-grouped, FullModel pinned first); open decisions in
  https://github.com/Haigutus/triplets/issues/87. Its GIL-free C++ lookup tables would also
  speed the sequential path, order kept. One-instance-per-thread variant (GIL released in
  the row loop, thread pool instead of `max_workers` processes):
  https://github.com/Haigutus/triplets/issues/213.

## qlever (local source build)

- [ ] Re-pin `vendor/qlever` to `ad-freiburg/qlever` once
  https://github.com/ad-freiburg/qlever/pull/3074 merges (fork branch
  `libqlever-parser-injection`, upstream issue
  https://github.com/ad-freiburg/qlever/issues/3075).
- [ ] Upstream `LibQlever.buildIndexAndRunQuery` segfaults on pristine master in our
  environment (exit 139, also without our patch); our `buildIndexFromInjectedParser` test
  passes. Report upstream if qlever CI reproduces it on that PR.
- [ ] On-disk index cache (`$TMPDIR/triplets-qlever/<hash>/`) has no eviction: one index per
  distinct data+schema, grows unboundedly (delete the dir to reclaim).
- [ ] macOS: the pixi `qlever` env claims `osx-arm64`; the qlever compile and the extension
  link flags are untested off Linux.

## Process

- [ ] Archive `dev_shacl` once nothing more is ported from it (the branch is checked out in
  the main worktree — retag/delete from there, then on origin).

## ENTSO-E SHACL notes (application-profiles-library)

- [ ] Portability: 17 shapes use SHACL-AF `sh:SPARQLTarget` (30 shapes have no
  `sh:targetClass`) — constraints silently vanish on core-only validators. Mention when
  commenting upstream.
