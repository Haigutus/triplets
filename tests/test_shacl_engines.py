"""Per-component tests for the vectorized SHACL engines (compiled-IR executors).

Every test runs against both the pandas and the lazy polars engine: each
hand-builds a tiny triplet dataset with one controlled defect and asserts the
exact violating (ID, VALUE) set. pyshacl agreement is covered by
tests/test_parity_validation.py; this file pins the shared engine semantics.
"""
import pytest

pytest.importorskip("rdflib")

import pandas
import triplets

ENGINES = ["pandas", "polars", "duckdb"]


@pytest.fixture(params=ENGINES)
def engine(request):
    if request.param != "pandas":
        pytest.importorskip(request.param)
    return request.param


PREFIX = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


def run(rows, shape_turtle, engine):
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape_turtle, format="turtle")
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    return triplets.validation.validate(data, graph, engine=engine)


def breaker(object_id, *properties):
    """Rows for one cim:Breaker with the given (KEY, VALUE) properties."""
    return [(object_id, "Type", "Breaker", "eq")] + [(object_id, key, value, "eq")
                                                     for key, value in properties]


def violating(violations, component):
    rows = violations[violations["VIOLATION_TYPE"] == component]
    return set(zip(rows["ID"], rows["VALUE"].where(rows["VALUE"].notna(), None)))


