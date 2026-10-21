"""Schema validation by instance header (triplets.cgmes_tools.declared_profiles /
validate_by_header) — the CIM orchestration on top of the header-free core."""
import json

import pandas
import pytest

from triplets import cgmes_tools

ENGINES = ["pandas", "polars", "duckdb"]
CIM = "http://iec.ch/TC57/CIM100#"


@pytest.fixture(params=ENGINES)
def engine(request):
    if request.param != "pandas":
        pytest.importorskip(request.param)
    return request.param


def attribute(low, high):
    return {"type": "Attribute", "multiplicity": f"{low}..{high}", "xsd:minOccours": low, "xsd:maxOccours": high,
            "xsd:type": "xsd:string"}


SCHEMA = {
    "EQ": {"ProfileMetadata": {"keyword": "EQ", "versionIRI": "http://example.org/CoreEquipment-EU/3.0",
                               "conformsTo": "http://example.org/profile/EQ"},
           "Breaker": {"type": "Class", "namespace": CIM, "inheritance": [CIM + "Breaker"],
                       "parameters": ["IdentifiedObject.mRID", "IdentifiedObject.name", "Equipment.EquipmentContainer"]},
           "VoltageLevel": {"type": "Class", "namespace": CIM, "inheritance": [CIM + "VoltageLevel"], "parameters": []},
           "IdentifiedObject.mRID": attribute("1", "1"), "IdentifiedObject.name": attribute("1", "1"),
           "Equipment.EquipmentContainer": {"type": "Association", "xsd:minOccours": "0", "xsd:maxOccours": "1",
                                            "range": CIM + "VoltageLevel"}},
    "SSH": {"ProfileMetadata": {"keyword": "SSH", "conformsTo": "http://example.org/profile/SSH"},
            "Breaker": {"type": "Class", "namespace": CIM, "inheritance": [CIM + "Breaker"],
                        "parameters": ["IdentifiedObject.mRID", "Switch.open"]},
            "IdentifiedObject.mRID": attribute("1", "1"), "Switch.open": attribute("1", "1")},
}


def frame(rows):
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def breaker(object_id, *properties, instance="eq"):
    return [(object_id, "Type", "Breaker", instance)] + [(object_id, key, value, instance) for key, value in properties]


OK = (("IdentifiedObject.mRID", "b1"), ("IdentifiedObject.name", "B1"))


def profiles(violations):
    return {tuple(profile) for profile in violations["PROFILE"]}


@pytest.mark.parametrize("header_key, value", [
    ("conformsTo", "http://example.org/profile/EQ"), ("Model.profile", "http://example.org/CoreEquipment-EU/3.0"),
    ("keyword", "EQ"), ("Model.profile", "http://entsoe.eu/CIM/EquipmentCore/3/1")],
    ids=["conformsTo", "versionIRI", "keyword", "legacy-url"])
def test_header_fields_resolve_to_the_profile(engine, header_key, value):
    rows = [("h", header_key, value, "eq")] + breaker("b1")
    assert cgmes_tools.declared_profiles(frame(rows), SCHEMA) == {"eq": ["EQ"]}
    v = cgmes_tools.validate_by_header(frame(rows), SCHEMA, engine=engine)
    assert profiles(v) == {("EQ",)} and ("b1", "IdentifiedObject.name") in set(zip(v["ID"], v["KEY"]))


def test_each_instance_against_its_own_profiles(engine):
    """EQ and SSH serialize mRID each: no cross-instance doubling, and each
    instance's own profile requirement is checked in that instance."""
    rows = ([("h1", "keyword", "EQ", "eq"), ("h2", "keyword", "SSH", "ssh")]
            + breaker("b1", *OK) + breaker("b1", ("Switch.open", "true"), instance="ssh"))
    v = cgmes_tools.validate_by_header(frame(rows), SCHEMA, engine=engine)
    assert set(zip(v["ID"], v["KEY"], v["PROFILE"].map(tuple))) == {("b1", "IdentifiedObject.mRID", ("SSH",))}


def test_instance_declaring_two_profiles_merges_them(engine):
    rows = ([("h", "conformsTo", "http://example.org/profile/EQ", "one"),
             ("h", "conformsTo", "http://example.org/profile/SSH", "one")] + breaker("b1", *OK, instance="one"))
    v = cgmes_tools.validate_by_header(frame(rows), SCHEMA, engine=engine)
    assert set(zip(v["KEY"], v["PROFILE"].map(tuple))) == {("Switch.open", ("EQ", "SSH"))}


def test_unresolved_instance_is_skipped_with_a_note(engine):
    rows = breaker("b1")                                       # no header at all
    v = cgmes_tools.validate_by_header(frame(rows), SCHEMA, engine=engine)
    assert len(v) == 0
    assert any("no schema profile matched" in note for note in v.attrs["validation"]["skipped_shapes"])
    forced = cgmes_tools.validate_by_header(frame(rows), SCHEMA, rdf_map_scope=["EQ"], engine=engine)
    assert len(forced) > 0


def test_shared_identifier_identifies_no_profile():
    """An identifier several profiles share (the IEC document URN in the shipped
    schemas) never matches the wrong profile."""
    shared = {name: {**section, "ProfileMetadata": {**section["ProfileMetadata"], "conformsTo": "urn:iso:std:iec:61970-501"}}
              for name, section in SCHEMA.items()}
    rows = [("h", "conformsTo", "urn:iso:std:iec:61970-501", "eq")] + breaker("b1")
    assert cgmes_tools.declared_profiles(frame(rows), shared) == {"eq": []}


