"""Schema-based validation (triplets.validation.compile_schema/validate_schema):
cardinality, datatypes, enumerations and association ranges straight from the
export schema, run by the same vectorized engines as SHACL — presented with
vocabulary-accurate rdfs:/xsd: types, not fake SHACL."""
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


SCHEMA = {"EQ": {
    "Breaker": {"type": "Class", "description": "A breaker.",
                "namespace": "http://iec.ch/TC57/CIM100#",
                "inheritance": ["#Breaker", "#Switch", "#Equipment"],
                "parameters": ["IdentifiedObject.name", "Switch.retained",
                               "Equipment.EquipmentContainer", "Breaker.kind"]},
    "VoltageLevel": {"type": "Class", "description": "A voltage level.",
                     "namespace": "http://iec.ch/TC57/CIM100#",
                     "inheritance": ["#VoltageLevel", "#EquipmentContainer"], "parameters": []},
    "Bay": {"type": "Class", "description": "A bay.", "namespace": "http://iec.ch/TC57/CIM100#",
            "inheritance": ["#Bay", "#EquipmentContainer"], "parameters": []},
    "Substation": {"type": "Class", "description": "A substation.",
                   "namespace": "http://iec.ch/TC57/CIM100#",
                   "inheritance": ["#Substation"], "parameters": []},
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
}}


def run(rows, engine, **kwargs):
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    return validate_schema(data, SCHEMA, engine=engine, **kwargs)


def breaker(object_id, *properties):
    return [(object_id, "Type", "Breaker", "eq")] + [(object_id, key, value, "eq")
                                                     for key, value in properties]


def violating(violations, violation_type):
    rows = violations[violations["VIOLATION_TYPE"] == violation_type]
    return set(zip(rows["ID"], rows["VALUE"].where(rows["VALUE"].notna(), None)))


CONTAINED = [("vl1", "Type", "VoltageLevel", "eq")]
OK_PROPS = (("IdentifiedObject.name", "B"), ("Equipment.EquipmentContainer", "vl1"))


def test_min_occurs(engine):
    rows = breaker("b1", *OK_PROPS) + breaker("b2", ("Equipment.EquipmentContainer", "vl1")) + CONTAINED
    v = run(rows, engine)
    assert violating(v, "xsd:minOccurs") == {("b2", None)}      # name missing


def test_max_occurs(engine):
    rows = (breaker("b1", *OK_PROPS, ("IdentifiedObject.name", "B-again")) + CONTAINED)
    v = run(rows, engine)
    assert violating(v, "xsd:maxOccurs") == {("b1", None)}