SHAPE = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property [ {body} ] ."""


def test_min_count(engine):
    rows = breaker("b1", ("IdentifiedObject.name", "B1")) + breaker("b2")
    v = run(rows, SHAPE.format(body="sh:path cim:IdentifiedObject.name ; sh:minCount 1"), engine)
    assert violating(v, "sh:minCount") == {("b2", None)}


def test_max_count(engine):
    rows = breaker("b1", ("IdentifiedObject.name", "B1"), ("IdentifiedObject.name", "B1b")) \
        + breaker("b2", ("IdentifiedObject.name", "B2"))
    v = run(rows, SHAPE.format(body="sh:path cim:IdentifiedObject.name ; sh:maxCount 1"), engine)
    assert violating(v, "sh:maxCount") == {("b1", None)}


def test_inverse_min_count(engine):
    # every Breaker must be referenced by at least one Terminal
    rows = (breaker("b1") + breaker("b2")
            + [("t1", "Type", "Terminal", "eq"), ("t1", "Terminal.ConductingEquipment", "b1", "eq")])
    shape = SHAPE.format(body="sh:path [ sh:inversePath cim:Terminal.ConductingEquipment ] ; sh:minCount 1")
    v = run(rows, shape, engine)
    assert violating(v, "sh:minCount") == {("b2", None)}


def test_datatype_two_levels(engine):
    rows = breaker("b1", ("Switch.ratedCurrent", "10.5")) \
        + breaker("b2", ("Switch.ratedCurrent", "5")) \
        + breaker("b3", ("Switch.ratedCurrent", "abc"))
    v = run(rows, SHAPE.format(body="sh:path cim:Switch.ratedCurrent ; sh:datatype xsd:float"), engine)
    assert violating(v, "sh:datatype") == {("b3", "abc")}
    assert violating(v, "triplets:lexicalForm") == {("b2", "5")}


def test_pattern_is_partial_match(engine):
    rows = breaker("b1", ("IdentifiedObject.name", "OK name")) \
        + breaker("b2", ("IdentifiedObject.name", "no digits vs 42"))
    # SHACL pattern = fn:matches (search, not anchored): "\\d" hits anywhere
    v = run(rows, SHAPE.format(body='sh:path cim:IdentifiedObject.name ; sh:pattern "\\\\d"'), engine)
    assert violating(v, "sh:pattern") == {("b1", "OK name")}


def test_lengths(engine):
    rows = breaker("b1", ("IdentifiedObject.name", "x")) \
        + breaker("b2", ("IdentifiedObject.name", "way too long name"))
    v = run(rows, SHAPE.format(body="sh:path cim:IdentifiedObject.name ; sh:minLength 2 ; sh:maxLength 8"), engine)
    assert violating(v, "sh:minLength") == {("b1", "x")}
    assert violating(v, "sh:maxLength") == {("b2", "way too long name")}


def test_ranges(engine):
    rows = breaker("b1", ("Switch.ratedCurrent", "-1")) \
        + breaker("b2", ("Switch.ratedCurrent", "0")) \
        + breaker("b3", ("Switch.ratedCurrent", "50")) \
        + breaker("b4", ("Switch.ratedCurrent", "101"))
    body = "sh:path cim:Switch.ratedCurrent ; sh:minInclusive 0 ; sh:maxInclusive 100 ; sh:minExclusive -1 ; sh:maxExclusive 101"
    v = run(rows, SHAPE.format(body=body), engine)
    assert violating(v, "sh:minInclusive") == {("b1", "-1")}
    assert violating(v, "sh:maxInclusive") == {("b4", "101")}
    assert violating(v, "sh:minExclusive") == {("b1", "-1")}
    assert violating(v, "sh:maxExclusive") == {("b4", "101")}


def test_in(engine):
    rows = breaker("b1", ("Switch.state", "open")) + breaker("b2", ("Switch.state", "broken"))
    v = run(rows, SHAPE.format(body='sh:path cim:Switch.state ; sh:in ( "open" "closed" )'), engine)
    assert violating(v, "sh:in") == {("b2", "broken")}


def test_has_value(engine):
    rows = breaker("b1", ("Equipment.inService", "true")) + breaker("b2", ("Equipment.inService", "false"))
    v = run(rows, SHAPE.format(body='sh:path cim:Equipment.inService ; sh:hasValue "true"'), engine)
    assert violating(v, "sh:hasValue") == {("b2", None)}


def test_class(engine):
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "vl1"))
            + breaker("b2", ("Equipment.EquipmentContainer", "sub1"))
            + [("vl1", "Type", "VoltageLevel", "eq"), ("sub1", "Type", "Substation", "eq")])
    v = run(rows, SHAPE.format(body="sh:path cim:Equipment.EquipmentContainer ; sh:class cim:VoltageLevel"), engine)
    assert violating(v, "sh:class") == {("b2", "sub1")}


def test_node_kind_heuristic(engine):
    rows = breaker("b1", ("Equipment.EquipmentContainer", "11111111-1111-1111-1111-111111111111")) \
        + breaker("b2", ("Equipment.EquipmentContainer", "just some text"))
    v = run(rows, SHAPE.format(body="sh:path cim:Equipment.EquipmentContainer ; sh:nodeKind sh:IRI"), engine)
    assert violating(v, "sh:nodeKind") == {("b2", "just some text")}


def test_equals_and_disjoint(engine):
    rows = breaker("b1", ("IdentifiedObject.name", "A"), ("IdentifiedObject.aliasName", "A")) \
        + breaker("b2", ("IdentifiedObject.name", "A"), ("IdentifiedObject.aliasName", "B"))
    equals = run(rows, SHAPE.format(body="sh:path cim:IdentifiedObject.name ; sh:equals cim:IdentifiedObject.aliasName"), engine)
    # set equality reports both directions (matches pyshacl): A missing from
    # aliasName's values AND B missing from name's values
    assert violating(equals, "sh:equals") == {("b2", "A"), ("b2", "B")}
    disjoint = run(rows, SHAPE.format(body="sh:path cim:IdentifiedObject.name ; sh:disjoint cim:IdentifiedObject.aliasName"), engine)
    assert violating(disjoint, "sh:disjoint") == {("b1", "A")}


def test_less_than(engine):
    rows = breaker("b1", ("Switch.ratedCurrent", "10"), ("Switch.breakingCapacity", "20")) \
        + breaker("b2", ("Switch.ratedCurrent", "30"), ("Switch.breakingCapacity", "20"))
    v = run(rows, SHAPE.format(body="sh:path cim:Switch.ratedCurrent ; sh:lessThan cim:Switch.breakingCapacity"), engine)
    assert violating(v, "sh:lessThan") == {("b2", "30")}
    v = run(rows, SHAPE.format(body="sh:path cim:Switch.ratedCurrent ; sh:lessThanOrEquals cim:Switch.breakingCapacity"), engine)
    assert violating(v, "sh:lessThanOrEquals") == {("b2", "30")}


def test_closed(engine):
    rows = breaker("b1", ("IdentifiedObject.name", "B1"), ("Switch.undeclared", "x"))
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:closed true ; sh:ignoredProperties ( cim:IdentifiedObject.mRID ) ;
        sh:property [ sh:path cim:IdentifiedObject.name ] ."""
    v = run(rows, shape, engine)
    assert set(v.loc[v["VIOLATION_TYPE"] == "sh:closed", "KEY"]) == {"Switch.undeclared"}


def test_or(engine):
    # name OR description must be present
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:or ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]
                              [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) ] ."""
    rows = breaker("b1", ("IdentifiedObject.name", "B1")) \
        + breaker("b2", ("IdentifiedObject.description", "described")) \
        + breaker("b3")
    v = run(rows, shape, engine)
    assert violating(v, "sh:or") == {("b3", None)}


def test_and(engine):
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:and ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]
                               [ sh:path cim:IdentifiedObject.name ; sh:maxLength 3 ] ) ] ."""
    rows = breaker("b1", ("IdentifiedObject.name", "B1")) + breaker("b2", ("IdentifiedObject.name", "too long"))
    v = run(rows, shape, engine)
    assert set(v.loc[v["VIOLATION_TYPE"] == "sh:and", "ID"]) == {"b2"}


