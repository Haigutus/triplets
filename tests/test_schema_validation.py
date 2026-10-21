"""Schema validation (triplets.validation.compile_schema / validate_schema):
cardinality, datatypes and enumerations straight from the export schema, run by
the same vectorized engines as SHACL — presented with vocabulary-accurate
rdfs:/xsd:/schema: types, not fake SHACL.

Semantics under test: every instance is validated on its own against the
profiles the caller scopes, merged into one; nothing reads instance headers."""
import pandas
import pytest

from triplets.validation import compile_schema, validate_schema

ENGINES = ["pandas", "polars", "duckdb"]
CIM = "http://iec.ch/TC57/CIM100#"


@pytest.fixture(params=ENGINES)
def engine(request):
    if request.param != "pandas":
        pytest.importorskip(request.param)
    return request.param


def attribute(low, high, datatype="xsd:string"):
    return {"type": "Attribute", "multiplicity": f"{low}..{high}", "xsd:minOccours": low,
            "xsd:maxOccours": high, "xsd:type": datatype, "description": "An attribute."}


EQ = {
    "ProfileMetadata": {"keyword": "EQ", "versionIRI": "http://example.org/CoreEquipment-EU/3.0"},
    "Breaker": {"type": "Class", "description": "A breaker.", "namespace": CIM,
                "inheritance": [CIM + "Breaker", CIM + "Switch"],
                "parameters": ["IdentifiedObject.mRID", "IdentifiedObject.name", "Switch.retained", "Breaker.kind"]},
    "Switch": {"type": "Class", "namespace": CIM, "inheritance": [CIM + "Switch"],
               "parameters": ["IdentifiedObject.mRID", "Switch.locked"]},
    "IdentifiedObject.mRID": attribute("1", "1"),
    "IdentifiedObject.name": attribute("1", "1"),
    "Switch.retained": attribute("0", "1", "xsd:boolean"),
    "Switch.locked": attribute("1", "1", "xsd:boolean"),
    "Breaker.kind": {"type": "Enumeration", "multiplicity": "0..1", "xsd:minOccours": "0", "xsd:maxOccours": "1",
                     "values": ["BreakerKind.air", "BreakerKind.vacuum"]},
}
OP = {
    "ProfileMetadata": {"keyword": "OP", "versionIRI": "http://example.org/Operation-EU/3.0"},
    "Breaker": {"type": "Class", "namespace": CIM, "inheritance": [CIM + "Breaker", CIM + "Switch"],
                "parameters": ["IdentifiedObject.name", "Breaker.operated"]},
    "IdentifiedObject.name": attribute("0", "1"),
    "Breaker.operated": attribute("1", "1", "xsd:boolean"),
}
SCHEMA = {"EQ": EQ, "OP": OP}
OK = (("IdentifiedObject.mRID", "b"), ("IdentifiedObject.name", "B"))


def frame(rows):
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def breaker(object_id, *properties, instance="eq"):
    return [(object_id, "Type", "Breaker", instance)] + [(object_id, key, value, instance)
                                                         for key, value in properties]


def run(rows, engine, rdf_map_scope="EQ", **kwargs):
    return validate_schema(frame(rows), SCHEMA, rdf_map_scope=rdf_map_scope, engine=engine, **kwargs)


def profiles(violations):
    return {tuple(profile) for profile in violations["PROFILE"]}


def violating(violations, violation_type):
    rows = violations[violations["VIOLATION_TYPE"] == violation_type]
    return set(zip(rows["ID"], rows["VALUE"].where(rows["VALUE"].notna(), None)))


# ── per-check semantics ──────────────────────────────────────────────────────

def test_min_occurs(engine):
    v = run(breaker("b1", *OK) + breaker("b2", ("IdentifiedObject.mRID", "b2")), engine)
    assert violating(v, "xsd:minOccurs") == {("b2", None)}      # name missing


def test_max_occurs(engine):
    v = run(breaker("b1", *OK, ("IdentifiedObject.name", "B-again")), engine)
    assert violating(v, "xsd:maxOccurs") == {("b1", None)}
    assert v.loc[v["VIOLATION_TYPE"] == "xsd:maxOccurs", "TARGET"].iloc[0] == "found 2 values: 'B', 'B-again'"


def test_datatype_lexical_two_levels(engine):
    rows = (breaker("b1", *OK, ("Switch.retained", "maybe"))     # invalid boolean
            + breaker("b2", ("IdentifiedObject.mRID", "b2"), ("IdentifiedObject.name", "B2"),
                      ("Switch.retained", "1")))                 # valid, non-canonical
    v = run(rows, engine)
    assert violating(v, "xsd:type") == {("b1", "maybe")}
    assert violating(v, "triplets:lexicalForm") == {("b2", "1")}
    assert (v.loc[v["VIOLATION_TYPE"] == "triplets:lexicalForm", "SEVERITY"] == "Warning").all()


