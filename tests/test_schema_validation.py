"""Schema-based validation (triplets.validation.compile_schema/validate_schema):
cardinality, datatypes, enumerations and association ranges straight from the
export schema, run by the same vectorized engines as SHACL — presented with
vocabulary-accurate rdfs:/xsd:/schema: types, not fake SHACL.

Semantics under test: validation runs per INSTANCE_ID, per profile the
instance declares — profiles are never merged."""
import pandas
import pytest

import triplets
from triplets.validation import compile_schema, validate_schema

ENGINES = ["pandas", "polars", "duckdb"]


@pytest.fixture(params=ENGINES)
def engine(request):
    if request.param != "pandas":
        pytest.importorskip(request.param)
    return request.param


EQ_SECTION = {
    "ProfileMetadata": {"Type": "Description", "keyword": "EQ",
                        "versionIRI": "http://example.org/CoreEquipment-EU/3.0",
                        "conformsTo": "http://example.org/profile/EQ"},
    "Breaker": {"type": "Class", "description": "A breaker.",
                "namespace": "http://iec.ch/TC57/CIM100#",
                "inheritance": ["#Breaker", "#Switch", "#Equipment"],
                "parameters": ["IdentifiedObject.mRID", "IdentifiedObject.name",
                               "Switch.retained", "Equipment.EquipmentContainer",
                               "Breaker.kind"]},
    "VoltageLevel": {"type": "Class", "description": "A voltage level.",
                     "namespace": "http://iec.ch/TC57/CIM100#",
                     "inheritance": ["#VoltageLevel", "#EquipmentContainer"], "parameters": []},
    "Bay": {"type": "Class", "description": "A bay.", "namespace": "http://iec.ch/TC57/CIM100#",
            "inheritance": ["#Bay", "#EquipmentContainer"], "parameters": []},
    "Substation": {"type": "Class", "description": "A substation.",
                   "namespace": "http://iec.ch/TC57/CIM100#",
                   "inheritance": ["#Substation"], "parameters": []},
    "IdentifiedObject.mRID": {"type": "Attribute", "multiplicity": "1..1",
                              "xsd:minOccours": "1", "xsd:maxOccours": "1",
                              "xsd:type": "xsd:string", "description": "Master RID."},
    "IdentifiedObject.name": {"type": "Attribute", "multiplicity": "1..1",
                              "xsd:minOccours": "1", "xsd:maxOccours": "1",
                              "xsd:type": "xsd:string", "description": "The name."},
    "Switch.retained": {"type": "Attribute", "multiplicity": "0..1",
                        "xsd:minOccours": "0", "xsd:maxOccours": "1",
                        "xsd:type": "xsd:boolean", "description": "Retained flag."},
    "Equipment.EquipmentContainer": {"type": "Association", "multiplicity": "1",
                                     "xsd:minOccours": "1", "xsd:maxOccours": "1",
                                     "range": "#EquipmentContainer",   # abstract — subclasses count
                                     "description": "Container of this equipment."},
    "Breaker.kind": {"type": "Enumeration", "multiplicity": "0..1",
                     "xsd:minOccours": "0", "xsd:maxOccours": "1", "range": "BreakerKind",
                     "values": ["BreakerKind.air", "BreakerKind.vacuum"],
                     "description": "Breaker kind."},
}

SSH_SECTION = {
    "ProfileMetadata": {"Type": "Description", "keyword": "SSH",
                        "versionIRI": "http://example.org/SteadyStateHypothesis-EU/3.0",
                        "conformsTo": "http://example.org/profile/SSH"},
    "Breaker": {"type": "Class", "description": "A breaker (SSH view).",
                "namespace": "http://iec.ch/TC57/CIM100#",
                "inheritance": ["#Breaker", "#Switch", "#Equipment"],
                "parameters": ["IdentifiedObject.mRID", "Switch.open"]},
    "IdentifiedObject.mRID": {"type": "Attribute", "multiplicity": "1..1",
                              "xsd:minOccours": "1", "xsd:maxOccours": "1",
                              "xsd:type": "xsd:string", "description": "Master RID."},
    "Switch.open": {"type": "Attribute", "multiplicity": "1..1",
                    "xsd:minOccours": "1", "xsd:maxOccours": "1",
                    "xsd:type": "xsd:boolean", "description": "Open state."},
}

