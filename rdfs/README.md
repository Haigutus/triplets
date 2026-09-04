# RDFS schema sources

Committed snapshots of the RDFS profile definitions that `triplets/export_schema/*.json`
is generated from. Snapshots are committed (not fetched at build time) so schema
generation is reproducible offline and upstream changes arrive as reviewable diffs.

| Directory | Source | Version authority |
|---|---|---|
| `ENTSOE_NC_2.4.1/` | [entsoe/application-profiles-library](https://github.com/entsoe/application-profiles-library) release branch `ncp-v2-4-1` — the most recent official NCP publication | branch name + `SOURCE.json` commit pin |
| `ENTSOE_NC_2.5-dev/` | same repo, `main` / `NCP/RDFS` — unpublished NCP 2.5 draft (`-dev` until an `ncp-v2-5-0` release branch exists) | `SOURCE.json` commit pin |
| `ENTSOE_CGMES_2.4.15/`, `ENTSOE_CGMES_3.0.0/`, `ENTSOE_FH/` | legacy hand-collected sets (predate the fetch workflow) | version in filenames |

NCP 2.4.2 is **not** onboarded (already published; ENTSO-E will not patch it). The 2.5
draft **is** onboarded as `ENTSOE_NC_2.5-dev` after
[application-profiles-library#99](https://github.com/entsoe/application-profiles-library/pull/99)
restored DatasetMetadata class linkage via `schema:domainIncludes` (fixes
[#92](https://github.com/entsoe/application-profiles-library/issues/92)). Drop the
`-dev` suffix when `ncp-v2-5-0` is cut.

Upstream files are versionless by name (git-diff friendly); the release branch is the
authoritative version — per-profile `owl:versionInfo` inside the files lags the release
(e.g. the AE profile still says 2.4.0 on `ncp-v2-4-1`). Each fetched snapshot carries a
`SOURCE.json` with `{repo, ref, commit, path, fetched}`.

## Onboarding a new release

1. Add an entry to `SOURCES` in `triplets/rdfs_tools/fetch_profiles.py`
   (name, upstream ref, path) and to `BUNDLES` in
   `triplets/rdfs_tools/cim_rdfs_to_json.py` (rdfs dir, header profile, indexing).
2. ```
   uv run python -m triplets.rdfs_tools.fetch_profiles ENTSOE_NC_X.Y.Z
   uv run python -m triplets.rdfs_tools.cim_rdfs_to_json ENTSOE_NC_X.Y.Z
   ```
3. Commit `rdfs/ENTSOE_NC_X.Y.Z/` and the generated
   `triplets/export_schema/ENTSOE_NC_X.Y.Z_552_ED*.json` — the bundle is
   automatically available as `schemas.ENTSOE_NC_X_Y_Z_552_ED*`.
4. Run the roundtrip suite: `uv run pytest tests/test_roundtrip_nc.py -q`.

Generation is deterministic: rerunning `cim_rdfs_to_json` must produce no diff
(`git diff --exit-code triplets/export_schema`).

## Streamlining roadmap (not yet implemented)

- Merge `SOURCES` + `BUNDLES` into one registry keyed by upstream ref, with the bundle
  name derived from it (`ncp-v2-5-0` → `ENTSOE_NC_2.5.0`) — onboarding becomes one
  registry line + two commands.
- CI reproducibility guard: regenerate schemas and `git diff --exit-code
  triplets/export_schema` to prevent hand-edited JSON drift.
- Automate the fetch of the upstream `SHACL/*.ttl` snapshots with the same fetch
  spec (SHACL validation itself ships in 0.2 via `triplets.validation`; only the
  fetch of the upstream shape files is still manual).
- Migrate the legacy CGMES snapshots to the fetch workflow (upstream branches
  `cgmes-v3-0` / `cgmes-v2-4`); filenames differ from the local copies, so that is a
  separate migration with its own regeneration diff review.
- Record the instance-facing `https://ap.cim4.eu/<Profile>/<ver>` IRI in
  `ProfileMetadata` so export profile resolution can match `conformsTo` directly
  instead of relying on the `dcat:keyword` hint (instances use `ap.cim4.eu`, schema
  `versionIRI` uses `ap-voc.cim4.eu`).

## Known upstream defects (reported)

- `ncp-v2-4-2` dropped `rdfs:domain` from 11 URI-typed `dcat:Dataset` properties
  in `DatasetMetadata-AP-Voc-RDFS2020.rdf` (`conformsTo`, `publisher`, `license`,
  `accessRights`, …; upstream commit `489c5c51ac4f`) — they cannot be tied to the
  Dataset class, so a schema from that branch would lack header attributes.
  Reported as [application-profiles-library#92](https://github.com/entsoe/application-profiles-library/issues/92);
  fixed on `main` (and therefore in `ENTSOE_NC_2.5-dev`) by
  [PR #99](https://github.com/entsoe/application-profiles-library/pull/99) using
  `schema:domainIncludes` for reused external terms. 2.4.2 stays unpatched.
- Several ReliCapGrid example instances do not conform to the released NCP 2.4.x
  profiles (draft-only attributes; `AssociationUsed=No` directions serialized) — the
  cases for the pinned submodule commit live in `KNOWN_MISMATCH` in
  `tests/test_roundtrip_nc.py`. Upstream fixed most of them in the 2026-07-15
  restructure (which also moved instances back to
  `Instance/<TSO>/NetworkCode/cimxml/`); the surviving cases are reported in
  [relicapgrid#340](https://github.com/entsoe/relicapgrid/issues/340). Bumping the
  submodule past that restructure requires updating the data paths in
  `tests/conftest.py`, `tests/_parity.py` and `tests/test_roundtrip_nc.py`.