def test_not(engine):
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:not [ sh:path cim:Switch.state ; sh:hasValue "banned" ] ] ."""
    rows = breaker("b1", ("Switch.state", "banned")) + breaker("b2", ("Switch.state", "open"))
    v = run(rows, shape, engine)
    assert violating(v, "sh:not") == {("b1", None)}


SPARQL_SHAPE = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [
        sh:path cim:Switch.state ;
        sh:sparql [
            sh:message "state must not be bad" ;
            sh:prefixes <http://example.org/prefixes> ;
            sh:select 'SELECT $this ?value WHERE { $this cim:Switch.state ?value . FILTER (str(?value) = "bad") }' ;
        ] ;
    ] .

<http://example.org/prefixes>
    sh:declare [ sh:prefix "cim" ; sh:namespace "http://iec.ch/TC57/CIM100#"^^xsd:anyURI ] .
"""


def test_sparql_delegated_to_sparql_engine(engine):
    rows = breaker("b1", ("Switch.state", "bad")) + breaker("b2", ("Switch.state", "open"))
    v = run(rows, SPARQL_SHAPE, engine)
    assert violating(v, "sh:sparql") == {("b1", "bad")}
    assert list(v.loc[v["VIOLATION_TYPE"] == "sh:sparql", "MESSAGE"]) == ["state must not be bad"]


def test_sparql_path_placeholder(engine):
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [
            sh:path cim:Switch.state ;
            sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "bad") }' ] ;
        ] ."""
    rows = breaker("b1", ("Switch.state", "bad")) + breaker("b2", ("Switch.state", "open"))
    v = run(rows, shape, engine)
    assert violating(v, "sh:sparql") == {("b1", "bad")}


def test_sparql_max_workers_matches_sequential(engine):
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + SPARQL_SHAPE, format="turtle")
    data = pandas.DataFrame(breaker("b1", ("Switch.state", "bad")) + breaker("b2", ("Switch.state", "open")),
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    sequential = triplets.validation.validate(data, graph, engine=engine)
    parallel = triplets.validation.validate(data, graph, engine=engine, max_workers=2)
    assert violating(parallel, "sh:sparql") == violating(sequential, "sh:sparql") == {("b1", "bad")}


NODE_SHAPE = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:VoltageLevelShape ] .

cim:VoltageLevelShape a sh:NodeShape ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""


def test_node(engine):
    """sh:node — the referenced object must conform to the referenced shape."""
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "vl1"))
            + breaker("b2", ("Equipment.EquipmentContainer", "vl2"))
            + [("vl1", "Type", "VoltageLevel", "eq"), ("vl1", "IdentifiedObject.name", "VL1", "eq"),
               ("vl2", "Type", "VoltageLevel", "eq")])   # vl2 has no name
    v = run(rows, NODE_SHAPE, engine)
    assert violating(v, "sh:node") == {("b2", "vl2")}


def test_node_nested_logical(engine):
    """Focus override propagates through logical operators inside the referenced shape."""
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:ContainerShape ] .

    cim:ContainerShape a sh:NodeShape ;
        sh:property [ sh:or ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]
                              [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) ] ."""
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "vl1"))
            + breaker("b2", ("Equipment.EquipmentContainer", "vl2"))
            + [("vl1", "Type", "VoltageLevel", "eq"), ("vl1", "IdentifiedObject.description", "d", "eq"),
               ("vl2", "Type", "VoltageLevel", "eq")])   # vl2 has neither name nor description
    v = run(rows, shape, engine)
    assert violating(v, "sh:node") == {("b2", "vl2")}


def test_node_cycle_dropped(engine):
    """Mutually-referencing shapes must not recurse forever — cycle is dropped with a warning."""
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:AShape ] .
    cim:AShape a sh:NodeShape ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ;
        sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:BShape ] .
    cim:BShape a sh:NodeShape ;
        sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:AShape ] ."""
    rows = breaker("b1", ("Equipment.EquipmentContainer", "x1")) + [("x1", "Type", "Thing", "eq")]
    v = run(rows, shape, engine)   # compiles and runs; the A→B→A cycle is truncated
    assert violating(v, "sh:node") == {("b1", "x1")}   # x1 has no name


def test_target_class_isolation(engine):
    """Constraints only apply to the target class — other types are untouched."""
    rows = breaker("b1") + [("d1", "Type", "Disconnector", "eq")]
    v = run(rows, SHAPE.format(body="sh:path cim:IdentifiedObject.name ; sh:minCount 1"), engine)
    assert set(v["ID"]) == {"b1"}


def test_shape_message_and_severity_carried(engine):
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
                      sh:message "give it a name" ; sh:severity sh:Warning ] ."""
    v = run(breaker("b1"), shape, engine)
    assert list(v["MESSAGE"]) == ["give it a name"]
    assert list(v["SEVERITY"]) == ["Warning"]