SCHEMA = {"EQ": EQ_SECTION, "SSH": SSH_SECTION}

EQ_HEADER = [("h-eq", "Model.profile", "http://example.org/CoreEquipment-EU/3.0", "eq")]
SSH_HEADER = [("h-ssh", "keyword", "SSH", "ssh")]
CONTAINED = [("vl1", "Type", "VoltageLevel", "eq")]
OK_PROPS = (("IdentifiedObject.mRID", "b"), ("IdentifiedObject.name", "B"),
            ("Equipment.EquipmentContainer", "vl1"))


def frame(rows):
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def run(rows, engine, **kwargs):
    """Single header-less EQ-profile instance — the common per-check harness."""
    kwargs.setdefault("profiles", ("EQ",))
    return validate_schema(frame(rows), SCHEMA, engine=engine, **kwargs)


def breaker(object_id, *properties, instance="eq"):
    return [(object_id, "Type", "Breaker", instance)] + [(object_id, key, value, instance)
                                                         for key, value in properties]


def violating(violations, violation_type):
    rows = violations[violations["VIOLATION_TYPE"] == violation_type]
    return set(zip(rows["ID"], rows["VALUE"].where(rows["VALUE"].notna(), None)))


# ── per-check semantics (single instance, explicit EQ profile) ───────────────

def test_min_occurs(engine):
    rows = breaker("b1", *OK_PROPS) + breaker("b2", ("IdentifiedObject.mRID", "b2"),
                                              ("Equipment.EquipmentContainer", "vl1")) + CONTAINED
    v = run(rows, engine)
    assert violating(v, "xsd:minOccurs") == {("b2", None)}      # name missing


def test_max_occurs(engine):
    rows = (breaker("b1", *OK_PROPS, ("IdentifiedObject.name", "B-again")) + CONTAINED)
    v = run(rows, engine)
    assert violating(v, "xsd:maxOccurs") == {("b1", None)}


def test_datatype_lexical_two_levels(engine):
    rows = (breaker("b1", *OK_PROPS, ("Switch.retained", "maybe"))     # invalid boolean
            + breaker("b2", ("IdentifiedObject.mRID", "b2"), ("IdentifiedObject.name", "B2"),
                      ("Equipment.EquipmentContainer", "vl1"),
                      ("Switch.retained", "1"))                        # valid, non-canonical
            + CONTAINED)
    v = run(rows, engine)
    assert violating(v, "xsd:type") == {("b1", "maybe")}
    assert violating(v, "triplets:lexicalForm") == {("b2", "1")}       # Warning, shared deviation
    assert (v.loc[v["VIOLATION_TYPE"] == "triplets:lexicalForm", "SEVERITY"] == "Warning").all()


def test_enumeration_membership(engine):
    rows = breaker("b1", *OK_PROPS, ("Breaker.kind", "BreakerKind.oil")) + CONTAINED
    v = run(rows, engine)
    assert violating(v, "rdfs:range") == {("b1", "BreakerKind.oil")}
    expected = v.loc[v["VIOLATION_TYPE"] == "rdfs:range", "EXPECTED"].iloc[0]
    assert expected == "one of: BreakerKind.air, BreakerKind.vacuum"


def test_association_range_abstract_expansion(engine):
    """The schema range #EquipmentContainer is abstract — Bay and VoltageLevel
    (its concrete subclasses via inheritance) pass, a Substation fails, and a
    dangling reference stays silent."""
    rows = (breaker("b1", ("IdentifiedObject.mRID", "b1"), ("IdentifiedObject.name", "B1"),
                    ("Equipment.EquipmentContainer", "bay1"))
            + breaker("b2", ("IdentifiedObject.mRID", "b2"), ("IdentifiedObject.name", "B2"),
                      ("Equipment.EquipmentContainer", "sub1"))
            + breaker("b3", ("IdentifiedObject.mRID", "b3"), ("IdentifiedObject.name", "B3"),
                      ("Equipment.EquipmentContainer", "ghost"))
            + [("bay1", "Type", "Bay", "eq"), ("sub1", "Type", "Substation", "eq"),
               ("sub1", "IdentifiedObject.name", "Main Sub", "eq")])
    v = run(rows, engine)
    assert violating(v, "rdfs:range") == {("b2", "sub1")}     # VALUE = the reference itself
    assert v.loc[v["ID"] == "b2", "TARGET"].iloc[0] \
        == 'referenced object sub1 found — Substation "Main Sub"'