def test_datatype_lexical_two_levels(engine):
    rows = (breaker("b1", *OK_PROPS, ("Switch.retained", "maybe"))     # invalid boolean
            + breaker("b2", ("IdentifiedObject.name", "B2"),
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
    dangling reference stays silent (minOccurs catches absence)."""
    rows = (breaker("b1", ("IdentifiedObject.name", "B1"),
                    ("Equipment.EquipmentContainer", "bay1"))
            + breaker("b2", ("IdentifiedObject.name", "B2"),
                      ("Equipment.EquipmentContainer", "sub1"))
            + breaker("b3", ("IdentifiedObject.name", "B3"),
                      ("Equipment.EquipmentContainer", "ghost"))
            + [("bay1", "Type", "Bay", "eq"), ("sub1", "Type", "Substation", "eq")])
    v = run(rows, engine)
    assert violating(v, "rdfs:range") == {("b2", "sub1")}     # VALUE = the reference itself
    assert v.loc[v["ID"] == "b2", "TARGET"].iloc[0] \
        == "referenced object sub1 found — Substation"


def test_association_multi_typed_target_conforms(engine):
    """SSH/TP re-type EQ objects (issue #100): a target with several rdf:type
    values conforms when ANY of them is in the expanded range set."""
    rows = (breaker("b1", ("IdentifiedObject.name", "B1"),
                    ("Equipment.EquipmentContainer", "bay1"))
            + [("bay1", "Type", "Bay", "eq"),
               ("bay1", "Type", "Equipment", "ssh")])    # extra generic type
    v = run(rows, engine)
    assert violating(v, "rdfs:range") == set()


def test_closed_flag_reports_unknown_property(engine):
    """schema:domainIncludes, not rdfs:domain — external properties attach to
    classes non-exclusively (the APL rdfs:domain/domainIncludes convention),
    so "not among this class's declared properties" is a domainIncludes claim."""
    rows = breaker("b1", *OK_PROPS, ("Breaker.madeUp", "x")) + CONTAINED
    assert violating(run(rows, engine), "schema:domainIncludes") == set()   # default: off
    v = run(rows, engine, closed=True)
    flagged = v[v["VIOLATION_TYPE"] == "schema:domainIncludes"]
    assert set(flagged["ID"]) == {"b1"}


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


def test_cross_profile_dedupe_first_wins():
    two_profiles = {"EQ": SCHEMA["EQ"],
                    "SSH": {"Breaker": SCHEMA["EQ"]["Breaker"],
                            "IdentifiedObject.name": {**SCHEMA["EQ"]["IdentifiedObject.name"],
                                                      "xsd:minOccours": "0"}}}
    compiled = compile_schema(two_profiles)
    name_rows = compiled.ir[(compiled.ir["path"] == "IdentifiedObject.name")
                            & (compiled.ir["component"] == "sh:minCount")]
    assert len(name_rows) == 1 and name_rows["params"].iloc[0] == 1    # EQ (first) wins


def test_compiled_shape_and_metadata():
    compiled = compile_schema(SCHEMA)
    assert compiled.language == "rdfs" and compiled.graph is None
    assert compiled.stats["node_shapes"] == 1          # only Breaker carries constraints
    assert compiled.stats["skipped_shapes"] == []
    assert compile_schema(SCHEMA) is compiled          # cached

    v = run(breaker("b1", *OK_PROPS) + CONTAINED, "pandas")
    meta = v.attrs["validation"]
    assert meta["language"] == "rdfs"
    assert meta["references"] == ["rdf_map"]
    assert meta["source_shapes"] == {}                 # nothing to embed — no graph


def test_unexpandable_range_lands_in_coverage():
    schema = {"EQ": {**SCHEMA["EQ"],
                     "Equipment.EquipmentContainer": {
                         **SCHEMA["EQ"]["Equipment.EquipmentContainer"],
                         "range": "http://iec.ch/TC57/61970-552/ModelDescription/1#Model"}}}
    compiled = compile_schema(schema)
    assert any("Model" in entry for entry in compiled.stats["skipped_shapes"])


def test_reports_carry_rdfs_tags(engine):
    """Both report formats present the rdfs language family — schema
    validation does not masquerade as SHACL."""
    import rdflib
    from triplets.validation.sarif import build_sarif

    v = run(breaker("b1", ("Equipment.EquipmentContainer", "vl1")) + CONTAINED, engine)
    text = build_sarif(v)["runs"][0]["results"][0]["message"]["text"]
    assert "[engine_message] " in text and "[rdfs_expected] " in text
    assert "[rdfs_path] " in text and "[shacl" not in text

    graph = rdflib.Graph().parse(
        data=v.shacl.to_shacl_report(export_to_memory=True).getvalue(), format="turtle")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    components = {str(c) for c in graph.objects(None, sh.sourceConstraintComponent)}
    assert "http://www.w3.org/2001/XMLSchema#minOccurs" in components  # real XSD IRI


def test_report_roundtrip_restores_rdfs_types():
    pytest.importorskip("rdflib")
    from triplets.validation.shacl_report import report_to_violations, violations_to_report_graph

    v = run(breaker("b1", ("Equipment.EquipmentContainer", "vl1")) + CONTAINED, "pandas")
    back = report_to_violations(violations_to_report_graph(v))
    assert set(back["VIOLATION_TYPE"]) == set(v["VIOLATION_TYPE"])     # xsd:minOccurs survives


def test_pyshacl_engine_refused():
    pytest.importorskip("pyshacl")
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    with pytest.raises(ValueError, match="pyshacl engine cannot run"):
        validate_schema(data, SCHEMA, engine="pyshacl")


def test_accessor():
    data = pandas.DataFrame(breaker("b1") + CONTAINED,
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = data.shacl.validate_schema(SCHEMA)
    assert violating(v, "xsd:minOccurs") == {("b1", None)}


@pytest.mark.performance
def test_real_schema_smoke():
    """Svedala EQ against the real CGMES 3.0.0 schema — engines agree."""
    from _parity import SVEDALA_DIR, SKIP_REASON
    from pathlib import Path
    from triplets.export_schema import schemas

    eq = SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"
    if not Path(eq).exists():
        pytest.skip(SKIP_REASON)
    data = pandas.read_RDF([str(eq)])
    results = {}
    for engine in ENGINES:
        pytest.importorskip(engine) if engine != "pandas" else None
        v = validate_schema(data, schemas.ENTSOE_CGMES_3_0_0_552_ED1, engine=engine)
        results[engine] = set(zip(v["ID"], v["KEY"].astype(str), v["VIOLATION_TYPE"]))
    assert results["pandas"] == results["polars"] == results["duckdb"]


def test_mrid_min_occurs_satisfied_by_rdf_id(engine):
    """IEC 61970-552: mRID maps to the rdf:ID/about attribute — every parsed
    object carries it as the ID column, so no minOccurs check (issue #101);
    an explicit duplicate element still trips maxOccurs."""
    schema = {"EQ": {**SCHEMA["EQ"]}}
    schema["EQ"]["Breaker"] = {**SCHEMA["EQ"]["Breaker"],
                               "parameters": [*SCHEMA["EQ"]["Breaker"]["parameters"],
                                              "IdentifiedObject.mRID"]}
    schema["EQ"]["IdentifiedObject.mRID"] = {
        "type": "Attribute", "multiplicity": "1..1", "xsd:minOccours": "1",
        "xsd:maxOccours": "1", "xsd:type": "xsd:string", "description": "Master RID."}
    data = pandas.DataFrame(breaker("b1", *OK_PROPS) + CONTAINED,
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = validate_schema(data, schema, engine=engine)
    assert violating(v, "xsd:minOccurs") == set()          # no mRID element needed

    doubled = pandas.DataFrame(
        breaker("b1", *OK_PROPS, ("IdentifiedObject.mRID", "x"),
                ("IdentifiedObject.mRID", "y")) + CONTAINED,
        columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = validate_schema(doubled, schema, engine=engine)
    assert ("b1", None) in violating(v, "xsd:maxOccurs")   # maxOccurs still applies


def test_sarif_titles_follow_rdfs_format():
    """Alert titles read "RDFS <Class> <attr> (N×)" — GitHub shows the rule
    name as the alert title; without one it dumps the raw message text."""
    from triplets.validation.sarif import build_sarif

    rows = (breaker("b1") + breaker("b2") + CONTAINED)     # two nameless breakers
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = validate_schema(data, SCHEMA, context=True)        # even enriched: same title
    rules = build_sarif(v)["runs"][0]["tool"]["driver"]["rules"]
    names = {rule["name"] for rule in rules}
    assert "RDFS Breaker IdentifiedObject.name (2×)" in names


def test_errors_are_self_contained(engine):
    """A model validator must understand the fix from the error alone: the
    offending literal is in the text, duplicates are listed, and references
    carry the target's id, type and name."""
    import rdflib
    from triplets.validation.sarif import build_sarif

    rows = (breaker("b1",
                    ("IdentifiedObject.name", "B1"), ("IdentifiedObject.name", "B1-dup"),
                    ("Switch.retained", "maybe"),
                    ("Equipment.EquipmentContainer", "sub1"))
            + [("sub1", "Type", "Substation", "eq"),
               ("sub1", "IdentifiedObject.name", "Main Sub", "eq")])
    v = run(rows, engine)
    targets = dict(zip(v["VIOLATION_TYPE"], v["TARGET"]))
    assert targets["xsd:maxOccurs"] == "found 2 values: 'B1', 'B1-dup'"
    assert targets["rdfs:range"] == 'referenced object sub1 found — Substation "Main Sub"'

    texts = [r["message"]["text"] for r in build_sarif(v, group=False)["runs"][0]["results"]]
    assert any("[context_value] maybe" in text for text in texts)          # the bad literal
    assert any("found 2 values: 'B1', 'B1-dup'" in text for text in texts)
    assert any('referenced object sub1 found — Substation "Main Sub"' in text
               for text in texts)

    grouped = build_sarif(v)["runs"][0]["results"]
    examples = " ".join(r["message"]["text"] for r in grouped)
    assert "= 'maybe'" in examples                        # object ↔ value pairing

    graph = rdflib.Graph().parse(
        data=v.shacl.to_shacl_report(export_to_memory=True).getvalue(), format="turtle")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    messages = {str(m) for m in graph.objects(None, sh.resultMessage)}
    assert "[context_value] maybe" in messages