def test_enumeration_membership(engine):
    v = run(breaker("b1", *OK, ("Breaker.kind", "BreakerKind.oil")), engine)
    assert violating(v, "rdfs:range") == {("b1", "BreakerKind.oil")}
    assert v.loc[v["VIOLATION_TYPE"] == "rdfs:range", "EXPECTED"].iloc[0] == "one of: BreakerKind.air, BreakerKind.vacuum"


def test_undefined_keys_reported_only_on_request(engine):
    """schema:domainIncludes, not rdfs:domain — external properties attach to
    classes non-exclusively (the APL rdfs:domain/domainIncludes convention)."""
    rows = breaker("b1", *OK, ("Breaker.madeUp", "x"))
    assert violating(run(rows, engine), "schema:domainIncludes") == set()
    v = run(rows, engine, report_undefined_keys=True)
    assert set(v.loc[v["VIOLATION_TYPE"] == "schema:domainIncludes", "ID"]) == {"b1"}


def test_class_rules_do_not_widen_to_subclasses(engine):
    """Switch's own rules (Switch.locked 1..1, its closed key list) never reach a Breaker."""
    v = run(breaker("b1", *OK), engine, report_undefined_keys=True)
    assert len(v) == 0


# ── scope: instances and merged profiles ─────────────────────────────────────

def test_each_instance_counts_its_own_rows(engine):
    """EQ and SSH both serialize mRID: the union carries two rows per object, each
    instance's run counts one — no xsd:maxOccurs false positive; a missing row is
    found in the instance that lacks it."""
    rows = breaker("b1", *OK, instance="eq") + breaker("b1", ("IdentifiedObject.mRID", "b1"), instance="ssh")
    assert violating(run(rows, engine), "xsd:maxOccurs") == set()
    v = run(rows, engine)
    assert set(zip(v["ID"], v["VIOLATION_TYPE"])) == {("b1", "xsd:minOccurs")}   # ssh: no name
    assert len(run(rows, engine, data_scope=["eq"])) == 0


def test_listed_profiles_are_merged(engine):
    """EQ + OP: parameter lists unite (OP's Breaker.operated is required too), the
    cardinality is the most relaxed (name 0..1) and PROFILE lists the profiles."""
    v = run(breaker("b1", ("IdentifiedObject.mRID", "b1")), engine, rdf_map_scope=["EQ", "OP"])
    assert set(zip(v["KEY"], v["VIOLATION_TYPE"])) == {("Breaker.operated", "xsd:minOccurs")}
    assert profiles(v) == {("EQ", "OP")}
    assert any(entry.startswith("IdentifiedObject.name: cardinality relaxed")
               for entry in v.attrs["validation"]["skipped_shapes"])


def test_datatype_conflict_is_not_checked(engine):
    schema = {"EQ": EQ, "XX": {"Switch.retained": attribute("0", "1", "xsd:integer")}}
    v = validate_schema(frame(breaker("b1", *OK, ("Switch.retained", "maybe"))), schema,
                        rdf_map_scope=["EQ", "XX"], engine=engine)
    assert violating(v, "xsd:type") == set()
    assert any("Switch.retained: datatype conflict" in entry for entry in v.attrs["validation"]["skipped_shapes"])


def test_scope_by_profile_iri():
    by_iri = run(breaker("b1"), "pandas", rdf_map_scope=["http://example.org/CoreEquipment-EU/3.0"])
    assert profiles(by_iri) == {("EQ",)} and len(by_iri) == len(run(breaker("b1"), "pandas"))


def test_scope_errors():
    with pytest.raises(ValueError, match="unknown rdf_map_scope profile"):
        run(breaker("b1"), "pandas", rdf_map_scope=["NOPE"])
    with pytest.raises(ValueError, match="selects no Class entries"):
        run(breaker("b1"), "pandas", rdf_map_scope=None)       # a sectioned bundle has no root entries


def test_root_entries_schema(engine):
    """An unsectioned schema is its own root scope: PROFILE stays empty."""
    v = validate_schema(frame(breaker("b1", ("IdentifiedObject.mRID", "b1"))), EQ, engine=engine)
    assert violating(v, "xsd:minOccurs") == {("b1", None)}
    assert v["PROFILE"].isna().all()


# ── compiled shapes, metadata, reports ───────────────────────────────────────

def test_compile_schema_is_one_cached_shapes_object():
    compiled = compile_schema(SCHEMA, ["EQ", "OP"])
    assert compiled is compile_schema(SCHEMA, ["EQ", "OP"])
    assert compiled is not compile_schema(SCHEMA, ["EQ"])
    assert compiled is not compile_schema(SCHEMA, ["EQ", "OP"], report_undefined_keys=True)
    assert compiled.language == "rdfs" and compiled.graph is None
    assert compiled.stats["node_shapes"] == 2                  # Breaker and Switch carry constraints
    assert compiled.ir["shape_id"].str.startswith("urn:triplets:schema:EQ:OP#").all()


