"""Roundtrip of ENTSO-E ReliCapGrid NC instances through the versioned NCP schema bundles.

Every (TSO, profile keyword) instance is parsed, exported via each NCP 2.4.1 bundle
serialization and re-parsed — the result must equal the source exactly (the parser
normalises ED1/ED2 identifier forms, so strict comparison holds for both).

Only NCP 2.4.1 — the latest official publication — is onboarded. 2.4.2 and the 2.5
draft dropped ``rdfs:domain`` from 11 dcat:Dataset header properties (conformsTo,
publisher, license, ...), which makes them unusable for schema-driven export; see
https://github.com/entsoe/application-profiles-library/issues/92 and rdfs/README.md.

Profile selection is identity-based (``resolve_instance_config``): instances carry
``dcat:keyword`` and ``dcterms:conformsTo`` on host ``ap.cim4.eu`` while the schema
versionIRIs use ``ap-voc.cim4.eu`` — resolution works via keyword only, pinned below.
"""
import re
from pathlib import Path

import pandas
import pytest

import triplets  # noqa: F401
from triplets import export
from triplets.export.cimxml_utils import _profile_identity_index, load_rdf_map, resolve_instance_config
from triplets.export_schema import schemas

NC_DIR = Path("test_data/relicapgrid/Instance/NetworkCode")
SKIP_REASON = "ReliCapGrid NC test data not available (needs git submodule)"

EXCLUDE_KEYWORDS = {"FAP", "PV", "CD", "MO"}  # present in ReliCapGrid but not NCP profiles

# Header rows must roundtrip (dcat:Dataset), only parser-injected meta rows are stripped
_META_TYPES = {"Distribution", "NamespaceMap"}

ROUNDTRIP_MAPS = {
    "2.4.1-ED1": schemas.ENTSOE_NC_2_4_1_552_ED1,
    "2.4.1-ED2": schemas.ENTSOE_NC_2_4_1_552_ED2,
}

RESOLUTION_BUNDLES = ["ENTSOE_NC_2_4_1_552_ED1", "ENTSOE_NC_2_4_1_552_ED2"]

# ReliCapGrid instances that do not conform to the released NCP 2.4.x profiles: rows with
# these keys are dropped on export because the released RDFS has no such attribute
# (instances authored against drafts) or declares the association AssociationUsed=No
# (instance serializes the non-navigable direction). To be reported to entsoe/relicapgrid.
KNOWN_MISMATCH = {
    ("Belgovia", "RA"): {"CountertradeRemedialAction.gLSKStrategy",
                         "CountertradeRemedialAction.shiftMethod",
                         "GateInputPin.normalEnabled"},           # not in released RA profile
    ("Belgovia", "SIS"): {"PowerBidDependency.MainPowerBidSchedule",
                          "PowerBidDependency.delay",
                          "PowerBidScheduleTimePoint.PowerShiftKeySchedule"},  # not in released SIS profile
    ("Espheim", "ER"): {"Equipment.inService",
                        "EquivalentGeneratingUnit.ExternalNetworkInjection",
                        "GeneratingUnit.isRamping"},              # not in released ER profile
    ("Jotunheim", "SAR"): {"IdentifiedObject.mRID"},              # cim:IdentifiedObject objects not in SAR profile
    ("Svedala", "AE"): {"AssessedElement.CrossBorderRelevance"},  # AssociationUsed=No direction
    ("Svedala", "RA"): {"RemedialAction.ContingencyWithRemedialAction"},  # AssociationUsed=No direction
}


def _keyword(path):
    match = re.search(r"<dcat:keyword>([^<]+)</dcat:keyword>", path.read_text()[:4000])
    return match.group(1) if match else None


def _nc_instances():
    """One instance file per (TSO, keyword); plain-named files preferred over variants."""
    instances = {}
    for path in sorted(NC_DIR.glob("*/*_instance/*.xml"), key=lambda p: (len(p.name), p.name)):
        keyword = _keyword(path)
        if keyword and keyword not in EXCLUDE_KEYWORDS:
            instances.setdefault((path.parent.parent.name, keyword), path)
    return instances


NC_PARAMS = [pytest.param(path, tso, keyword, id=f"{tso}-{keyword}")
             for (tso, keyword), path in sorted(_nc_instances().items())]

if not NC_PARAMS:
    pytest.skip(SKIP_REASON, allow_module_level=True)


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
def test_roundtrip_ncp_2_4_1(path, tso, keyword, serialization):
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
    index = _profile_identity_index(load_rdf_map(schemas.ENTSOE_NC_2_4_1_552_ED1))
    assert "AE" in index
    assert "https://ap.cim4.eu/AssessedElement/2.4" not in index
