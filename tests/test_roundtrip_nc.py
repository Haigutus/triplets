"""Roundtrip of ENTSO-E ReliCapGrid NC instances through the versioned NCP schema bundles.

Every (TSO, profile keyword) instance of the ``cgmes-3.0_ncp-2.5_tc-2.0`` release is
parsed, exported via each NCP 2.5.0 bundle serialization and re-parsed — the result
must equal the source exactly (the parser normalises ED1/ED2 identifier forms, so
strict comparison holds for both).

Profile selection is identity-based (``resolve_instance_config``): instances carry
``dcat:keyword`` and ``dcterms:conformsTo`` on host ``ap.cim4.eu`` while the schema
versionIRIs use ``ap-voc.cim4.eu`` — resolution works via keyword only, pinned below.

Known deviations are listed, not skipped: ``EXPECTED_FAILURES`` (strict xfail) and
``KNOWN_MISMATCH`` (exact dropped keys) both fail on the data or triplets fix that
resolves them, so a resolved entry cannot stay behind.
"""
import re
from pathlib import Path

import pandas
import pytest

import triplets  # noqa: F401
from triplets import export
from triplets.export.cimxml_utils import _profile_identity_index, load_rdf_map, resolve_instance_config
from triplets.export_schema import schemas

NC_DIR = Path("test_data/relicapgrid/Instance")
SKIP_REASON = "ReliCapGrid NC test data not available (needs git submodule)"

# First dcat:keyword → why its instance cannot roundtrip yet (strict xfail)
EXPECTED_FAILURES = {
    # conformsTo declares the profile IRI, the schema has only the vocabulary IRI
    "CommonData": "https://github.com/Haigutus/triplets/issues/142",
    "FAP": "https://github.com/Haigutus/triplets/issues/142",
    # Provenance prototype, no NCP profile (Instance/Svedala/NetworkCode/manifest.ttl:182)
    "PV": "https://github.com/entsoe/relicapgrid/issues/30",
}

# Header rows must roundtrip (dcat:Dataset), only parser-injected meta rows are stripped
_META_TYPES = {"Distribution", "NamespaceMap"}

ROUNDTRIP_MAPS = {
    "2.5.0-ED1": schemas.ENTSOE_NC_2_5_0_552_ED1,
    "2.5.0-ED2": schemas.ENTSOE_NC_2_5_0_552_ED2,
}

RESOLUTION_BUNDLES = ["ENTSOE_NC_2_5_0_552_ED1", "ENTSOE_NC_2_5_0_552_ED2"]

# Keys of rows the NCP 2.5.0 profiles do not define; export drops them.
# ER: concrete EQ classes (ACLineSegment, RatioTapChanger, ...) that the ER RDFS declares only
# as Description / abstract or not at all, so the whole object ("Type" too) is dropped; APL
# follows up on the RDFS, https://github.com/entsoe/relicapgrid/issues/340
KNOWN_MISMATCH = {
    ("Belgovia", "ER"): {"Type", "Equipment.Circuit"},
    ("Espheim", "ER"): {"Type", "TapChanger.TapChangerController"},
    ("Jotunheim", "SAR"): {"IdentifiedObject.mRID"},  # https://github.com/entsoe/relicapgrid/issues/400
    ("Svedala", "ER"): {"Type", "RegulatingCondEq.EquipmentController", "TapChanger.TapChangerController"},
}


def _keyword(path):
    match = re.search(r"<dcat:keyword>([^<]+)</dcat:keyword>", path.read_text()[:4000])
    return match.group(1) if match else None


def _nc_instances():
    """One instance file per (TSO, keyword); plain-named files preferred over variants."""
    instances = {}
    paths = [*NC_DIR.glob("*/NetworkCode/*.xml"), *NC_DIR.glob("*/NetworkCode/cimxml/*.xml")]
    for path in sorted(paths, key=lambda p: (len(p.name), p.name)):
        keyword = _keyword(path)
        if keyword:
            instances.setdefault((path.relative_to(NC_DIR).parts[0], keyword), path)
    return instances


NC_PARAMS = [pytest.param(path, tso, keyword, id=f"{tso}-{keyword}",
                          marks=pytest.mark.xfail(strict=True, reason=EXPECTED_FAILURES[keyword])
                          if keyword in EXPECTED_FAILURES else ())
             for (tso, keyword), path in sorted(_nc_instances().items())]

if not NC_PARAMS:
    pytest.skip(SKIP_REASON, allow_module_level=True)

_stale = set(EXPECTED_FAILURES) - {keyword for _, _, keyword in (param.values for param in NC_PARAMS)}
assert not _stale, f"EXPECTED_FAILURES keywords no instance has: {_stale}"


def _canon(data):
    """Comparable set of (ID, KEY, VALUE) rows without parser-injected meta objects."""
    frame = data[["ID", "KEY", "VALUE"]]
    meta_ids = set(frame[(frame["KEY"] == "Type") & (frame["VALUE"].isin(_META_TYPES))]["ID"])
    return set(map(tuple, frame[~frame["ID"].isin(meta_ids)].values))


def _roundtrip(data, rdf_map):
    outputs = export.export_to_cimxml(data.copy(), rdf_map=rdf_map, export_to_memory=True)
    return _canon(pandas.read_RDF(outputs))


def _assert_roundtrip(path, rdf_map, expected_dropped_keys):
    """Roundtrip must be exact apart from the named, explained key drops."""
    data = pandas.read_RDF([str(path)])
    source, result = _canon(data), _roundtrip(data, rdf_map)
    assert not result - source, f"export invented rows: {sorted(result - source)[:5]}"
    dropped_keys = {key for _, key, _ in source - result}
    assert dropped_keys == expected_dropped_keys


@pytest.mark.parametrize("serialization", list(ROUNDTRIP_MAPS))
@pytest.mark.parametrize("path,tso,keyword", NC_PARAMS)
def test_roundtrip_ncp_2_5_0(path, tso, keyword, serialization):
    _assert_roundtrip(path, ROUNDTRIP_MAPS[serialization], KNOWN_MISMATCH.get((tso, keyword), set()))


@pytest.mark.parametrize("bundle", RESOLUTION_BUNDLES)
@pytest.mark.parametrize("path,tso,keyword", NC_PARAMS)
def test_profile_resolution(path, tso, keyword, bundle):
    """Each instance resolves to the schema section matching its dcat:keyword."""
    data = pandas.read_RDF([str(path)])
    _, _, section = resolve_instance_config(data, load_rdf_map(getattr(schemas, bundle)))
    assert section.get("ProfileMetadata", {}).get("keyword") == keyword


def test_conforms_to_alone_does_not_match():
    """Instances declare conformsTo on ap.cim4.eu, schema versionIRIs use ap-voc.cim4.eu —
    identity matching relies on the keyword, the conformsTo URI finds no section."""
    index = _profile_identity_index(load_rdf_map(schemas.ENTSOE_NC_2_5_0_552_ED1))
    assert "AE" in index
    assert "https://ap.cim4.eu/AssessedElement/2.4" not in index
