# RDFS schema sources

Committed snapshots of the RDFS profile definitions that `triplets/export_schema/*.json`
is generated from. Snapshots are committed (not fetched at build time) so schema
generation is reproducible offline and upstream changes arrive as reviewable diffs.

| Directory | Source | Version authority |
|---|---|---|
| `ENTSOE_NC_2.4.1/`, `ENTSOE_NC_2.4.2/` | [entsoe/application-profiles-library](https://github.com/entsoe/application-profiles-library) release branches `ncp-v2-4-1` / `ncp-v2-4-2` | branch name + `SOURCE.json` commit pin |
| `ENTSOE_NC_2.5-dev/` | same repo, `main:NCP/CurrentRelease` (unreleased draft) | `SOURCE.json` commit pin; renamed to `ENTSOE_NC_2.5.0` when ENTSO-E releases |
| `ENTSOE_CGMES_2.4.15/`, `ENTSOE_CGMES_3.0.0/`, `ENTSOE_FH/` | legacy hand-collected sets (predate the fetch workflow) | version in filenames |

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
- Snapshot the upstream `SHACL/*.ttl` with the same fetch spec (feeds the
  `triplets.validation` engines).
- Migrate the legacy CGMES snapshots to the fetch workflow (upstream branches
  `cgmes-v3-0` / `cgmes-v2-4`); filenames differ from the local copies, so that is a
  separate migration with its own regeneration diff review.
- Record the instance-facing `https://ap.cim4.eu/<Profile>/<ver>` IRI in
  `ProfileMetadata` so export profile resolution can match `conformsTo` directly
  instead of relying on the `dcat:keyword` hint (instances use `ap.cim4.eu`, schema
  `versionIRI` uses `ap-voc.cim4.eu`).

## Known upstream defects (to report)

- `ncp-v2-4-2` (and the 2.5 draft) dropped `rdfs:domain` from 11 `dcat:Dataset`
  properties in `DatasetMetadata-AP-Voc-RDFS2020.rdf` (`conformsTo`, `publisher`,
  `license`, `accessRights`, …) — they can no longer be tied to the Dataset class, so
  generated 2.4.2/2.5-dev schemas lack those header attributes and exports drop them
  (pinned by `test_roundtrip_ncp_2_4_2_upstream_domain_regression`).
- Several ReliCapGrid example instances do not conform to the released NCP 2.4.x
  profiles (draft-only attributes; `AssociationUsed=No` directions serialized) — the
  exact cases live in `KNOWN_MISMATCH` in `tests/test_roundtrip_nc.py`.