def test_association_multi_typed_target_conforms(engine):
    """SSH/TP re-type EQ objects (issue #100): a target with several rdf:type
    values conforms when ANY of them is in the expanded range set."""
    rows = (breaker("b1", *OK_PROPS[:2], ("Equipment.EquipmentContainer", "bay1"))
            + [("bay1", "Type", "Bay", "eq"),
               ("bay1", "Type", "Equipment", "eq")])       # extra generic type
    v = run(rows, engine)
    assert violating(v, "rdfs:range") == set()


def test_closed_flag_reports_unknown_property(engine):
    """schema:domainIncludes, not rdfs:domain — external properties attach to
    classes non-exclusively (the APL rdfs:domain/domainIncludes convention)."""
    rows = breaker("b1", *OK_PROPS, ("Breaker.madeUp", "x")) + CONTAINED
    assert violating(run(rows, engine), "schema:domainIncludes") == set()   # default: off
    v = run(rows, engine, closed=True)
    assert set(v.loc[v["VIOLATION_TYPE"] == "schema:domainIncludes", "ID"]) == {"b1"}


# ── per-(instance, profile) orchestration ─────────────────────────────────────

def test_mrid_across_profiles_no_duplication(engine):
    """The case that motivated per-instance semantics: EQ and SSH both
    serialize mRID (1..1 each) — the union carries two mRID rows per object,
    but each (instance, profile) run counts only its own instance's rows, so
    no xsd:maxOccurs false-positive; and each profile's mRID requirement is
    checked against ITS instance (the #101 case done right)."""
    rows = (EQ_HEADER + SSH_HEADER + CONTAINED
            + breaker("b1", *OK_PROPS, instance="eq")
            + [("b1", "Type", "Breaker", "ssh"),
               ("b1", "IdentifiedObject.mRID", "b1", "ssh"),
               ("b1", "Switch.open", "true", "ssh")])
    v = validate_schema(frame(rows), SCHEMA, engine=engine)
    assert violating(v, "xsd:maxOccurs") == set()              # no cross-profile doubling

    without_ssh_mrid = [row for row in rows
                        if row[3] != "ssh" or row[1] != "IdentifiedObject.mRID"]
    v = validate_schema(frame(without_ssh_mrid), SCHEMA, engine=engine)
    missing = v[(v["VIOLATION_TYPE"] == "xsd:minOccurs") & (v["KEY"] == "IdentifiedObject.mRID")]
    assert set(zip(missing["ID"], missing["PROFILE"])) == {("b1", "SSH")}   # SSH's own rule


def test_resolution_by_header_fields(engine):
    """conformsTo (NC-style), Model.profile→versionIRI (CGMES3-style) and
    keyword all resolve through the schema's own declared identity."""
    for header_key, value in (("conformsTo", "http://example.org/profile/EQ"),
                              ("Model.profile", "http://example.org/CoreEquipment-EU/3.0"),
                              ("keyword", "EQ")):
        rows = [("h", header_key, value, "eq")] + breaker("b1") + CONTAINED
        v = validate_schema(frame(rows), SCHEMA, engine=engine)
        assert set(v["PROFILE"]) == {"EQ"}
        assert ("b1", None) in violating(v, "xsd:minOccurs")   # mRID + name missing


def test_resolution_legacy_url_fallback(engine):
    rows = [("h", "Model.profile",
             "http://entsoe.eu/CIM/EquipmentCore/3/1", "eq")] + breaker("b1") + CONTAINED
    v = validate_schema(frame(rows), SCHEMA, engine=engine)
    assert set(v["PROFILE"]) == {"EQ"}


def test_instance_declaring_two_profiles_runs_both(engine):
    """One instance file serializing several profiles: each declared profile
    is validated separately against the same instance — never merged."""
    rows = ([("h", "conformsTo", "http://example.org/profile/EQ", "one"),
             ("h", "conformsTo", "http://example.org/profile/SSH", "one")]
            + breaker("b1", *OK_PROPS, ("Switch.open", "true"), instance="one")
            + [("vl1", "Type", "VoltageLevel", "one")])
    v = validate_schema(frame(rows), SCHEMA, engine=engine)
    assert len(v) == 0                                         # conforms to both

    incomplete = [row for row in rows if row[1] != "Switch.open"]
    v = validate_schema(frame(incomplete), SCHEMA, engine=engine)
    assert set(zip(v["KEY"], v["PROFILE"])) == {("Switch.open", "SSH")}