def test_target_subjects_of(engine):
    """sh:targetSubjectsOf — focus = every subject carrying the property,
    regardless of its class; subjects without it are not targeted."""
    shape = """cim:SwitchStateShape a sh:NodeShape ;
        sh:targetSubjectsOf cim:Switch.normalOpen ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = (breaker("b1", ("Switch.normalOpen", "true"))                       # targeted, nameless
            + breaker("b2", ("Switch.normalOpen", "false"), ("IdentifiedObject.name", "B2"))
            + [("d1", "Type", "Disconnector", "eq"),
               ("d1", "Switch.normalOpen", "true", "eq")]                      # other class, still targeted
            + breaker("b3"))                                                   # not targeted
    v = run(rows, shape, engine)
    assert violating(v, "sh:minCount") == {("b1", None), ("d1", None)}


def test_target_subjects_of_value_test(engine):
    """A value constraint under sh:targetSubjectsOf checks the focus set's paths."""
    shape = """cim:SwitchStateShape a sh:NodeShape ;
        sh:targetSubjectsOf cim:Switch.normalOpen ;
        sh:property [ sh:path cim:Switch.normalOpen ; sh:in ( "true" "false" ) ] ."""
    rows = (breaker("b1", ("Switch.normalOpen", "open"))
            + breaker("b2", ("Switch.normalOpen", "true")))
    v = run(rows, shape, engine)
    assert violating(v, "sh:in") == {("b1", "open")}


def test_target_subjects_of_pyshacl_parity(engine):
    pytest.importorskip("pyshacl")
    shape = """cim:SwitchStateShape a sh:NodeShape ;
        sh:targetSubjectsOf cim:Switch.normalOpen ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = (breaker("b1", ("Switch.normalOpen", "true"))
            + breaker("b2", ("Switch.normalOpen", "false"), ("IdentifiedObject.name", "B2")))
    assert violating(run(rows, shape, engine), "sh:minCount") \
        == violating(run(rows, shape, "pyshacl"), "sh:minCount") == {("b1", None)}


def test_ill_typed_value_fails_sparql_engine_once_with_context():
    """One ill-typed value under an rdf_map is a data-vs-schema error: the
    strict qlever ingest names the offender, the failed build is cached (no
    per-rule rebuild), exactly ONE triplets:invalidSparql Warning is emitted,
    and the sh:sparql rules still validate through the rdflib fallback."""
    from triplets import sparql
    if sparql.get_engine("auto")[0] != "qlever":
        pytest.skip("needs the strict qlever engine as the auto SPARQL engine")
    import rdflib
    import triplets as t
    t.clear_caches()

    rdf_map = {"EQ": {"Conductor.length": {
        "type": "Attribute", "xsd:type": "xsd:float",
        "namespace": "http://iec.ch/TC57/CIM100#"}}}
    shape = """cim:LineShape a sh:NodeShape ; sh:targetClass cim:ACLineSegment ;
        sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ] ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:sparql [
            sh:message "name is reserved" ;
            sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "reserved") }' ] ] ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:sparql [
            sh:message "name is banned" ;
            sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "banned") }' ] ] ."""
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    rows = [("a1", "Type", "ACLineSegment", "eq"),
            ("a1", "Conductor.length", "abc", "eq"),        # ill-typed for xsd:float
            ("a1", "IdentifiedObject.name", "banned", "eq"),
            ("a2", "Type", "ACLineSegment", "eq"),
            ("a2", "Conductor.length", "1.5", "eq"),
            ("a2", "IdentifiedObject.name", "reserved", "eq")]
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])

    violations = triplets.validation.validate(data, graph, rdf_map=rdf_map, engine="pandas")

    notes = violations[violations["VIOLATION_TYPE"] == "triplets:invalidSparql"]
    assert len(notes) == 1, "the cached ingest failure must be reported once, not per rule"
    assert "Conductor.length" in notes["MESSAGE"].iloc[0]
    assert '"abc"' in notes["MESSAGE"].iloc[0]
    # the datatype defect itself is reported by the vectorized check
    assert violating(violations, "sh:datatype") == {("a1", "abc")}
    # both sh:sparql rules still validated via the rdflib fallback
    assert violating(violations, "sh:sparql") == {("a2", "reserved"), ("a1", "banned")}
