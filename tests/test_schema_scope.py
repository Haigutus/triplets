"""rdf_map_scope: profile resolution, the merged schema and the class inheritance map."""
import pytest

from triplets.export_schema import schemas
from triplets.iri import TRIPLETS_NS, datatypes, load_rdf_map
from triplets.validation.schema_scope import (SCOPE_KEY, ancestor_pairs, ancestor_triples, class_ancestors,
                                              scope_sections, scoped_rdf_map)

CIM, NC, CIM_NC = "http://iec.ch/TC57/CIM100#", "https://cim4.eu/ns/nc#", "https://cim.ucaiug.io/ns#"

SCHEMA = {
    "EQ": {"ProfileMetadata": {"keyword": "EQ", "versionIRI": "http://example.org/EQ/1"},
           "Breaker": {"type": "Class", "namespace": CIM, "parameters": ["IdentifiedObject.name"],
                       "inheritance": [CIM + "Breaker", CIM + "Switch"]},
           "IdentifiedObject.name": {"type": "Attribute", "xsd:type": "xsd:string", "multiplicity": "1..1",
                                     "xsd:minOccours": "1", "xsd:maxOccours": "1"},
           "Switch.kind": {"type": "Enumeration", "values": ["SwitchKind.a"]}},
    "OP": {"ProfileMetadata": {"keyword": "OP", "conformsTo": "urn:shared"},
           "Breaker": {"type": "Class", "namespace": CIM, "parameters": ["Switch.kind"],
                       "inheritance": [CIM + "Breaker", CIM + "Equipment"]},
           "IdentifiedObject.name": {"type": "Attribute", "xsd:type": "xsd:integer", "multiplicity": "0..n",
                                     "xsd:minOccours": "0", "xsd:maxOccours": "n"},
           "Switch.kind": {"type": "Enumeration", "values": ["SwitchKind.b"]}},
    "SC": {"ProfileMetadata": {"keyword": "SC", "conformsTo": "urn:shared"}},
}


def test_scope_resolves_keys_and_declared_identifiers_in_order():
    assert scope_sections(SCHEMA, ["OP", "http://example.org/EQ/1"]) == ["OP", "EQ"]
    assert scope_sections(SCHEMA, "EQ") == ["EQ"]
    assert scope_sections(SCHEMA, "*") == ["EQ", "OP", "SC"]
    assert scope_sections(SCHEMA, None) is None


@pytest.mark.parametrize("rdf_map_scope, match", [
    (["XX"], "unknown"), (["urn:shared"], "several profiles"), (["*", "EQ"], "every profile")])
def test_scope_errors(rdf_map_scope, match):
    with pytest.raises(ValueError, match=match):
        scope_sections(SCHEMA, rdf_map_scope)


def test_one_profile_is_copied_unchanged():
    scoped, conflicts = scoped_rdf_map(SCHEMA, ["EQ"])
    assert list(scoped) == [SCOPE_KEY] and conflicts == []
    assert scoped[SCOPE_KEY]["IdentifiedObject.name"] == SCHEMA["EQ"]["IdentifiedObject.name"]
    assert scoped[SCOPE_KEY]["ProfileMetadata"] == {"sections": ["EQ"], "keyword": ["EQ"],
                                                    "versionIRI": ["http://example.org/EQ/1"]}


def test_merged_profile_metadata_keeps_every_identity():
    """Each field becomes a list in scope order, aligned with the section keys."""
    metadata = scoped_rdf_map(SCHEMA, ["OP", "EQ"])[0][SCOPE_KEY]["ProfileMetadata"]
    assert metadata == {"sections": ["OP", "EQ"], "keyword": ["OP", "EQ"],
                        "conformsTo": ["urn:shared", None], "versionIRI": [None, "http://example.org/EQ/1"]}


def test_merge_rules():
    scoped, conflicts = scoped_rdf_map(SCHEMA, ["EQ", "OP"])
    merged = scoped[SCOPE_KEY]
    name = merged["IdentifiedObject.name"]
    assert (name["xsd:minOccours"], name["xsd:maxOccours"], name["multiplicity"]) == ("0", "n", "0..n")
    assert "xsd:type" not in name                        # conflicting datatype: left untyped
    assert "IdentifiedObject.name" not in datatypes(scoped)
    assert merged["Breaker"]["parameters"] == ["IdentifiedObject.name", "Switch.kind"]
    assert merged["Breaker"]["inheritance"] == [CIM + "Breaker", CIM + "Switch", CIM + "Equipment"]
    assert merged["Switch.kind"]["values"] == ["SwitchKind.a", "SwitchKind.b"]
    assert conflicts == ["IdentifiedObject.name: cardinality relaxed to 0..n (EQ: 1..1, OP: 0..n)",
                         "IdentifiedObject.name: datatype conflict (EQ: xsd:string, OP: xsd:integer) — not checked",
                         "Switch.kind: enumeration values united (EQ + OP)"]
    # merge order does not change the relaxed cardinality
    other = scoped_rdf_map(SCHEMA, ["OP", "EQ"])[0][SCOPE_KEY]["IdentifiedObject.name"]
    assert (other["xsd:minOccours"], other["xsd:maxOccours"]) == ("0", "n")