def test_unresolved_instance_skipped_with_coverage_note(engine):
    rows = breaker("b1") + CONTAINED                           # no header at all
    v = validate_schema(frame(rows), SCHEMA, engine=engine)
    assert len(v) == 0
    notes = v.attrs["validation"]["skipped_shapes"]
    assert any("no schema profile matched" in note for note in notes)

    forced = validate_schema(frame(rows), SCHEMA, engine=engine, profiles=("EQ",))
    assert len(forced) > 0                                     # override validates it


def test_explicit_profiles_accept_any_identifier():
    rows = breaker("b1") + CONTAINED
    by_uri = validate_schema(frame(rows), SCHEMA, profiles=("http://example.org/profile/EQ",))
    by_key = validate_schema(frame(rows), SCHEMA, profiles=("EQ",))
    assert set(by_uri["PROFILE"]) == set(by_key["PROFILE"]) == {"EQ"}
    with pytest.raises(ValueError, match="unknown schema profile.*available"):
        validate_schema(frame(rows), SCHEMA, profiles=("NOPE",))


# ── compiled set, metadata, reports ──────────────────────────────────────────

def test_compiled_set_lookup_and_cache():
    compiled = compile_schema(SCHEMA)
    assert sorted(compiled.profiles) == ["EQ", "SSH"]
    assert compiled.get("EQ") is compiled.profiles["EQ"]
    assert compiled.get("http://example.org/profile/SSH") is compiled.profiles["SSH"]
    assert compiled.get("http://example.org/CoreEquipment-EU/3.0") is compiled.profiles["EQ"]
    assert compiled.get("unknown") is None
    assert compile_schema(SCHEMA) is compiled                  # cached by content
    eq = compiled.profiles["EQ"]
    assert eq.language == "rdfs" and eq.graph is None
    assert eq.stats["node_shapes"] == 1                        # only Breaker carries constraints


def test_run_metadata(engine):
    rows = EQ_HEADER + breaker("b1", *OK_PROPS) + CONTAINED
    v = validate_schema(frame(rows), SCHEMA, engine=engine)
    meta = v.attrs["validation"]
    assert meta["language"] == "rdfs"
    assert meta["profiles"] == ["EQ"]
    assert meta["references"] == ["rdf_map"]
    assert meta["source_shapes"] == {}
    assert meta["engine"] == engine


def test_unexpandable_range_lands_in_coverage():
    schema = {"EQ": {**EQ_SECTION,
                     "Equipment.EquipmentContainer": {
                         **EQ_SECTION["Equipment.EquipmentContainer"],
                         "range": "http://iec.ch/TC57/61970-552/ModelDescription/1#Model"}}}
    compiled = compile_schema(schema)
    assert any("Model" in entry for entry in compiled.stats["skipped_shapes"])
    assert any("Model" in entry for entry in compiled.profiles["EQ"].stats["skipped_shapes"])


def test_reports_carry_rdfs_tags_and_profile(engine):
    """Both formats present the rdfs family with the profile named in the
    heading and in the error itself."""
    import rdflib
    from triplets.validation.sarif import build_sarif

    rows = EQ_HEADER + breaker("b1", ("IdentifiedObject.mRID", "b1"),
                               ("Equipment.EquipmentContainer", "vl1")) + CONTAINED
    v = validate_schema(frame(rows), SCHEMA, engine=engine)
    run_sarif = build_sarif(v)["runs"][0]
    text = run_sarif["results"][0]["message"]["text"]
    assert "[engine_message] " in text and "[rdfs_expected] " in text
    assert "[rdfs_profile] EQ" in text and "[shacl" not in text
    names = {rule["name"] for rule in run_sarif["tool"]["driver"]["rules"]}
    assert "RDFS EQ Breaker IdentifiedObject.name (1×)" in names

    graph = rdflib.Graph().parse(
        data=v.shacl.to_shacl_report(export_to_memory=True).getvalue(), format="turtle")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    messages = {str(m) for m in graph.objects(None, sh.resultMessage)}
    assert "[rdfs_profile] EQ" in messages
    components = {str(c) for c in graph.objects(None, sh.sourceConstraintComponent)}
    assert "http://www.w3.org/2001/XMLSchema#minOccurs" in components


