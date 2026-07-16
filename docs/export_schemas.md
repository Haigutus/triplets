# Export Schemas

> **Single source of truth:** edit this file only. The published docs include it
> from `docs/source/guides/export_schemas.md` via MyST `{include}`.

Export schemas are the JSON bundles in `triplets/export_schema/` that tell the
CIM XML / N-Quads exporters (and the validation context enrichment) what a
profile looks like: which classes exist, how objects are identified
(`rdf:ID` vs `rdf:about`), which attributes/associations/enumerations each
class carries, their namespaces, datatypes and multiplicities. They are
generated from the ENTSO-E RDFS profile definitions — never edited by hand.

## Shipped Bundles

One JSON per (profile release, IEC 61970-552 serialization edition):

| Bundle | Source | Runtime attribute |
|--------|--------|-------------------|
| `ENTSOE_CGMES_2.4.15_552_ED1/ED2.json` | `rdfs/ENTSOE_CGMES_2.4.15/` (legacy hand-collected) | `schemas.ENTSOE_CGMES_2_4_15_552_ED1/ED2` |
| `ENTSOE_CGMES_3.0.0_552_ED1/ED2.json` | `rdfs/ENTSOE_CGMES_3.0.0/` (legacy hand-collected) | `schemas.ENTSOE_CGMES_3_0_0_552_ED1/ED2` |
| `ENTSOE_NC_2.4.1_552_ED1/ED2.json` | `rdfs/ENTSOE_NC_2.4.1/` — snapshot of [entsoe/application-profiles-library](https://github.com/entsoe/application-profiles-library) branch `ncp-v2-4-1`, commit-pinned in `SOURCE.json` | `schemas.ENTSOE_NC_2_4_1_552_ED1/ED2` |

`triplets/export_schema/__init__.py` walks the directory at import time and
exposes every JSON as an attribute on the `schemas` object (filename sanitized:
`.`/`-` become `_`), so a new bundle file is available without code changes.
Source provenance, release onboarding and known upstream defects are documented
in [`rdfs/README.md`](../rdfs/README.md).

## 552_ED1 vs 552_ED2

The two serialization editions of IEC 61970-552 differ in how identifiers are
written; each bundle is generated once per edition (`cim_serializations` in
`rdfs_tools/cim_rdfs_to_json.py`):

| | `552_ED1` (2013) | `552_ED2` (2016) |
|---|---|---|
| object identity | `rdf:ID="_uuid"` | `rdf:about="urn:uuid:uuid"` |
| references | `rdf:resource="#_uuid"` | `rdf:resource="urn:uuid:uuid"` |
| `conformsTo` | `urn:iso:std:iec:61970-552:2013` | `urn:iso:std:iec:61970-552:2016` |

The header profile inside every bundle is always converted with `552_ED2`
rules — `md:FullModel` / `dcat:Dataset` headers use `rdf:about` with
`urn:uuid:` in real documents regardless of the body edition.

**The edition only affects CIM XML export.** N-Quads export is
serialization-edition-independent: `export_to_nquads` reconstructs every
subject, reference and graph as an absolute `urn:uuid:` IRI from the bare
UUID (see `export/nquads_utils.py`), so a bundle's ED1 and ED2 produce
byte-identical `.nq` output — both valid input for any SPARQL engine. The
`#uuid` fragment-reference pitfall that makes ED1 unsafe to load into a
triplestore is specific to RDF/XML (`rdf:resource="#uuid"` resolves against
the document `xml:base`, often the filename); it does not arise here because
N-Quads never uses relative fragments. Pass either edition for N-Quads; the
examples use ED2 only for consistency with the CIM XML calls beside them.

## Anatomy of a Bundle

A bundle is a dict of profile sections keyed by profile keyword
(`EQ`, `SSH`, ... / `AE`, `CO`, `RAS`, ...). Each section:

```
"AE": {
    "ProfileMetadata":     keyword, title, versionInfo, versionIRI, conformsTo,
                           serialization, ... + "header": identity of the injected
                           header profile (keyword, title, identifier, versionInfo,
                           versionIRI)
    "ProfileNamespaceMap": prefix -> namespace (profile + header, merged)
    "ProfileXMLBase":      xml:base of the profile
    "<Class>":             {"attrib": {"attribute": rdf:ID|rdf:about, "value_prefix": ...},
                            "type": "Class", "inheritance": [...], "stereotyped": bool,
                            "namespace": ..., "parameters": [...]}
    "<Class.attribute>":   {"type": "Attribute", "dataType": ..., "xsd:type": ...,
                            "multiplicity": ..., "namespace": ...}
    "<Class.Association>": {"type": "Association", "range": ..., "attrib": {...}}
    "<Class.enumAttr>":    {"type": "Enumeration", "range": ..., "values": [...]}
    "<Datatype|EnumValue>": supporting definitions referenced by the entries above
}
```

Two class flavors matter for export:

- **concrete** classes (stereotype `concrete`) are instantiated with the
  edition's ID attribute (`rdf:ID` in ED1);
- **Description** classes (stereotype `Description`) are defined in another
  profile and referenced with `rdf:about` — e.g. NC profiles attaching
  associations to EQ equipment, or CGMES SSH updating `RegulatingCondEq`.
  `"stereotyped": true` marks them.

## How Exporters Pick a Section

`export/cimxml_utils.py` matches each instance against the schema's own
identity metadata — there is no hardcoded per-release knowledge:

```
resolve_instance_config(instance_data, rdf_map)
|
|-> _profile_identity_index(rdf_map)
|   every section registers: section key, ProfileMetadata.keyword,
|   versionIRI, conformsTo -> section name
|
|-> _instance_profile_hints(instance_data)
|   header fields in priority order:
|   Model.messageType > keyword > Model.profile > conformsTo
|
|-> first hint found in the index wins
|-> fallback: legacy 2.4.15 Model.profile URL substrings (PROFILE_URL_MAP)
'-> fallback: schema root (warns)
```

Note: NC instances declare `dcterms:conformsTo` on host `ap.cim4.eu` while the
schema `versionIRI` uses `ap-voc.cim4.eu`, so NC resolution works via
`dcat:keyword` (pinned by `tests/test_roundtrip_nc.py`).

## Generation Pipeline

Two registry-driven scripts in `triplets/rdfs_tools/`; both take bundle names
as arguments and default to all:

```
python -m triplets.rdfs_tools.fetch_profiles [bundle ...]
|
|-> SOURCES registry: name -> {repo, ref, path}
|-> shallow sparse clone of the upstream ref
|-> wipe + recreate rdfs/<bundle>/, copy *.rdf
'-> write rdfs/<bundle>/SOURCE.json  {repo, ref, commit, path, fetched}

python -m triplets.rdfs_tools.cim_rdfs_to_json [bundle ...]
|
|-> BUNDLES registry: name -> {rdfs_dir, header, exclude, index}
|
'-> build_bundle(name, spec)
    |
    |-> parse header RDFS -> convert_profile(552_ED2)   # header entries + namespaces
    |   '-> header identity (get_metadata) kept for ProfileMetadata["header"]
    |
    |-> parse all profile RDFS in rdfs_dir (minus exclude)
    |   parse(..., engine="python_lxml_pandas", shorten_resources=False)
    |   # lossless: ranges/inheritance/stereotypes are cross-namespace URIs
    |
    '-> per serialization edition (552_ED1, 552_ED2):
        |-> convert(): one section per RDFS profile (classes, attributes,
        |   associations, enumerations, datatypes + ProfileMetadata)
        |-> spec["index"]: section keying strategy
        |   index_by_keyword          (CGMES 3.0, NC — error on missing keyword)
        |   index_largest_per_keyword (CGMES 2.4 — dedup, "_" stripped)
        |-> inject header entries per section, only where missing
        |   (a profile's own definitions are never overwritten)
        '-> write triplets/export_schema/<name>_<edition>.json
```

Generation is deterministic — regenerating must produce no diff:

```shell
uv run python -m triplets.rdfs_tools.cim_rdfs_to_json
git diff --exit-code triplets/export_schema
```

## Onboarding a New Release

1. Add one entry to `SOURCES` (`fetch_profiles.py`) and one to `BUNDLES`
   (`cim_rdfs_to_json.py`) — commented-out templates for NCP 2.4.2/2.5 sit in
   both registries.
2. Fetch, generate, and run the roundtrip suite:

```shell
uv run python -m triplets.rdfs_tools.fetch_profiles ENTSOE_NC_X.Y.Z
uv run python -m triplets.rdfs_tools.cim_rdfs_to_json ENTSOE_NC_X.Y.Z
uv run pytest tests/test_roundtrip_nc.py -q
```

3. Commit the snapshot and the generated JSONs — the bundle appears as
   `schemas.ENTSOE_NC_X_Y_Z_552_ED*` automatically.

The roundtrip suite (`tests/test_roundtrip_nc.py`) parses every ReliCapGrid
(TSO, profile) example instance, exports it through the bundle, re-parses and
requires exact row-set equality — deviations are named per case in a
`KNOWN_MISMATCH` registry with reasons, never tolerated fuzzily.

## File Layout

```
rdfs/                              # RDFS sources (committed snapshots)
|-- README.md                      # provenance, onboarding, upstream defects
|-- ENTSOE_NC_2.4.1/               # fetched: *.rdf + SOURCE.json commit pin
|-- ENTSOE_CGMES_2.4.15/           # legacy hand-collected
|-- ENTSOE_CGMES_3.0.0/            # legacy hand-collected
'-- ENTSOE_FH/                     # header profiles for the CGMES bundles

triplets/rdfs_tools/
|-- fetch_profiles.py              # SOURCES registry, upstream snapshot fetch
|-- cim_rdfs_to_json.py            # BUNDLES registry, build_bundle(), converters
'-- rdfs_tools.py                  # RDFS query helpers (lossless parse, stereotypes)

triplets/export_schema/
|-- __init__.py                    # schemas object: filename -> attribute
'-- ENTSOE_*_552_ED{1,2}.json      # the generated bundles
```

## Usage

```python
import pandas
import triplets
from triplets.export_schema import schemas

data = pandas.read_RDF(["nc_instances.zip"])

# exporters take a bundle as rdf_map (Path or dict)
files = data.export_to_cimxml(rdf_map=schemas.ENTSOE_NC_2_4_1_552_ED1, export_to_memory=True)
# N-Quads is edition-independent (always absolute urn:uuid: IRIs) — see the note below
data.export_to_nquads("nc.nq", rdf_map=schemas.ENTSOE_NC_2_4_1_552_ED2)

# validation context enrichment uses the same bundles
report = data.shacl.validate(shapes, context=True, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)

# a bundle is plain JSON — inspect it directly
import json
schema = json.loads(schemas.ENTSOE_NC_2_4_1_552_ED1.read_text())
print(schema["AE"]["ProfileMetadata"]["versionInfo"])     # profile version
print(schema["AE"]["ProfileMetadata"]["header"])          # injected header identity
```