def test_run_metadata(engine):
    v = run(breaker("b1", *OK), engine)
    meta = v.attrs["validation"]
    assert meta["language"] == "rdfs" and meta["engine"] == engine
    assert meta["profiles"] == ["EQ"]
    assert meta["references"] == ["rdf_map"] and meta["source_shapes"] == {}


def test_reports_carry_rdfs_tags_and_profile(engine):
    """Both formats present the rdfs family with the profile named in the
    heading and in the error itself."""
    import rdflib
    from triplets.validation.sarif import build_sarif

    v = run(breaker("b1", ("IdentifiedObject.mRID", "b1")), engine)
    run_sarif = build_sarif(v)["runs"][0]
    text = run_sarif["results"][0]["message"]["text"]
    assert "[engine_message] " in text and "[rdfs_expected] " in text
    assert "[rdfs_profile] EQ" in text and "[shacl" not in text
    names = {rule["name"] for rule in run_sarif["tool"]["driver"]["rules"]}
    assert "RDFS EQ Breaker IdentifiedObject.name (1×)" in names

    graph = rdflib.Graph().parse(
        data=v.shacl.to_shacl_report(export_to_memory=True).getvalue(), format="turtle")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    assert "[rdfs_profile] EQ" in {str(m) for m in graph.objects(None, sh.resultMessage)}
    assert "http://www.w3.org/2001/XMLSchema#minOccurs" in {
        str(c) for c in graph.objects(None, sh.sourceConstraintComponent)}
    csv_text = v.shacl.to_csv(export_to_memory=True)[0].getvalue().decode()
    assert ",EQ" in csv_text and "['EQ']" not in csv_text      # the list is formatted for display


def test_undefined_key_type_roundtrips_through_report():
    pytest.importorskip("rdflib")
    import rdflib
    from triplets.validation.shacl_report import report_to_violations, violations_to_report_graph

    v = run(breaker("b1", *OK, ("Breaker.madeUp", "x")), "pandas", report_undefined_keys=True)
    graph = violations_to_report_graph(v)
    components = {str(c) for c in graph.objects(
        None, rdflib.Namespace("http://www.w3.org/ns/shacl#").sourceConstraintComponent)}
    assert "https://schema.org/domainIncludes" in components            # real schema.org IRI
    assert "schema:domainIncludes" in set(report_to_violations(graph)["VIOLATION_TYPE"])


def test_report_roundtrip_restores_rdfs_types():
    pytest.importorskip("rdflib")
    from triplets.validation.shacl_report import report_to_violations, violations_to_report_graph

    v = run(breaker("b1", ("IdentifiedObject.mRID", "b1")), "pandas")
    back = report_to_violations(violations_to_report_graph(v))
    assert set(back["VIOLATION_TYPE"]) == set(v["VIOLATION_TYPE"])     # xsd:minOccurs survives


def test_sarif_title_grouping_is_per_profile_property():
    """Alert rules group per (profile, class, property) — a title never covers
    another property's or profile's findings."""
    from triplets.validation.sarif import build_sarif

    v = run(breaker("b1") + breaker("b2"), "pandas", context=True)     # nameless, mRID-less
    names = {rule["name"] for rule in build_sarif(v)["runs"][0]["tool"]["driver"]["rules"]}
    assert {"RDFS EQ Breaker IdentifiedObject.name (2×)", "RDFS EQ Breaker IdentifiedObject.mRID (2×)"} <= names


def test_pyshacl_engine_refused():
    pytest.importorskip("pyshacl")
    with pytest.raises(ValueError, match="pyshacl engine cannot run"):
        run(breaker("b1"), "pyshacl")


def test_duckdb_connection_with_configured_table():
    """Instance discovery and the describe helpers resolve the connection's
    configured table/schema like the duckdb engine — not a hardcoded name."""
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(table="grid", schema="cim")
    con.execute("CREATE SCHEMA cim")
    con.register("_src", frame(breaker("b1")))
    con.execute("CREATE TABLE cim.grid AS SELECT * FROM _src")
    v = validate_schema(con, SCHEMA, rdf_map_scope="EQ", engine="duckdb")
    assert ("b1", None) in violating(v, "xsd:minOccurs")


def test_accessor():
    v = frame(breaker("b1")).shacl.validate_schema(SCHEMA, rdf_map_scope="EQ")
    assert ("b1", None) in violating(v, "xsd:minOccurs")


@pytest.mark.performance
def test_real_schema_engines_agree():
    """Svedala EQ (declares CoreEquipment) against the CGMES 3.0 EQ profile — engines agree."""
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
        v = validate_schema(data, schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope=["EQ"], engine=engine)
        assert profiles(v) <= {("EQ",)}
        results[engine] = set(zip(v["ID"], v["KEY"].astype(str), v["VIOLATION_TYPE"]))
    assert results["pandas"] == results["polars"] == results["duckdb"]