def test_closed_type_roundtrips_through_report():
    pytest.importorskip("rdflib")
    import rdflib
    from triplets.validation.shacl_report import report_to_violations, violations_to_report_graph

    rows = breaker("b1", *OK_PROPS, ("Breaker.madeUp", "x")) + CONTAINED
    v = run(rows, "pandas", closed=True)
    graph = violations_to_report_graph(v)
    components = {str(c) for c in graph.objects(
        None, rdflib.Namespace("http://www.w3.org/ns/shacl#").sourceConstraintComponent)}
    assert "https://schema.org/domainIncludes" in components            # real schema.org IRI
    back = report_to_violations(graph)
    assert "schema:domainIncludes" in set(back["VIOLATION_TYPE"])


def test_report_roundtrip_restores_rdfs_types():
    pytest.importorskip("rdflib")
    from triplets.validation.shacl_report import report_to_violations, violations_to_report_graph

    v = run(breaker("b1", ("Equipment.EquipmentContainer", "vl1")) + CONTAINED, "pandas")
    back = report_to_violations(violations_to_report_graph(v))
    assert set(back["VIOLATION_TYPE"]) == set(v["VIOLATION_TYPE"])     # xsd:minOccurs survives


def test_errors_are_self_contained(engine):
    """A model validator must understand the fix from the error alone."""
    from triplets.validation.sarif import build_sarif

    rows = (breaker("b1", *OK_PROPS[:2],
                    ("IdentifiedObject.name", "B1-dup"),
                    ("Switch.retained", "maybe"),
                    ("Equipment.EquipmentContainer", "sub1"))
            + [("sub1", "Type", "Substation", "eq"),
               ("sub1", "IdentifiedObject.name", "Main Sub", "eq")])
    v = run(rows, engine)
    targets = dict(zip(v["VIOLATION_TYPE"], v["TARGET"]))
    assert targets["xsd:maxOccurs"] == "found 2 values: 'B', 'B1-dup'"
    texts = [r["message"]["text"] for r in build_sarif(v, group=False)["runs"][0]["results"]]
    assert any("[context_value] maybe" in text for text in texts)
    assert any('referenced object sub1 found — Substation "Main Sub"' in text
               for text in texts)


def test_pyshacl_engine_refused():
    pytest.importorskip("pyshacl")
    with pytest.raises(ValueError, match="pyshacl engine cannot run"):
        run(breaker("b1"), "pyshacl")


def test_sarif_title_grouping_is_per_profile_property():
    """Alert rules group per (profile, class, property) — a title never covers
    another property's or profile's findings."""
    from triplets.validation.sarif import build_sarif

    rows = breaker("b1") + breaker("b2") + CONTAINED           # nameless, mRID-less
    v = run(rows, "pandas", context=True)
    rules = build_sarif(v)["runs"][0]["tool"]["driver"]["rules"]
    names = {rule["name"] for rule in rules}
    assert "RDFS EQ Breaker IdentifiedObject.name (2×)" in names
    assert "RDFS EQ Breaker IdentifiedObject.mRID (2×)" in names


def test_accessor():
    data = frame(breaker("b1") + CONTAINED)
    v = data.shacl.validate_schema(SCHEMA, profiles=("EQ",))
    assert ("b1", None) in violating(v, "xsd:minOccurs")


@pytest.mark.performance
def test_real_schema_smoke():
    """Svedala EQ auto-resolves its profile from the header — engines agree."""
    from pathlib import Path

    from _parity import SVEDALA_DIR, SKIP_REASON
    from triplets.export_schema import schemas

    eq = SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"
    if not Path(eq).exists():
        pytest.skip(SKIP_REASON)
    data = pandas.read_RDF([str(eq)])
    results = {}
    for engine in ENGINES:
        if engine != "pandas":
            pytest.importorskip(engine)
        v = validate_schema(data, schemas.ENTSOE_CGMES_3_0_0_552_ED1, engine=engine)
        assert set(v["PROFILE"]) <= {"EQ"} and v.attrs["validation"]["profiles"] == ["EQ"]
        results[engine] = set(zip(v["ID"], v["KEY"].astype(str), v["VIOLATION_TYPE"]))
    assert results["pandas"] == results["polars"] == results["duckdb"]