def test_root_entries():
    flat = {"Breaker": {"type": "Class", "inheritance": ["Breaker", "Switch"]}, "EQ": SCHEMA["EQ"]}
    scoped, _ = scoped_rdf_map(flat, None)
    assert list(scoped[SCOPE_KEY]) == ["Breaker"]                 # root: no ProfileMetadata
    assert scoped_rdf_map(SCHEMA, None)[0] == {SCOPE_KEY: {}}


@pytest.mark.parametrize("bundle, datatype_conflicts", [
    ("ENTSOE_CGMES_2_4_15_552_ED2", ["Model.version"]), ("ENTSOE_CGMES_3_0_0_552_ED2", []),
    ("ENTSOE_NC_2_4_1_552_ED2", []), ("ENTSOE_NC_2_5_0_552_ED2", [])])
def test_shipped_bundles_merge_with_one_datatype_conflict(bundle, datatype_conflicts):
    """Merging every section of a shipped bundle: only CGMES 2.4.15 FH Model.version
    (integer) clashes with the other profiles (string)."""
    _, conflicts = scoped_rdf_map(load_rdf_map(getattr(schemas, bundle)), "*")
    assert [entry.split(":")[0] for entry in conflicts if "datatype conflict" in entry] == datatype_conflicts


SCOPED = {
    "SSI": {"ACLineSegment": {"type": "Class", "namespace": CIM_NC,
                              "inheritance": [CIM_NC + "ACLineSegment", CIM_NC + "Equipment"]}},
    "EQ": {"Breaker": {"type": "Class", "namespace": CIM_NC,
                       "inheritance": [CIM_NC + name for name in ("Breaker", "Equipment", "IdentifiedObject")]}},
    "ER": {"Equipment": {"type": "Class", "namespace": CIM_NC,
                         "inheritance": [CIM_NC + "Equipment", CIM_NC + "PowerSystemResource"]},
           "PowerSystemResource": {"type": "Class", "namespace": CIM_NC,
                                   "inheritance": [CIM_NC + "PowerSystemResource", CIM_NC + "IdentifiedObject"]}},
}


def test_class_ancestors_close_only_through_own_entries():
    assert class_ancestors(SCOPED, ["SSI", "EQ"])["ACLineSegment"] == {CIM_NC + "Equipment"}
    assert class_ancestors(SCOPED, ["SSI", "ER"])["ACLineSegment"] == {
        CIM_NC + "Equipment", CIM_NC + "PowerSystemResource", CIM_NC + "IdentifiedObject"}
    assert class_ancestors(SCOPED, ["SSI"]) == {"ACLineSegment": {CIM_NC + "Equipment"}}
    assert class_ancestors({}, "*") == {}


def test_shipped_nc_ssi_reaches_identified_object_only_with_er():
    nc = load_rdf_map(schemas.ENTSOE_NC_2_5_0_552_ED2)
    ancestors = {step.split("#")[-1] for step in class_ancestors(nc, ["SSI"])["ACLineSegment"]}
    assert ancestors == {"Conductor", "ConductingEquipment", "Equipment"}
    with_er = {step.split("#")[-1] for step in class_ancestors(nc, ["SSI", "ER"])["ACLineSegment"]}
    assert {"PowerSystemResource", "IdentifiedObject"} <= with_er


def test_projections_agree():
    """Vectorized pairs and pyshacl triples are the same edges, local vs absolute."""
    ancestors = class_ancestors(SCOPED, ["SSI", "ER"])
    pairs = ancestor_pairs(ancestors)
    scoped = scoped_rdf_map(SCOPED, ["SSI", "ER"])[0]
    triples = ancestor_triples(ancestors, scoped, TRIPLETS_NS)
    assert [(child.split("#")[-1], parent.split("#")[-1]) for child, parent in triples] == pairs
    assert (CIM_NC + "ACLineSegment", CIM_NC + "IdentifiedObject") in triples


def test_triples_resolve_bare_names_like_the_data_graph():
    schema = {"EQ": {"Breaker": {"type": "Class", "namespace": CIM, "inheritance": ["Breaker", "Equipment"]}}}
    ancestors = class_ancestors(schema, "*")
    assert ancestor_triples(ancestors, schema, TRIPLETS_NS) == [(CIM + "Breaker", TRIPLETS_NS + "Equipment")]
    assert ancestor_triples(ancestors, schema, CIM) == [(CIM + "Breaker", CIM + "Equipment")]


def test_shipped_bundle_parents_are_absolute():
    nc = load_rdf_map(schemas.ENTSOE_NC_2_5_0_552_ED2)
    triples = ancestor_triples(class_ancestors(nc, "*"), scoped_rdf_map(nc, "*")[0], TRIPLETS_NS)
    assert triples and not any(TRIPLETS_NS in iri for pair in triples for iri in pair)