def test_legacy_url_fallback_eq_operation_short_circuit():
    """A 2.4 EQ file may declare only the Operation/ShortCircuit profile URLs."""
    rows = ([("h", "Model.profile", "http://entsoe.eu/CIM/EquipmentOperation/3/1", "eq"),
             ("h", "Model.profile", "http://entsoe.eu/CIM/EquipmentShortCircuit/3/1", "eq")] + breaker("b1"))
    assert cgmes_tools.declared_profiles(frame(rows), SCHEMA) == {"eq": ["EQ"]}


def test_legacy_url_map_covers_all_24_sections():
    from triplets._header import PROFILE_URL_MAP
    from triplets.export_schema import schemas

    with open(schemas.ENTSOE_CGMES_2_4_15_552_ED2) as file:
        sections = {name for name, entry in json.load(file).items() if isinstance(entry, dict)}
    assert sections <= set(PROFILE_URL_MAP.values())


def test_references_against_the_other_loaded_instances(engine):
    rows = ([("h1", "keyword", "EQ", "eq"), ("h2", "keyword", "SSH", "ssh")]
            + breaker("b1", *OK, ("Equipment.EquipmentContainer", "vl1")) + breaker("b2", *OK, ("Equipment.EquipmentContainer", "b1"))
            + [("vl1", "Type", "VoltageLevel", "ssh")] + breaker("b1", ("IdentifiedObject.mRID", "b1"), ("Switch.open", "true"),
                                                          instance="ssh"))
    v = cgmes_tools.validate_by_header(frame(rows), SCHEMA, references=True, engine=engine)
    assert set(zip(v["ID"], v["VIOLATION_TYPE"], v["VALUE"])) == {("b2", "rdfs:range", "b1")}


def test_data_scope_selects_sources_but_references_see_every_instance(engine):
    """data_scope=["ssh"]: only the SSH instance is validated, its reference into
    the EQ instance resolves there — no false triplets:missing."""
    rows = ([("h1", "keyword", "EQ", "eq"), ("h2", "keyword", "SSH", "ssh"), ("vl1", "Type", "VoltageLevel", "eq")]
            + breaker("b1", *OK, ("Equipment.EquipmentContainer", "vl1"))
            + breaker("b2", ("IdentifiedObject.mRID", "b2"), ("Switch.open", "true"),
                      ("Equipment.EquipmentContainer", "vl1"), instance="ssh"))
    schema = {**SCHEMA, "SSH": {**SCHEMA["SSH"], "Breaker": {**SCHEMA["SSH"]["Breaker"], "parameters": [
        *SCHEMA["SSH"]["Breaker"]["parameters"], "Equipment.EquipmentContainer"]},
        "Equipment.EquipmentContainer": SCHEMA["EQ"]["Equipment.EquipmentContainer"],
        "VoltageLevel": SCHEMA["EQ"]["VoltageLevel"]}}
    v = cgmes_tools.validate_by_header(frame(rows), schema, data_scope=["ssh"], references=True, engine=engine)
    assert v.empty
    assert v.attrs["validation"]["profiles"] == ["SSH"]


def test_schema_and_reference_runs_count_their_own_rules(engine):
    """concat_violations counts constraints once per compiled rule set — a schema
    run and a reference run on the same profiles both count."""
    from triplets import validation
    data = frame([("h1", "keyword", "EQ", "eq")] + breaker("b1", *OK))
    schema_run = validation.validate_schema(data, SCHEMA, rdf_map_scope=["EQ"], engine=engine)
    reference_run = validation.validate_schema_references(data, SCHEMA, ["eq"], ["EQ"], engine=engine)
    both = validation.concat_violations([schema_run, reference_run])
    assert both.attrs["validation"]["constraints"] == (schema_run.attrs["validation"]["constraints"]
                                                       + reference_run.attrs["validation"]["constraints"])
    again = validation.concat_violations([schema_run, schema_run])
    assert again.attrs["validation"]["constraints"] == schema_run.attrs["validation"]["constraints"]


def test_duckdb_connection_with_configured_table():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(table="grid", schema="cim")
    con.execute("CREATE SCHEMA cim")
    con.register("_src", frame([("h", "keyword", "EQ", "eq")] + breaker("b1")))
    con.execute("CREATE TABLE cim.grid AS SELECT * FROM _src")
    assert cgmes_tools.declared_profiles(con, SCHEMA) == {"eq": ["EQ"]}
    v = cgmes_tools.validate_by_header(con, SCHEMA, engine="duckdb")
    assert profiles(v) == {("EQ",)}


@pytest.mark.performance
def test_real_data_resolves_its_profile():
    """Svedala EQ auto-resolves its CoreEquipment profile from the header — engines agree."""
    from pathlib import Path

    from _parity import SVEDALA_DIR, SKIP_REASON
    from triplets.export_schema import schemas

    eq = SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"
    if not Path(eq).exists():
        pytest.skip(SKIP_REASON)
    data = pandas.read_RDF([str(eq)])
    results = {}
    for name in ENGINES:
        if name != "pandas":
            pytest.importorskip(name)
        v = cgmes_tools.validate_by_header(data, schemas.ENTSOE_CGMES_3_0_0_552_ED1, engine=name)
        assert profiles(v) <= {("EQ",)} and v.attrs["validation"]["profiles"] == ["EQ"]
        results[name] = set(zip(v["ID"], v["KEY"].astype(str), v["VIOLATION_TYPE"]))
    assert results["pandas"] == results["polars"] == results["duckdb"]
