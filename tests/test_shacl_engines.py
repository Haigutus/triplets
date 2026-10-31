"""Per-component tests for the vectorized SHACL engines (compiled-IR executors).

Every test runs against both the pandas and the lazy polars engine: each
hand-builds a tiny triplet dataset with one controlled defect and asserts the
exact violating (ID, VALUE) set. pyshacl agreement is covered by
tests/test_parity_shacl_matrix.py; this file pins the shared engine semantics.
"""
import pytest

pytest.importorskip("rdflib")

import pandas
import triplets

from triplets.validation.shacl_report import has_violation

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
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
"""


def run(rows, shape_turtle, engine, rdf_map=None, **kwargs):
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape_turtle, format="turtle")
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    return triplets.validation.validate_shacl(data, graph, engine=engine, rdf_map=rdf_map,
                                              rdf_map_scope="*" if rdf_map else None, **kwargs)


def breaker(object_id, *properties):
    """Rows for one cim:Breaker with the given (KEY, VALUE) properties."""
    return [(object_id, "Type", "Breaker", "eq")] + [(object_id, key, value, "eq")
                                                     for key, value in properties]


def violating(violations, component):
    rows = violations[violations["VIOLATION_TYPE"] == component]
    return set(zip(rows["ID"], rows["VALUE"].astype(object).where(rows["VALUE"].notna(), None)))


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


def test_datatype_two_levels_under_property(engine):
    """sh:property on a property shape reports the nested datatype findings as its
    own, the lexicalForm Warning included, on the value nodes."""
    rows = [row for n, length in ((1, "10.5"), (2, "1"), (3, "abc"))
            for row in breaker(f"b{n}", ("Equipment.EquipmentContainer", f"c{n}"))
            + [(f"c{n}", "Type", "Bay", "eq"), (f"c{n}", "Conductor.length", length, "eq")]]
    body = ("sh:path cim:Equipment.EquipmentContainer ; "
            "sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ]")
    v = run(rows, SHAPE.format(body=body), engine)
    assert violating(v, "sh:datatype") == {("c3", "abc")}
    assert violating(v, "triplets:lexicalForm") == {("c2", "1")}
    assert set(v.loc[v["VIOLATION_TYPE"] == "triplets:lexicalForm", "SEVERITY"]) == {"Warning"}


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


def test_class_target_names_referenced_type(engine):
    """A type-of-association finding says what the reference points at (the
    TARGET column — the raw MESSAGE stays verbatim): the target's actual
    Type when it exists, or that the reference is dangling."""
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "sub1"))
            + breaker("b2", ("Equipment.EquipmentContainer", "ghost"))
            + [("sub1", "Type", "Substation", "eq")])
    v = run(rows, SHAPE.format(body="sh:path cim:Equipment.EquipmentContainer ; sh:class cim:VoltageLevel"), engine)
    v = triplets.validation.enrich(v, data=pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"]))
    targets = dict(zip(v["ID"], v["TARGET"]))
    assert targets["b1"] == "referenced object sub1 found — Substation"
    assert targets["b2"] == "referenced object ghost not found in the data"
    assert " — " not in v["MESSAGE"].iloc[0]              # raw text untouched
    assert set(v["EXPECTED"]) == {"a reference to a VoltageLevel"}


def test_node_kind_heuristic(engine):
    rows = breaker("b1", ("Equipment.EquipmentContainer", "11111111-1111-1111-1111-111111111111")) \
        + breaker("b2", ("Equipment.EquipmentContainer", "just some text"))
    v = run(rows, SHAPE.format(body="sh:path cim:Equipment.EquipmentContainer ; sh:nodeKind sh:IRI"), engine)
    assert violating(v, "sh:nodeKind") == {("b2", "just some text")}


def test_node_kind_by_schema(engine):
    """With an rdf_map the schema decides: an Association path violates sh:Literal on
    every row (whatever the value looks like) and an Enumeration path violates sh:IRI never."""
    import rdflib
    rdf_map = {"EQ": {"Equipment.EquipmentContainer": {"type": "Association", "xsd:type": "xsd:anyURI"},
                      "Switch.kind": {"type": "Enumeration", "xsd:type": "xsd:anyURI"}}}
    rows = breaker("b1", ("Equipment.EquipmentContainer", "not-a-uuid"), ("Switch.kind", "SwitchKind.breaker")) \
        + breaker("b2", ("Equipment.EquipmentContainer", "11111111-1111-1111-1111-111111111111"), ("Switch.kind", "x"))
    shape = SHAPE.format(body="sh:path cim:Equipment.EquipmentContainer ; sh:nodeKind sh:Literal") \
        .replace(" .", " ; sh:property [ sh:path cim:Switch.kind ; sh:nodeKind sh:IRI ] .", 1)
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = triplets.validation.validate_shacl(data, graph, rdf_map=rdf_map, rdf_map_scope="*", engine=engine)
    assert violating(v, "sh:nodeKind") == {("b1", "not-a-uuid"), ("b2", "11111111-1111-1111-1111-111111111111")}


def test_datatype_any_uri_lexical(engine):
    """xsd:anyURI attributes are literals, so the lexical form is checked: an IRI reference
    (relative and non-ASCII allowed), no space / <>"{}|^`\\ / controls, "%" only as %XX."""
    rows = [(f"b{index}", "Type", "Breaker", "eq") for index in range(6)] + [
        ("b0", "Model.uri", "http://tso.example/a", "eq"), ("b1", "Model.uri", "urn:uuid:x", "eq"),
        ("b2", "Model.uri", "http://ä.example/%20", "eq"), ("b3", "Model.uri", "has space", "eq"),
        ("b4", "Model.uri", "a%2", "eq"), ("b5", "Model.uri", "a<b>", "eq")]
    v = run(rows, SHAPE.format(body="sh:path cim:Model.uri ; sh:datatype xsd:anyURI"), engine)
    assert violating(v, "sh:datatype") == {("b3", "has space"), ("b4", "a%2"), ("b5", "a<b>")}


def test_node_kind_iri_on_association_follows_schema(engine):
    """sh:IRI on an Association path never violates with an rdf_map: the exporters write
    every reference as an IRI (unsafe text percent-encoded), so all engines agree."""
    import rdflib
    rdf_map = {"EQ": {"Equipment.EquipmentContainer": {"type": "Association", "xsd:type": "xsd:anyURI"}}}
    rows = breaker("b1", ("Equipment.EquipmentContainer", "just some text"))
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + SHAPE.format(body="sh:path cim:Equipment.EquipmentContainer ; sh:nodeKind sh:IRI"),
                format="turtle")
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = triplets.validation.validate_shacl(data, graph, rdf_map=rdf_map, rdf_map_scope="*", engine=engine)
    assert violating(v, "sh:nodeKind") == set()


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


def test_closed_allows_every_path_of_a_skipped_property_shape(engine):
    """A property shape with two sh:path values is skipped as malformed; under
    sh:closed both paths stay declared, whatever the triple order."""
    rows = breaker("b1", ("IdentifiedObject.name", "n"), ("IdentifiedObject.description", "d"))
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:closed true ; sh:ignoredProperties ( rdf:type ) ; sh:property cim:P .
        cim:P sh:path cim:IdentifiedObject.name, cim:IdentifiedObject.description ."""
    v = run(rows, shape, engine)
    assert v.empty
    assert v.attrs["validation"]["skipped_shapes"] == [f"{CIM}P: two sh:path values"]


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


def test_sparql_literal_values_keep_leading_underscore(engine):
    """?value literals come back verbatim — only IRIs are shortened (a literal
    ``_name`` must not lose its ``_`` to the ID prefix rule)."""
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [
            sh:path cim:IdentifiedObject.name ;
            sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (STRSTARTS(str(?value), "_")) }' ] ;
        ] ."""
    rows = breaker("b1", ("IdentifiedObject.name", "_name")) + breaker("b2", ("IdentifiedObject.name", "name"))
    v = run(rows, shape, engine)
    assert violating(v, "sh:sparql") == {("b1", "_name")}


def test_sparql_max_workers_matches_sequential(engine):
    """max_workers runs every sh:sparql rule after the first on the pool (threads
    for qlever/oxigraph, forked processes for rdflib) with the sequential result."""
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:Switch.state ;
            sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "bad") }' ] ;
            sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "broken") }' ] ;
            sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "stuck") }' ] ;
        ] ."""
    rows = (breaker("b1", ("Switch.state", "bad")) + breaker("b2", ("Switch.state", "broken"))
            + breaker("b3", ("Switch.state", "stuck")) + breaker("b4", ("Switch.state", "open")))
    sequential = run(rows, shape, engine)
    parallel = run(rows, shape, engine, max_workers=2)
    assert (violating(parallel, "sh:sparql") == violating(sequential, "sh:sparql")
            == {("b1", "bad"), ("b2", "broken"), ("b3", "stuck")})
    assert "triplets:invalidSparql" not in set(parallel["VIOLATION_TYPE"])


ABORT_SHAPES = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ;
    sh:property [ sh:path cim:Switch.state ;
        sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "bad") }' ] ] .
cim:DescriptionShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ; sh:severity sh:Warning ] .
cim:AliasShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.aliasName ; sh:minCount 1 ; sh:severity sh:Warning ] .
"""


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
def test_abort_on_first(engine_name):
    """abort_on_first gives the full run's yes/no answer: with a Violation the run
    stops early (the vectorized engines before their sh:sparql phase) and says so;
    without one — Warnings only, which never stop it — it is the full run."""
    pytest.importorskip(engine_name)
    violated = breaker("b1", ("Switch.state", "bad")) + breaker("b2", ("IdentifiedObject.name", "n"))
    full = run(violated, ABORT_SHAPES, engine_name)
    aborted = run(violated, ABORT_SHAPES, engine_name, abort_on_first=True)
    assert has_violation(full) and has_violation(aborted)
    assert full.attrs["validation"]["aborted"] is False
    assert aborted.attrs["validation"]["aborted"] is True
    if engine_name != "pyshacl":   # pyshacl picks its own shape order
        assert "sh:sparql" in set(full["VIOLATION_TYPE"]) - set(aborted["VIOLATION_TYPE"])

    # the vectorized pass finds Warnings only, so the delegated sh:sparql still runs
    late = run(breaker("b1", ("IdentifiedObject.name", "n"), ("Switch.state", "bad")), ABORT_SHAPES,
               engine_name, abort_on_first=True)
    assert has_violation(late) and "sh:sparql" in set(late["VIOLATION_TYPE"])
    assert late.attrs["validation"]["aborted"] is True

    clean = breaker("b1", ("IdentifiedObject.name", "n"), ("Switch.state", "open"))
    full = run(clean, ABORT_SHAPES, engine_name)
    complete = run(clean, ABORT_SHAPES, engine_name, abort_on_first=True)
    assert not has_violation(complete) and complete.attrs["validation"]["aborted"] is False
    assert len(complete) == 2   # both Warning shapes
    columns = ["ID", "KEY", "VIOLATION_TYPE", "SEVERITY", "MESSAGE"]   # SOURCE_SHAPE: a fresh blank node per parse
    assert set(complete[columns].itertuples(index=False)) == set(full[columns].itertuples(index=False))


def test_sparql_bind_exists_first_sees_focus_node(engine):
    """The focus-node VALUES block opens the WHERE group, ahead of the authored
    pattern, which stays verbatim; a BIND (EXISTS { $this … }) opening the group
    is evaluated per focus node."""
    from types import SimpleNamespace
    from triplets.export.nquads_utils import make_subject
    from triplets.validation import shacl_pandas
    select = ("SELECT $this ?value WHERE { BIND (EXISTS { $this cim:Switch.state ?state } AS ?switched) "
              "$this cim:IdentifiedObject.name ?value . FILTER (!?switched) }")
    prefixes = "PREFIX cim: <http://iec.ch/TC57/CIM100#>\n"
    text = shacl_pandas._sparql_query_text(
        SimpleNamespace(params={"select": select, "prefixes": prefixes, "path": None}), ["b1"])
    block = f" VALUES ?this {{ {make_subject('b1')} }} "
    assert text.index("WHERE {") < text.index(block) < text.index("BIND")
    assert text.replace(block, "", 1) == prefixes + select

    shape = f"""cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:sparql [ sh:prefixes <http://example.org/prefixes> ; sh:select '{select}' ] .
    <http://example.org/prefixes> sh:declare [ sh:prefix "cim" ; sh:namespace "http://iec.ch/TC57/CIM100#"^^xsd:anyURI ] ."""
    rows = (breaker("b1", ("IdentifiedObject.name", "B1"), ("Switch.state", "open"))
            + breaker("b2", ("IdentifiedObject.name", "B2"))
            + [("d1", "Type", "Disconnector", "eq"), ("d1", "IdentifiedObject.name", "D1", "eq")])
    assert violating(run(rows, shape, engine), "sh:sparql") == {("b2", "B2")}


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


def test_target_isolation(engine):
    """Constraints only apply to the target class — other types are untouched."""
    rows = breaker("b1") + [("d1", "Type", "Disconnector", "eq")]
    v = run(rows, SHAPE.format(body="sh:path cim:IdentifiedObject.name ; sh:minCount 1"), engine)
    assert set(v["ID"]) == {"b1"}


def test_shape_message_and_severity_carried(engine):
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
                      sh:message "give it a name" ; sh:severity sh:Warning ] ."""
    v = run(breaker("b1"), shape, engine)
    assert list(v["MESSAGE"]) == ["give it a name"]   # authored text verbatim (enrich adds context)
    assert list(v["SEVERITY"]) == ["Warning"]


def test_two_severities_read_the_first(engine):
    """Two sh:severity values: the first one declared, on every run (pyshacl's pick
    follows the hash seed, so the parity matrix has no case for it)."""
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; sh:severity sh:Warning, sh:Info ] ."""
    assert list(run(breaker("b1"), shape, engine)["SEVERITY"]) == ["Warning"]


def test_two_messages_read_the_first(engine):
    """Two sh:message values: the first one declared (pyshacl reports both; its report
    reader keeps the alphabetically first — the parity matrix's message_two_values xfail)."""
    shape = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; sh:message "beta", "alpha" ] ."""
    assert list(run(breaker("b1"), shape, engine)["MESSAGE"]) == ["beta"]


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

    violations = triplets.validation.validate_shacl(data, graph, rdf_map=rdf_map, rdf_map_scope="*", engine="pandas")

    notes = violations[violations["VIOLATION_TYPE"] == "triplets:invalidSparql"]
    assert len(notes) == 1, "the cached ingest failure must be reported once, not per rule"
    assert "Conductor.length" in notes["MESSAGE"].iloc[0]
    assert '"abc"' in notes["MESSAGE"].iloc[0]
    # the datatype defect itself is reported by the vectorized check
    assert violating(violations, "sh:datatype") == {("a1", "abc")}
    # both sh:sparql rules still validated via the rdflib fallback
    assert violating(violations, "sh:sparql") == {("a2", "reserved"), ("a1", "banned")}


VALUE_TYPE_SHAPE = """@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
cim:BreakerBayShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [
        sh:path ( cim:Equipment.EquipmentContainer rdf:type ) ;
        sh:in ( cim:Bay cim:VoltageLevel ) ;
        sh:nodeKind sh:IRI ;
        sh:message "container must be a Bay or a VoltageLevel" ;
    ] ."""


def test_sequence_path_via_type(engine):
    """The ENTSO-E valueType pattern — sh:path ( assoc rdf:type ) — checks the
    referenced object's class. Wrong-class targets are flagged; a dangling
    reference yields no value node (SHACL path semantics) and passes."""
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "bay1"))
            + breaker("b2", ("Equipment.EquipmentContainer", "sub1"))
            + breaker("b3", ("Equipment.EquipmentContainer", "missing"))
            + [("bay1", "Type", "Bay", "eq"), ("sub1", "Type", "Substation", "eq")])
    import rdflib
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    compiled = triplets.validation.compile(rdflib.Graph().parse(data=PREFIX + VALUE_TYPE_SHAPE, format="turtle"))
    v = triplets.validation.validate_shacl(data, compiled, engine=engine)
    assert violating(v, "sh:in") == {("b2", "Substation")}
    assert violating(v, "sh:nodeKind") == set()   # types are IRIs by definition
    v = triplets.validation.enrich(v, data=data, shapes=compiled)   # shapes: which rows are valueType rows
    # authored text verbatim; the found type is its own TARGET entry
    flagged = v.loc[v["VIOLATION_TYPE"] == "sh:in"].iloc[0]
    assert flagged["MESSAGE"] == "container must be a Bay or a VoltageLevel"
    assert flagged["TARGET"] == "association target found, of type Substation"
    assert flagged["EXPECTED"] == "one of: Bay, VoltageLevel"


def test_defective_sparql_reports_not_evaluated():
    """A query no engine can run yields ONE Violation row that names the
    shape and says plainly the constraint went unchecked (a shapes bug)."""
    shape = """cim:BadShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:sparql [
            sh:select 'SELECT $this WHERE { $this nc:undeclared ?x }' ] ] ."""
    rows = breaker("b1", ("IdentifiedObject.name", "B1"))
    v = run(rows, shape, "pandas")
    notes = v[v["VIOLATION_TYPE"] == "triplets:invalidSparql"]
    assert len(notes) == 1
    assert (notes["SEVERITY"] == "Violation").all()
    message = notes["MESSAGE"].iloc[0]
    assert "Breaker/IdentifiedObject.name" in message   # anonymous shape → target/path names it
    assert "NOT evaluated" in message and "shapes bug" in message


def test_unsupported_property_paths_still_skip_with_warning(caplog):
    """Longer sequences / zeroOrMorePath stay out of the IR — skipped with the
    coverage warning (pyshacl covers them)."""
    import logging
    shape = """@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
        cim:DeepShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path ( cim:Equipment.EquipmentContainer cim:Bay.VoltageLevel rdf:type ) ;
                      sh:in ( cim:VoltageLevel ) ] ;
        sh:property [ sh:path [ sh:zeroOrMorePath cim:Bay.VoltageLevel ] ; sh:minCount 1 ] ."""
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    with caplog.at_level(logging.WARNING, logger="triplets.validation.shacl_ir"):
        compiled = triplets.validation.compile(graph)
    assert len(compiled.ir) == 0
    assert sum("cannot express" in record.message for record in caplog.records) == 2


def test_rdf_type_path_compiles_to_the_type_key(engine):
    """sh:path rdf:type lands on the Type KEY (local_key), not a KEY "type"."""
    rows = breaker("b1") + [("x1", "IdentifiedObject.name", "untyped", "eq")]
    v = run(rows, SHAPE.format(body="sh:path rdf:type ; sh:maxCount 1"), engine)
    assert violating(v, "sh:maxCount") == set()
    v = run(rows + [("b1", "Type", "Switch", "eq")], SHAPE.format(body="sh:path rdf:type ; sh:maxCount 1"), engine)
    assert {row[0] for row in violating(v, "sh:maxCount")} == {"b1"}


def test_in_member_with_slash_iri_matches_the_whole_value(engine):
    """sh:in members follow the VALUE rule: a "/"-only IRI stays whole, as the data VALUE does."""
    kind = "http://example.org/kinds/a"
    rows = breaker("b1", ("Switch.kind", kind)) + breaker("b2", ("Switch.kind", "http://example.org/kinds/b"))
    v = run(rows, SHAPE.format(body=f"sh:path cim:Switch.kind ; sh:in ( <{kind}> )"), engine)
    assert violating(v, "sh:in") == {("b2", "http://example.org/kinds/b")}


CIM = "http://iec.ch/TC57/CIM100#"
TOY_SCHEMA = {"EQ": {
    **{name: {"type": "Class", "namespace": CIM, "inheritance": [CIM + name, CIM + "Switch", CIM + "Equipment"]}
       for name in ("Breaker", "Disconnector")},
    **{name: {"type": "Class", "namespace": CIM, "inheritance": [CIM + name, CIM + "EquipmentContainer"]}
       for name in ("Bay", "VoltageLevel")},
    "Substation": {"type": "Class", "namespace": CIM, "inheritance": [CIM + "Substation"]},
}}


def test_abstract_target_walks_type_index(engine):
    """sh:targetClass Equipment hits Breaker/Disconnector via a precomputed
    Type index (IR is not fanned out). Without rdf_map, match is exact."""
    shape = """cim:EqShape a sh:NodeShape ; sh:targetClass cim:Equipment ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = (breaker("b1") + [("d1", "Type", "Disconnector", "eq"),
                             ("x1", "Type", "Substation", "eq")])
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    bare = triplets.validation.validate_shacl(data, graph, engine=engine)
    assert violating(bare, "sh:minCount") == set()
    bound = triplets.validation.validate_shacl(data, graph, engine=engine, rdf_map=TOY_SCHEMA, rdf_map_scope="*")
    assert violating(bound, "sh:minCount") == {("b1", None), ("d1", None)}
    # IR stays one row per constraint — walk is in the Type index, not a fan-out
    compiled = triplets.validation.compile(graph)
    assert compiled.ir["target_on"].tolist() == ["Equipment"]


def test_abstract_target_keeps_direct_equipment_type_rows(engine):
    """Type=Equipment in the data must not hide descendant IDs, and must stay
    in the Equipment index (batched minCount uses membership)."""
    shape = """cim:EqShape a sh:NodeShape ; sh:targetClass cim:Equipment ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = (breaker("b1") + [("e1", "Type", "Equipment", "eq"),
                             ("x1", "Type", "Substation", "eq")])
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    bound = triplets.validation.validate_shacl(data, graph, engine=engine, rdf_map=TOY_SCHEMA, rdf_map_scope="*")
    assert violating(bound, "sh:minCount") == {("b1", None), ("e1", None)}


def test_sequence_path_via_type_reads_instance_types_with_schema(engine):
    """( assoc rdf:type ) yields the referenced object's own Type values — the
    schema's ancestors (EquipmentContainer) are not extra value nodes, so they
    neither fail sh:in nor add to sh:maxCount."""
    shape = VALUE_TYPE_SHAPE.replace("sh:nodeKind sh:IRI ;", "sh:maxCount 1 ;")
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "bay1"))
            + breaker("b2", ("Equipment.EquipmentContainer", "sub1"))
            + [("bay1", "Type", "Bay", "eq"), ("sub1", "Type", "Substation", "eq")])
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    v = triplets.validation.validate_shacl(data, graph, engine=engine, rdf_map=TOY_SCHEMA, rdf_map_scope="*")
    assert violating(v, "sh:in") == {("b2", "Substation")}
    assert violating(v, "sh:maxCount") == set()


def test_sh_class_accepts_subclasses_via_index(engine):
    shape = """cim:BayShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:class cim:EquipmentContainer ] ."""
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "bay1"))
            + breaker("b2", ("Equipment.EquipmentContainer", "sub1"))
            + [("bay1", "Type", "Bay", "eq"), ("sub1", "Type", "Substation", "eq")])
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    v = triplets.validation.validate_shacl(data, graph, engine=engine, rdf_map=TOY_SCHEMA, rdf_map_scope="*")
    assert violating(v, "sh:class") == {("b2", "sub1")}


def findings(violations):
    return set(zip(violations["ID"], violations["VIOLATION_TYPE"]))


DEACTIVATED = {
    "node shape": """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:deactivated true ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .""",
    "inline property shape": """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; sh:deactivated true ] ;
        sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .""",
    # the NC profiles' form: a standalone property shape switched off
    "standalone property shape": """cim:NameShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ;
            sh:minCount 1 ; sh:deactivated true .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:NameShape ;
            sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .""",
    "sh:node target": """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
            sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:ContainerShape ] ;
            sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .
        cim:ContainerShape a sh:NodeShape ; sh:deactivated true ;
            sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .""",
    # a deactivated member conforms, so the sh:or holds for every aliasName value
    "logical member": """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.aliasName ;
                      sh:or ( [ sh:datatype xsd:integer ; sh:deactivated true ] [ sh:datatype xsd:boolean ] ) ] ;
        sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .""",
    # the path of a deactivated property shape is still declared, so sh:closed allows it
    "path under sh:closed": """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:closed true ;
        sh:ignoredProperties ( rdf:type cim:Equipment.EquipmentContainer cim:IdentifiedObject.name ) ;
        sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ;
        sh:property [ sh:path cim:IdentifiedObject.aliasName ; sh:maxCount 0 ; sh:deactivated true ] .""",
}


@pytest.mark.parametrize("engine_name", ENGINES)
@pytest.mark.parametrize("case", DEACTIVATED)
def test_deactivated_shapes_are_not_validated(engine_name, case):
    """b1 breaks only the deactivated constraints; b2 also lacks the active description."""
    pytest.importorskip(engine_name)
    rows = (breaker("b1", ("IdentifiedObject.description", "d"), ("IdentifiedObject.aliasName", "a"),
                    ("Equipment.EquipmentContainer", "vl1"))
            + breaker("b2", ("IdentifiedObject.aliasName", "a"), ("Equipment.EquipmentContainer", "vl1"))
            + [("vl1", "Type", "VoltageLevel", "eq")])   # vl1 has no name
    expected = set() if case == "node shape" else {("b2", "sh:minCount")}
    assert findings(run(rows, DEACTIVATED[case], engine_name)) == expected


CIM = "http://iec.ch/TC57/CIM100#"


def compiled_shapes(shape_turtle):
    import rdflib
    return triplets.validation.compile(rdflib.Graph().parse(data=PREFIX + shape_turtle, format="turtle"))


def switched_off(rows, compiled, engine_name, shapes=None):
    """validate_shacl with the cim: *shapes* as deactivated_shapes (None: the declared set)."""
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    return triplets.validation.validate_shacl(
        data, compiled, engine=engine_name, lexical=False,
        deactivated_shapes=None if shapes is None else [CIM + shape for shape in shapes])


@pytest.mark.parametrize("engine_name", ENGINES)
def test_deactivated_shapes_parameter(engine_name):
    """One compile, three rule sets: each switched-off set is its own cached filter."""
    pytest.importorskip(engine_name)
    from triplets.validation.shacl_ir import _COMPILE_CACHE
    compiled = compiled_shapes("""
        cim:NameShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 .
        cim:DescriptionShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.description ; sh:minCount 1 .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
            sh:property cim:NameShape, cim:DescriptionShape .""")
    cached = len(_COMPILE_CACHE)
    rows = breaker("b1", ("IdentifiedObject.description", "d")) + breaker("b2", ("IdentifiedObject.name", "n"))
    runs = {off: switched_off(rows, compiled, engine_name, off) for off in ((), ("NameShape",), ("DescriptionShape",))}
    assert {off: findings(v) for off, v in runs.items()} == {
        (): {("b1", "sh:minCount"), ("b2", "sh:minCount")},
        ("NameShape",): {("b2", "sh:minCount")},
        ("DescriptionShape",): {("b1", "sh:minCount")}}
    assert len(_COMPILE_CACHE) == cached
    assert compiled.active([CIM + "NameShape"]) is compiled.active([CIM + "NameShape"])
    metadata = {off: v.attrs["validation"] for off, v in runs.items()}
    assert {run["compiled_hash"] for run in metadata.values()} == {compiled.hash}
    assert {off: (run["node_shapes"], run["constraints"]) for off, run in metadata.items()} == {
        (): (1, 2), ("NameShape",): (1, 1), ("DescriptionShape",): (1, 1)}
    assert {off: run["deactivated_shapes"] for off, run in metadata.items()} == {
        (): [], ("NameShape",): [CIM + "NameShape"], ("DescriptionShape",): [CIM + "DescriptionShape"]}


def test_concat_violations_counts_each_rule_set_once():
    """Runs of one compile with different switched-off sets sum their counts;
    runs with the same set count once."""
    compiled = compiled_shapes("""
        cim:NameShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 .
        cim:DescriptionShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.description ; sh:minCount 1 .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
            sh:property cim:NameShape, cim:DescriptionShape .""")
    rows = breaker("b1", ("IdentifiedObject.description", "d"))
    every, without_name = (switched_off(rows, compiled, "pandas", off) for off in ((), ("NameShape",)))

    def counts(frames):
        merged = triplets.validation.concat_violations(frames).attrs["validation"]
        return merged["node_shapes"], merged["constraints"]
    assert counts([every, without_name]) == (2, 3)
    assert counts([without_name, without_name]) == (1, 1)


@pytest.mark.parametrize("engine_name", ENGINES)
def test_deactivated_shapes_replace_declared(engine_name):
    """deactivated_shapes is the run's switched-off set: None keeps the declared
    one, [] runs every shape, a list replaces it."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes("""
        cim:NameShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 .
        cim:DescShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.description ; sh:minCount 1 ;
            sh:deactivated true .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:NameShape, cim:DescShape .""")
    rows = breaker("b1", ("IdentifiedObject.description", "d")) + breaker("b2", ("IdentifiedObject.name", "n"))
    name, description = ("b1", "sh:minCount"), ("b2", "sh:minCount")
    assert findings(switched_off(rows, compiled, engine_name)) == {name}
    assert findings(switched_off(rows, compiled, engine_name, [])) == {name, description}
    assert findings(switched_off(rows, compiled, engine_name, ["NameShape"])) == {description}
    assert findings(switched_off(rows, compiled, engine_name, ["NameShape", "DescShape"])) == set()
    assert compiled.deactivated == {CIM + "DescShape"}
    # the same finding reads the same whichever set runs
    messages = {off: set(v.loc[v["ID"] == "b1", "MESSAGE"])
                for off, v in ((None, switched_off(rows, compiled, engine_name)),
                               ((), switched_off(rows, compiled, engine_name, [])))}
    assert messages[None] == messages[()] and messages[None]
    assert (compiled.ir["shape_id"] == CIM + "DescShape").any()


@pytest.mark.parametrize("engine_name", ENGINES)
def test_deactivated_shapes_single_iri(engine_name):
    """One shape IRI as a string is that shape, not its characters."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes("""
        cim:NameShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:NameShape .""")
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = triplets.validation.validate_shacl(data, compiled, engine=engine_name, deactivated_shapes=CIM + "NameShape")
    assert findings(v) == findings(switched_off(breaker("b1"), compiled, engine_name, ["NameShape"])) == set()


@pytest.mark.parametrize("engine_name", ENGINES)
def test_deactivated_shapes_not_member(engine_name):
    """A switched-off negated shape conforms for every value, so sh:not flags them all."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes("""cim:Banned sh:in ( "B" ) .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
            sh:property [ sh:path cim:IdentifiedObject.name ; sh:not cim:Banned ] .""")
    rows = breaker("b1", ("IdentifiedObject.name", "C")) + breaker("b2", ("IdentifiedObject.name", "B"))
    assert findings(switched_off(rows, compiled, engine_name)) == {("b2", "sh:not")}
    assert findings(switched_off(rows, compiled, engine_name, ["Banned"])) == {("b1", "sh:not"), ("b2", "sh:not")}


SHARED = """cim:P a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 .
    cim:A a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:P .
    cim:B a sh:NodeShape ; sh:targetClass cim:{target} ; sh:property cim:P ."""


@pytest.mark.parametrize("engine_name", ENGINES)
def test_deactivated_shared_property_shape(engine_name):
    """A standalone property shape switched off is off under every node shape."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes(SHARED.format(target="Disconnector"))
    rows = breaker("b1") + [("d1", "Type", "Disconnector", "eq")]
    assert findings(switched_off(rows, compiled, engine_name)) == {("b1", "sh:minCount"), ("d1", "sh:minCount")}
    assert findings(switched_off(rows, compiled, engine_name, ["P"])) == set()


@pytest.mark.parametrize("engine_name", ENGINES)
def test_deactivated_node_shape_keeps_shared_property_shape(engine_name):
    """Two node shapes share a property shape and a target: switching one off
    keeps the other's constraint."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes(SHARED.format(target="Breaker"))
    assert findings(switched_off(breaker("b1"), compiled, engine_name, ["A"])) == {("b1", "sh:minCount")}
    assert findings(switched_off(breaker("b1"), compiled, engine_name, ["A", "B"])) == set()


@pytest.mark.parametrize("engine_name", ENGINES)
def test_deactivated_shape_with_declared_false(engine_name):
    """The parameter overrides a declared sh:deactivated false."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes("""
        cim:X a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
            sh:deactivated "false"^^xsd:boolean .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:X .""")
    assert findings(switched_off(breaker("b1"), compiled, engine_name)) == {("b1", "sh:minCount")}
    assert findings(switched_off(breaker("b1"), compiled, engine_name, ["X"])) == set()


@pytest.mark.parametrize("engine_name", ENGINES)
@pytest.mark.parametrize("constraint, expected", [
    ("sh:maxCount 1", set()),
    ("sh:minCount 2", {("b1", "sh:minCount")}),
])
def test_shared_property_shape_counts_values_once(engine_name, constraint, expected):
    """One property shape under two node shapes with the same target counts each
    value once (issue #99): one name passes sh:maxCount 1 and fails sh:minCount 2."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes(SHARED.format(target="Breaker").replace("sh:minCount 1", constraint))
    assert findings(switched_off(breaker("b1", ("IdentifiedObject.name", "n")), compiled, engine_name)) == expected
    assert compiled.stats["constraints"] == 1


@pytest.mark.parametrize("engine_name", ENGINES)
def test_deactivated_shape_ill_typed_flag(engine_name):
    """An ill-typed sh:deactivated literal has no value, so the shape stays on (as pyshacl)."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes("""
        cim:X a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
            sh:deactivated "abc"^^xsd:integer .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:X .""")
    assert compiled.deactivated == frozenset()
    assert findings(switched_off(breaker("b1"), compiled, engine_name)) == {("b1", "sh:minCount")}


# one malformed shape cim:X next to a valid sibling: name missing on X, description on the sibling
MALFORMED_OWNER = {
    "property shape": """cim:X a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; {flaw} .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:X ;
            sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .""",
    "node shape": """cim:X a sh:NodeShape ; sh:targetClass cim:Breaker ; {flaw} ;
            sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
            sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .""",
}


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
@pytest.mark.parametrize("owner", MALFORMED_OWNER)
@pytest.mark.parametrize("flaw, reason", [
    ("sh:minCount 1, 2", "two sh:minCount values"),
    ("sh:datatype xsd:string, xsd:integer", "two sh:datatype values"),
    ('sh:in ( "a" ), ( "b" )', "two sh:in lists"),
    ("sh:path cim:IdentifiedObject.aliasName, cim:Conductor.length", "two sh:path values"),
    ("sh:closed true, false", "two sh:closed values"),
    ('sh:deactivated true, "1"^^xsd:integer', "two sh:deactivated values"),
    ("sh:deactivated cim:Yes", f"the sh:deactivated value {CIM}Yes is not a literal"),
    ("sh:or ( )", "empty sh:or list"),
    ("sh:and ( )", "empty sh:and list"),
    ("sh:xone ( )", "empty sh:xone list"),
    ("sh:ignoredProperties ( rdf:type )", "sh:ignoredProperties without sh:closed"),
])
def test_malformed_shape_skipped(engine_name, owner, flaw, reason):
    """A malformed shape (the compile's load checks): the engines name exactly
    that shape in skipped_shapes, with the reason, and validate the other shapes."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes(MALFORMED_OWNER[owner].format(flaw=flaw))
    violations = switched_off(breaker("b1"), compiled, engine_name)
    assert set(zip(violations["ID"], violations["KEY"])) == {("b1", "IdentifiedObject.description")}
    assert violations.attrs["validation"]["skipped_shapes"] == [f"{CIM}X: {reason}"]


# a malformed shape reached in other ways, next to the valid sibling of MALFORMED_OWNER:
# shape turtle → the skipped_shapes entries
MALFORMED_REACHED = {
    "sh:node target": ("""cim:BreakerShape sh:property cim:C .
        cim:C sh:path cim:Equipment.EquipmentContainer ; sh:node cim:T .
        cim:T a sh:NodeShape ; sh:or ( ) ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .""",
        [f"{CIM}C: sh:node target {CIM}T not evaluated", f"{CIM}T: empty sh:or list"]),
    "sh:or member": ("""cim:BreakerShape sh:or ( cim:M cim:Named ) .
        cim:M a sh:NodeShape ; sh:xone ( ) .
        cim:Named a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .""",
        [f"{CIM}BreakerShape: sh:or member {CIM}M holds a part that was not evaluated", f"{CIM}M: empty sh:xone list"]),
    "sh:not member": ("""cim:BreakerShape sh:not cim:M .
        cim:M a sh:NodeShape ; sh:xone ( ) .""",
        [f"{CIM}BreakerShape: sh:not member {CIM}M holds a part that was not evaluated", f"{CIM}M: empty sh:xone list"]),
    "sh:not member below": ("""cim:BreakerShape sh:not cim:M .
        cim:M a sh:NodeShape ; sh:property cim:P .
        cim:P sh:path cim:IdentifiedObject.name, cim:IdentifiedObject.aliasName ; sh:minCount 1 .""",
        [f"{CIM}BreakerShape: sh:not member {CIM}M holds a part that was not evaluated", f"{CIM}P: two sh:path values"]),
    "untargeted": ("""cim:Orphan a sh:NodeShape ; sh:or ( ) .""", [f"{CIM}Orphan: empty sh:or list"]),
    "two owners": ("""cim:BreakerShape sh:property cim:P .
        cim:OtherShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:P .
        cim:P sh:path cim:IdentifiedObject.name, cim:IdentifiedObject.aliasName ; sh:minCount 1 .""",
        [f"{CIM}P: two sh:path values"]),
    "sh:PropertyShape without sh:path": ("""cim:BreakerShape sh:property cim:P .
        cim:P a sh:PropertyShape ; sh:minCount 1 .""",
        [f"{CIM}P: sh:PropertyShape without sh:path"]),
}


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
@pytest.mark.parametrize("edge", MALFORMED_REACHED)
def test_malformed_shape_reached(engine_name, edge):
    """A malformed sh:node target or logical member: the row over it is not
    evaluated and named (it would conform, or fire, on every node — bay1 has no
    name, T's property shape would fire on it); a malformed shape under two
    owners is one entry; a declared sh:PropertyShape without sh:path is named
    as such, an untargeted malformed shape too; the sibling is still reported."""
    pytest.importorskip(engine_name)
    shapes, skipped = MALFORMED_REACHED[edge]
    compiled = compiled_shapes("""cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .
        """ + shapes)
    rows = breaker("b1", ("Equipment.EquipmentContainer", "bay1")) + [("bay1", "Type", "Bay", "eq")]
    violations = switched_off(rows, compiled, engine_name)
    assert set(zip(violations["ID"], violations["KEY"])) == {("b1", "IdentifiedObject.description")}
    assert violations.attrs["validation"]["skipped_shapes"] == skipped


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
@pytest.mark.parametrize("path, expressible", [
    ("cim:IdentifiedObject.name", True),
    ("[ sh:alternativePath ( cim:IdentifiedObject.name cim:IdentifiedObject.aliasName ) ]", False),
])
def test_malformed_part_of_sh_node_target(engine_name, path, expressible):
    """A sh:node target T with a malformed P and a sibling Q: pyshacl judges T
    without P (bay1 has no name); so do the vectorized engines when they can
    express Q, else nothing is left for them and they name the row not evaluated."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes(f"""cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
            sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ; sh:property cim:C .
        cim:C sh:path cim:Equipment.EquipmentContainer ; sh:node cim:T .
        cim:T a sh:NodeShape ; sh:property cim:P, cim:Q .
        cim:P sh:path cim:IdentifiedObject.name, cim:IdentifiedObject.aliasName ; sh:minCount 1 .
        cim:Q sh:path {path} ; sh:minCount 1 .""")
    rows = breaker("b1", ("Equipment.EquipmentContainer", "bay1")) + [("bay1", "Type", "Bay", "eq")]
    violations = switched_off(rows, compiled, engine_name)
    evaluated = expressible or engine_name == "pyshacl"
    node_finding = {("b1", "Equipment.EquipmentContainer")} if evaluated else set()
    assert set(zip(violations["ID"], violations["KEY"])) == {("b1", "IdentifiedObject.description"), *node_finding}
    assert violations.attrs["validation"]["skipped_shapes"] == ([f"{CIM}P: two sh:path values"] if evaluated else [
        f"{CIM}C: sh:node target {CIM}T not evaluated", f"{CIM}P: two sh:path values", f"{CIM}Q: unsupported sh:path form"])


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
@pytest.mark.parametrize("flaw, reason", [("sh:minCount 1, 2", "two sh:minCount values"),
                                          ("sh:path cim:IdentifiedObject.aliasName", "two sh:path values")])
def test_malformed_deactivated_shape(engine_name, flaw, reason):
    """A declared sh:deactivated shape that is malformed: switched off (None) it
    takes its entry along, switched on ([]) it is skipped with the reason."""
    pytest.importorskip(engine_name)
    compiled = compiled_shapes(MALFORMED_OWNER["property shape"].format(flaw=f"sh:deactivated true ; {flaw}"))
    assert compiled.deactivated == {f"{CIM}X"}
    for shapes, skipped in ((None, []), ([], [f"{CIM}X: {reason}"])):
        violations = switched_off(breaker("b1"), compiled, engine_name, shapes)
        assert set(zip(violations["ID"], violations["KEY"])) == {("b1", "IdentifiedObject.description")}
        assert violations.attrs["validation"]["skipped_shapes"] == skipped


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
def test_report_shapes_carry_the_run_flags(engine_name):
    """The report's embedded shapes say sh:deactivated as the run used it: a
    re-enabled shape without it, a shape switched off by the parameter with it."""
    import rdflib
    pytest.importorskip(engine_name)
    compiled = compiled_shapes("""cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:closed true ; sh:ignoredProperties ( rdf:type ) ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; sh:deactivated true ] ;
        sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .""")
    by_path = dict(zip(compiled.ir["path"], compiled.ir["shape_id"]))
    name, description = by_path["IdentifiedObject.name"], by_path["IdentifiedObject.description"]
    rows = breaker("b1", ("IdentifiedObject.name", "n"), ("IdentifiedObject.description", "d"),
                   ("IdentifiedObject.aliasName", "a"))
    deactivated = rdflib.URIRef("http://www.w3.org/ns/shacl#deactivated")
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    for off, flagged in (([], set()), ([description], {description})):
        v = triplets.validation.validate_shacl(data, compiled, engine=engine_name, lexical=False,
                                               deactivated_shapes=off)
        assert findings(v) == {("b1", "sh:closed")}
        shapes = v.attrs["validation"]["source_shapes"]
        assert set(shapes) == {CIM + "BreakerShape"}
        cbd = shapes[CIM + "BreakerShape"]
        assert {str(shape) for shape, _ in cbd.subject_objects(deactivated)} == flagged
        assert {name, description} <= {str(node) for node in cbd.subjects()}
    assert len(list(compiled.graph.subject_objects(deactivated))) == 1   # the compiled graph is untouched


@pytest.mark.parametrize("engine_name", ENGINES)
def test_shape_on_parent_and_child_reports_once(engine_name):
    """One property shape on both a parent and a child node shape (CGMES SHACL
    attaches inherited attributes to each) reaches a Breaker twice: one finding, like pyshacl."""
    pytest.importorskip(engine_name)
    shape = """cim:NameShape a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 .
        cim:SwitchShape a sh:NodeShape ; sh:targetClass cim:Switch ; sh:property cim:NameShape .
        cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:NameShape ."""
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    v = triplets.validation.validate_shacl(data, graph, engine=engine_name, rdf_map=TOY_SCHEMA, rdf_map_scope="*")
    assert (v["VIOLATION_TYPE"] == "sh:minCount").sum() == 1


@pytest.mark.parametrize("engine_name", ENGINES)
def test_inheritance_off_matches_exact_type(engine_name):
    """inheritance=False: sh:targetClass Equipment no longer reaches a Breaker."""
    pytest.importorskip(engine_name)
    shape = """cim:EqShape a sh:NodeShape ; sh:targetClass cim:Equipment ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    on = triplets.validation.validate_shacl(data, graph, engine=engine_name, rdf_map=TOY_SCHEMA, rdf_map_scope="*")
    off = triplets.validation.validate_shacl(data, graph, engine=engine_name, rdf_map=TOY_SCHEMA, rdf_map_scope="*", inheritance=False)
    assert violating(on, "sh:minCount") == {("b1", None)}
    assert violating(off, "sh:minCount") == set()
    assert off.attrs["validation"]["inheritance"] is False


# NC-style bundle: SSI's ACLineSegment list stops at Equipment; Breaker's EQ list
# runs on to IdentifiedObject; ER has its own Equipment entry; TP and EQ define
# ConnectivityNode with different lists
SCOPED_SCHEMA = {
    "SSI": {"ACLineSegment": {"type": "Class", "namespace": CIM,
                              "inheritance": [CIM + "ACLineSegment", CIM + "Equipment"]}},
    "EQ": {"Breaker": {"type": "Class", "namespace": CIM,
                       "inheritance": [CIM + name for name in ("Breaker", "Switch", "Equipment",
                                                               "PowerSystemResource", "IdentifiedObject")]},
           "ConnectivityNode": {"type": "Class", "namespace": CIM,
                                "inheritance": [CIM + "ConnectivityNode", CIM + "IdentifiedObject"]}},
    "ER": {"Equipment": {"type": "Class", "namespace": CIM,
                         "inheritance": [CIM + "Equipment", CIM + "PowerSystemResource", CIM + "IdentifiedObject"]}},
    "TP": {"ConnectivityNode": {"type": "Class", "namespace": CIM, "inheritance": [CIM + "ConnectivityNode"]}},
}
IO_SHAPE = """cim:IoShape a sh:NodeShape ; sh:targetClass cim:IdentifiedObject ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""


def io_shapes():
    import rdflib
    return rdflib.Graph().parse(data=PREFIX + IO_SHAPE, format="turtle")


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
@pytest.mark.parametrize("instance_type, rdf_map_scope, reached", [
    ("ACLineSegment", ["SSI"], False),
    ("ACLineSegment", ["SSI", "EQ"], False),      # Breaker's list is no definition of Equipment
    ("ACLineSegment", ["SSI", "ER"], True),       # through ER's own Equipment entry
    ("ConnectivityNode", ["TP"], False),
    ("ConnectivityNode", ["TP", "EQ"], True),     # same-named entries union their lists
    ("ConnectivityNode", "*", True),
], ids=["ssi", "ssi+eq", "ssi+er", "tp", "tp+eq", "all"])
def test_inheritance_follows_the_scoped_profiles(engine_name, instance_type, rdf_map_scope, reached):
    """sh:targetClass IdentifiedObject reaches an instance only through the scoped
    profiles' own Class entries — the same on every engine, pyshacl included."""
    pytest.importorskip(engine_name)
    data = pandas.DataFrame([("a1", "Type", instance_type, "eq")], columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = triplets.validation.validate_shacl(data, io_shapes(), engine=engine_name, rdf_map=SCOPED_SCHEMA,
                                     rdf_map_scope=rdf_map_scope)
    assert violating(v, "sh:minCount") == ({("a1", None)} if reached else set())


@pytest.mark.parametrize("engine_name", ENGINES)
def test_empty_data_scope_loads_no_instance(engine_name):
    """data_scope=[] validates nothing, on every engine."""
    pytest.importorskip(engine_name)
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    assert triplets.validation.validate_shacl(data, io_shapes(), engine=engine_name, data_scope=[]).empty


def test_rdf_map_scope_needs_an_rdf_map():
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    with pytest.raises(ValueError, match="pass the rdf_map"):
        triplets.validation.validate_shacl(data, io_shapes(), rdf_map_scope="EQ")


def test_inheritance_metadata_says_whether_it_was_applied():
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    assert triplets.validation.validate_shacl(data, io_shapes(), engine="pandas").attrs["validation"][
        "inheritance"] is False                                     # no rdf_map: nothing applied
    applied = triplets.validation.validate_shacl(data, io_shapes(), engine="pandas", rdf_map=SCOPED_SCHEMA,
                                                 rdf_map_scope="*")
    assert applied.attrs["validation"]["inheritance"] is True


def test_rdf_map_without_scope_entries_raises():
    """A sectioned bundle has no root entries: rdf_map_scope=None is a caller error."""
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    with pytest.raises(ValueError, match="rdf_map_scope"):
        triplets.validation.validate_shacl(data, io_shapes(), rdf_map=SCOPED_SCHEMA)


def test_merge_conflicts_reach_the_run_metadata():
    """A datatype conflict between the scoped profiles is listed as not checked, on pyshacl too."""
    pytest.importorskip("pyshacl")
    schema = {"A": {"Model.version": {"type": "Attribute", "xsd:type": "xsd:string"}},
              "B": {"Model.version": {"type": "Attribute", "xsd:type": "xsd:integer"}}}
    data = pandas.DataFrame(breaker("b1"), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    for name in ("pandas", "pyshacl"):
        v = triplets.validation.validate_shacl(data, io_shapes(), engine=name, rdf_map=schema, rdf_map_scope="*")
        assert any("Model.version: datatype conflict" in entry
                   for entry in v.attrs["validation"]["skipped_shapes"]), name


def test_pyshacl_ont_graph_abstract_target():
    """pyshacl + rdf_map: sh:targetClass Equipment hits concrete subclasses via
    ont_graph rdfs:subClassOf — instance Type stays Breaker."""
    pytest.importorskip("pyshacl")
    shape = """cim:EqShape a sh:NodeShape ; sh:targetClass cim:Equipment ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = breaker("b1") + [("d1", "Type", "Disconnector", "eq"),
                            ("x1", "Type", "Substation", "eq")]
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    bare = triplets.validation.validate_shacl(data, graph, engine="pyshacl")
    assert violating(bare, "sh:minCount") == set()
    bound = triplets.validation.validate_shacl(data, graph, engine="pyshacl", rdf_map=TOY_SCHEMA, rdf_map_scope="*")
    assert violating(bound, "sh:minCount") == {("b1", None), ("d1", None)}


# property-level logical operators: a nested shape without its own sh:path
# constrains the property's value nodes (SHACL: each value node conforms)
VALUE_OR = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.name ;
        sh:or ( [ sh:in ( "A" ) ] [ sh:pattern "^B" ] ) ] ."""
VALUE_NOT = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:not [ sh:in ( "B" ) ] ] ."""


@pytest.mark.parametrize("engine_name", ENGINES)
@pytest.mark.parametrize("shape, properties, expected", [
    (VALUE_OR, [("IdentifiedObject.name", "C")], {("b1", "C")}),
    (VALUE_OR, [("IdentifiedObject.name", "A"), ("IdentifiedObject.name", "Bx")], set()),   # each value passes one
    (VALUE_OR, [("IdentifiedObject.name", "A"), ("IdentifiedObject.name", "C")], {("b1", "C")}),
    (VALUE_NOT, [("IdentifiedObject.name", "C")], set()),
    (VALUE_NOT, [("IdentifiedObject.name", "B")], {("b1", "B")}),
], ids=["or-fails", "or-each-value-passes", "or-one-value-fails", "not-passes", "not-fails"])
def test_property_logical_operators_judge_value_nodes(engine_name, shape, properties, expected):
    pytest.importorskip(engine_name)
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    data = pandas.DataFrame(breaker("b1", *properties), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    v = triplets.validation.validate_shacl(data, graph, engine=engine_name, lexical=False)
    component = "sh:or" if shape is VALUE_OR else "sh:not"
    assert violating(v, component) == expected


# ── sh:targetNode / sh:targetObjectsOf ───────────────────────────────────────

def frame(rows):
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


# the association typed as a reference, as the CGMES schema exports it
REFERENCES = {"EQ": {"Equipment.EquipmentContainer": {"type": "Association"}}}


def test_target_node_present_and_absent(engine):
    """sh:targetNode picks one ID, in the data or not — an absent node still
    fails sh:minCount (SHACL: a targetNode is a focus node unconditionally)."""
    shape = """cim:Picked a sh:NodeShape ;
        sh:targetNode <urn:uuid:b1>, <urn:uuid:absent> ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = breaker("b1") + breaker("b2")
    assert violating(run(rows, shape, engine), "sh:minCount") == {("b1", None), ("absent", None)}


def test_target_node_virtual_iri_with_sparql(engine):
    """The ENTSO-E idiom: sh:targetNode names a class IRI that is in no data,
    only to give a dataset-wide sh:sparql constraint a focus; the finding's ID
    is that IRI and VALUE the offending object."""
    shape = '''cim:Virtual a sh:NodeShape ; sh:targetNode cim:IDuniqueness ;
        sh:property [ sh:path rdf:type ;
            sh:sparql [ a sh:SPARQLConstraint ; sh:message "no name" ;
                sh:select """SELECT $this ?value WHERE { ?value a <http://iec.ch/TC57/CIM100#Breaker> .
                           FILTER NOT EXISTS { ?value <http://iec.ch/TC57/CIM100#IdentifiedObject.name> ?n } }""" ] ] .'''
    rows = breaker("b1", ("IdentifiedObject.name", "n")) + breaker("b2")
    assert violating(run(rows, shape, engine), "sh:sparql") == {("http://iec.ch/TC57/CIM100#IDuniqueness", "b2")}


def test_target_node_under_data_scope(engine):
    """The picked ID stays a focus node whatever the scope; its rows are read
    from the scoped instances only."""
    shape = """cim:Picked a sh:NodeShape ; sh:targetNode <urn:uuid:b1> ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = breaker("b1", ("IdentifiedObject.name", "n")) + [("b1", "Type", "Breaker", "other")]
    assert violating(run(rows, shape, engine), "sh:minCount") == set()
    assert violating(run(rows, shape, engine, data_scope=["other"]), "sh:minCount") == {("b1", None)}


def test_target_objects_of(engine):
    """sh:targetObjectsOf picks the VALUEs under the KEY — the referenced
    objects, including one the data does not describe."""
    shape = """cim:Containers a sh:NodeShape ;
        sh:targetObjectsOf cim:Equipment.EquipmentContainer ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "bay1"))
            + breaker("b2", ("Equipment.EquipmentContainer", "bay2"))
            + breaker("b3", ("Equipment.EquipmentContainer", "dangling"))
            + [("bay1", "Type", "Bay", "eq"), ("bay1", "IdentifiedObject.name", "B1", "eq"),
               ("bay2", "Type", "Bay", "eq")])
    assert violating(run(rows, shape, engine, rdf_map=REFERENCES), "sh:minCount") == {("bay2", None), ("dangling", None)}


def test_target_objects_of_literal_value(engine):
    """A literal VALUE is a focus node too (SHACL targets any object node)."""
    shape = """cim:Names a sh:NodeShape ; sh:targetObjectsOf cim:IdentifiedObject.name ;
        sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ."""
    rows = breaker("b1", ("IdentifiedObject.name", "n"))
    assert violating(run(rows, shape, engine), "sh:minCount") == {("n", None)}


def test_target_objects_of_under_data_scope(engine):
    shape = """cim:Containers a sh:NodeShape ;
        sh:targetObjectsOf cim:Equipment.EquipmentContainer ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "bay1"))
            + [("b2", "Type", "Breaker", "other"), ("b2", "Equipment.EquipmentContainer", "bay2", "other")])
    assert violating(run(rows, shape, engine, rdf_map=REFERENCES), "sh:minCount") == {("bay1", None), ("bay2", None)}
    assert violating(run(rows, shape, engine, data_scope=["other"], rdf_map=REFERENCES), "sh:minCount") == {("bay2", None)}


def test_targets_other_than_type_never_widen(engine):
    """Only target_by="Type" (and sh:class) follow the schema inheritance."""
    shape = """cim:Picked a sh:NodeShape ; sh:targetObjectsOf cim:Equipment.EquipmentContainer ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."""
    rows = breaker("b1", ("Equipment.EquipmentContainer", "bay1")) + [("bay1", "Type", "Bay", "eq"),
                                                                       ("sub1", "Type", "Substation", "eq")]
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=PREFIX + shape, format="turtle")
    v = triplets.validation.validate_shacl(frame(rows), graph, engine=engine, rdf_map=TOY_SCHEMA, rdf_map_scope="*")
    assert violating(v, "sh:minCount") == {("bay1", None)}


# ── node-level logical operators; members with their own property shapes ─────

def test_node_level_logical_operators(engine):
    """sh:or / sh:not on the NodeShape judge the focus nodes themselves."""
    shape = """cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:or ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]
                [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) ;
        sh:not [ sh:path cim:Switch.state ; sh:hasValue "banned" ] ."""
    rows = (breaker("named", ("IdentifiedObject.name", "n")) + breaker("described", ("IdentifiedObject.description", "d"))
            + breaker("bare") + breaker("banned", ("IdentifiedObject.name", "n"), ("Switch.state", "banned")))
    for component, expected in (("sh:or", {("bare", None)}), ("sh:not", {("banned", None)})):
        assert violating(run(rows, shape, engine), component) == expected, component


def test_logical_member_with_property_shapes(engine):
    """A NodeShape as a member: under a path its property shapes judge the value
    nodes; at node level they judge the focus."""
    shape = """cim:Named a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
    cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:or ( cim:Named [ sh:hasValue <urn:uuid:ok> ] ) ] ;
        sh:not cim:Named ."""
    rows = (breaker("b1", ("Equipment.EquipmentContainer", "named"))                     # value conforms
            + breaker("b2", ("Equipment.EquipmentContainer", "ok"))                      # second alternative
            + breaker("b3", ("Equipment.EquipmentContainer", "bare"))                    # neither
            + breaker("b4", ("IdentifiedObject.name", "n"))                              # conforms to Named → sh:not
            + [("named", "Type", "Bay", "eq"), ("named", "IdentifiedObject.name", "N", "eq"),
               ("ok", "Type", "Bay", "eq"), ("bare", "Type", "Bay", "eq")])
    assert violating(run(rows, shape, engine, rdf_map=REFERENCES), "sh:or") == {("b3", "bare")}
    assert violating(run(rows, shape, engine, rdf_map=REFERENCES), "sh:not") == {("b4", None)}


def test_operator_over_unknown_component_reports_nothing(engine):
    """sh:or ( [ sh:not <qualifiedValueShape shape> ] [...] ) is not evaluated on
    the member's expressible part (that would flag every focus node); the run
    says so in skipped_shapes."""
    shape = """cim:Q a sh:NodeShape ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:qualifiedValueShape [ sh:hasValue "n" ] ; sh:qualifiedMinCount 1 ] .
    cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:or ( [ sh:not cim:Q ] [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) ."""
    rows = breaker("b1", ("IdentifiedObject.name", "n")) + breaker("b2")
    v = run(rows, shape, engine)
    assert v.empty
    assert any("sh:not member http://iec.ch/TC57/CIM100#Q uses" in entry
               for entry in v.attrs["validation"]["skipped_shapes"])


def test_node_level_value_constraint_is_reported_not_run(engine):
    """sh:in on a node shape judges the focus node itself: the vectorized
    engines do not evaluate it (pyshacl does) and the run says so."""
    shape = "cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:in ( <urn:uuid:b1> ) ."
    v = run(breaker("b1") + breaker("b2"), shape, engine)
    assert v.empty
    assert "http://iec.ch/TC57/CIM100#N: node-level sh:in not evaluated" in v.attrs["validation"]["skipped_shapes"]


@pytest.mark.parametrize("engine_name", ENGINES)
def test_node_level_operators_over_deactivated_shapes(engine_name):
    """At node level too: a deactivated negated shape conforms for every focus,
    so sh:not flags them all; a deactivated sh:and member holds."""
    pytest.importorskip(engine_name)
    rows = breaker("b1", ("IdentifiedObject.name", "n")) + breaker("b2")
    negated = """cim:Named a sh:NodeShape ; sh:deactivated true ;
            sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
        cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:not cim:Named ."""
    conjunct = """cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:and ( [ sh:deactivated true ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ] ) ."""
    assert findings(run(rows, negated, engine_name)) == {("b1", "sh:not"), ("b2", "sh:not")}
    assert findings(run(rows, conjunct, engine_name)) == set()


# ── node-level sh:node ───────────────────────────────────────────────────────

NAMED = "cim:Named a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."


def test_node_level_node(engine):
    """sh:node on the NodeShape judges the focus node itself: one finding per
    non-conforming focus, through a chain of references too."""
    rows = breaker("named", ("IdentifiedObject.name", "n")) + breaker("bare")
    direct = "cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:node cim:Named .\n" + NAMED
    chain = ("cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:node cim:Mid .\n"
             "cim:Mid a sh:NodeShape ; sh:node cim:Named .\n" + NAMED)
    for shape in (direct, chain):
        assert violating(run(rows, shape, engine), "sh:node") == {("bare", None)}


def test_node_level_node_with_own_property_shapes(engine):
    """The referenced shape and the node shape's own property shapes judge the same focus."""
    shape = """cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:node cim:Named ;
        sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .\n""" + NAMED
    rows = (breaker("both", ("IdentifiedObject.name", "n"), ("IdentifiedObject.description", "d"))
            + breaker("named", ("IdentifiedObject.name", "n")) + breaker("described", ("IdentifiedObject.description", "d")))
    assert violating(run(rows, shape, engine), "sh:node") == {("described", None)}
    assert violating(run(rows, shape, engine), "sh:minCount") == {("named", None)}


@pytest.mark.parametrize("engine_name", ENGINES)
def test_node_level_node_over_deactivated_shape(engine_name):
    """A deactivated referenced shape conforms for every focus, declared or switched off."""
    pytest.importorskip(engine_name)
    rows = breaker("b1", ("IdentifiedObject.name", "n")) + breaker("b2")
    shape = "cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:node cim:Named .\n" + NAMED
    assert ("b2", "sh:node") in findings(switched_off(rows, compiled_shapes(shape), engine_name))
    assert findings(switched_off(rows, compiled_shapes(shape), engine_name, ["Named"])) == set()
    declared = shape.replace("cim:Named a sh:NodeShape ;", "cim:Named a sh:NodeShape ; sh:deactivated true ;")
    assert findings(run(rows, declared, engine_name)) == set()


# ── sh:xone ──────────────────────────────────────────────────────────────────

def test_xone_node_level(engine):
    """Exactly one alternative: both or neither violates."""
    shape = """cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:xone ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]
                  [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) ."""
    rows = (breaker("both", ("IdentifiedObject.name", "n"), ("IdentifiedObject.description", "d"))
            + breaker("name", ("IdentifiedObject.name", "n")) + breaker("neither"))
    assert violating(run(rows, shape, engine), "sh:xone") == {("both", None), ("neither", None)}


def test_xone_property_level_judges_values(engine):
    """Under a path, each value node needs exactly one alternative."""
    shape = """cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:xone ( [ sh:pattern "^A" ] [ sh:pattern "Z$" ] ) ] ."""
    rows = breaker("b1", ("IdentifiedObject.name", "AZ"), ("IdentifiedObject.name", "Ax"),
                   ("IdentifiedObject.name", "xx"))
    assert violating(run(rows, shape, engine), "sh:xone") == {("b1", "AZ"), ("b1", "xx")}


def test_xone_deactivated_member_conforms(engine):
    """A deactivated member is satisfied for every node: with the name present
    two alternatives hold."""
    shape = """cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:xone ( [ sh:deactivated true ; sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ]
                  [ sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ] ) ."""
    rows = breaker("b1", ("IdentifiedObject.name", "n")) + breaker("b2")
    assert violating(run(rows, shape, engine), "sh:xone") == {("b1", None)}


# ── SPARQL targets (sh:target [ a sh:SPARQLTarget ; sh:select … ]) ───────────

# the CGMES Equipment target: power transformers without a third winding
TWO_WINDING = """SELECT DISTINCT ?this WHERE {
    ?this rdf:type cim:PowerTransformer .
    FILTER NOT EXISTS { ?this ^cim:PowerTransformerEnd.PowerTransformer/cim:TransformerEnd.endNumber 3 } . }"""
NAME_TOO_LONG = "sh:property [ sh:path cim:IdentifiedObject.name ; sh:maxLength 2 ] ."
# a vectorized rule and one the polars / duckdb engines hand to pandas (which resolves the target again)
NAME_RULES = """sh:property [ sh:path cim:IdentifiedObject.name ; sh:maxLength 2 ] ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:or ( [ sh:minLength 9 ] [ sh:pattern "^x" ] ) ] ."""


def sparql_target(select, body=NAME_TOO_LONG):
    """cim:TwoWinding targeted by *select*, its prefixes declared on the cim: ontology node."""
    return f'''
cim: sh:declare [ sh:prefix "cim" ; sh:namespace "http://iec.ch/TC57/CIM100#"^^xsd:anyURI ] ,
                [ sh:prefix "rdf" ; sh:namespace "http://www.w3.org/1999/02/22-rdf-syntax-ns#"^^xsd:anyURI ] .
cim:TwoWinding a sh:NodeShape ;
    sh:target [ a sh:SPARQLTarget ; sh:prefixes cim: ; sh:select """{select}""" ] ;
    {body}'''


# endNumber typed and the end → transformer link an IRI, as the CGMES schema exports them
TRANSFORMERS = {"EQ": {"TransformerEnd.endNumber": {"type": "Attribute", "xsd:type": "xsd:integer", "namespace": CIM},
                       "PowerTransformerEnd.PowerTransformer": {"type": "Association"}}}


def transformer(object_id, windings):
    """Rows for one cim:PowerTransformer named PT-<id> with *windings* ends."""
    rows = [(object_id, "Type", "PowerTransformer", "eq"),
            (object_id, "IdentifiedObject.name", f"PT-{object_id}", "eq")]
    for number in range(1, windings + 1):
        end = f"{object_id}-end{number}"
        rows += [(end, "Type", "PowerTransformerEnd", "eq"), (end, "TransformerEnd.endNumber", str(number), "eq"),
                 (end, "PowerTransformerEnd.PowerTransformer", object_id, "eq")]
    return rows


TRANSFORMER_ROWS = transformer("pt1", 2) + transformer("pt2", 2) + transformer("pt3", 3)


def test_sparql_target(engine):
    """The query, not the Type, picks the focus: every name is too long, only
    the two-winding transformers are reported."""
    assert violating(run(TRANSFORMER_ROWS, sparql_target(TWO_WINDING), engine, rdf_map=TRANSFORMERS), "sh:maxLength") \
        == {("pt1", "PT-pt1"), ("pt2", "PT-pt2")}


def test_sparql_target_with_sparql_constraint(engine):
    """A sh:sparql constraint under a SPARQL target binds the target's focus nodes."""
    shape = sparql_target(TWO_WINDING, """sh:property [ sh:path cim:IdentifiedObject.name ;
        sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value }' ] ] .""")
    assert violating(run(TRANSFORMER_ROWS, shape, engine, rdf_map=TRANSFORMERS), "sh:sparql") \
        == {("pt1", "PT-pt1"), ("pt2", "PT-pt2")}


def test_sparql_target_and_target_class(engine):
    """A shape with both targets: the overlap is reported once."""
    shape = sparql_target(TWO_WINDING, "sh:targetClass cim:PowerTransformer ; " + NAME_TOO_LONG)
    assert violating(run(TRANSFORMER_ROWS, shape, engine, rdf_map=TRANSFORMERS), "sh:maxLength") \
        == {("pt1", "PT-pt1"), ("pt2", "PT-pt2"), ("pt3", "PT-pt3")}


def test_sparql_target_without_select_is_skipped(engine):
    """A custom sh:target without sh:select: no findings, named in skipped_shapes
    (pyshacl rejects a bare-IRI sh:target: ShapeLoadError)."""
    violations = run(TRANSFORMER_ROWS, "cim:Custom a sh:NodeShape ; sh:target cim:SomeTarget ; " + NAME_TOO_LONG, engine)
    assert len(violations) == 0
    assert violations.attrs["validation"]["skipped_shapes"] == [CIM + "Custom: sh:target not walked (no sh:select)"]


def test_sparql_target_defective_query(engine):
    """A target query no engine can run: one Violation naming the NodeShape,
    the shape is not evaluated."""
    violations = run(TRANSFORMER_ROWS, sparql_target("SELECT ?this WHERE { ?this nc:undeclared ?x }", NAME_RULES),
                     engine)
    assert list(violations["VIOLATION_TYPE"]) == ["triplets:invalidSparql"]
    note = violations.iloc[0]
    assert (note["SEVERITY"], note["SOURCE_SHAPE"]) == ("Violation", CIM + "TwoWinding")
    assert "SPARQL target of shape TwoWinding was NOT evaluated — the query is defective" in note["MESSAGE"]


def test_shared_defective_sparql_target_flags_every_shape(engine):
    """Two NodeShapes with the same defective target query: the query runs
    once, each shape gets its own Violation."""
    target = 'sh:target [ sh:select "SELECT ?this WHERE { ?this nc:undeclared ?x }" ]'
    shape = f"""cim:A a sh:NodeShape ; {target} ; {NAME_RULES}
        cim:B a sh:NodeShape ; {target} ; {NAME_RULES}"""
    violations = run(TRANSFORMER_ROWS, shape, engine)
    assert sorted(zip(violations["VIOLATION_TYPE"], violations["SEVERITY"], violations["SOURCE_SHAPE"])) == [
        ("triplets:invalidSparql", "Violation", CIM + "A"), ("triplets:invalidSparql", "Violation", CIM + "B")]


def test_sparql_target_without_this(engine):
    """A target query that projects no ?this gives the shape no focus and one Violation."""
    violations = run(TRANSFORMER_ROWS, sparql_target("SELECT ?x WHERE { ?x rdf:type cim:PowerTransformer }"), engine)
    assert list(zip(violations["VIOLATION_TYPE"], violations["SEVERITY"])) == [("triplets:invalidSparql", "Violation")]
    assert "TwoWinding was NOT evaluated — the query projects no ?this" in violations["MESSAGE"].iloc[0]


def test_sparql_target_rejected_by_strict_engine(engine):
    """A target query only rdflib accepts (a non-grouped projection): the focus
    still comes from rdflib, and one Warning flags the NodeShape — also when the
    pandas fallback (sh:or) resolves the target a second time."""
    from triplets import sparql
    if sparql.get_engine("auto")[0] == "rdflib":
        pytest.skip("needs a strict auto SPARQL engine (qlever or oxigraph)")
    shape = sparql_target(TWO_WINDING.replace("SELECT DISTINCT ?this", "SELECT ?this ?name").replace(
        "cim:PowerTransformer .", "cim:PowerTransformer ; cim:IdentifiedObject.name ?name .") + " GROUP BY ?this",
        NAME_RULES)
    violations = triplets.validation.validate_shacl(
        frame(TRANSFORMER_ROWS), compiled_shapes(shape), engine=engine, rdf_map=TRANSFORMERS, rdf_map_scope="*")
    assert violating(violations, "sh:maxLength") == violating(violations, "sh:or") \
        == {("pt1", "PT-pt1"), ("pt2", "PT-pt2")}
    notes = violations[violations["VIOLATION_TYPE"] == "triplets:invalidSparql"]
    assert list(zip(notes["SEVERITY"], notes["SOURCE_SHAPE"])) == [("Warning", CIM + "TwoWinding")]
    assert "the SPARQL target WAS still evaluated" in notes["MESSAGE"].iloc[0]


@pytest.mark.parametrize("engine_name", ENGINES)
def test_sparql_target_deactivated(engine_name):
    pytest.importorskip(engine_name)
    compiled = compiled_shapes(sparql_target(TWO_WINDING))
    assert len(switched_off(TRANSFORMER_ROWS, compiled, engine_name, [])) == 3   # no rdf_map: every transformer
    violations = switched_off(TRANSFORMER_ROWS, compiled, engine_name, ["TwoWinding"])
    assert len(violations) == 0
    assert violations.attrs["validation"]["deactivated_shapes"] == [CIM + "TwoWinding"]


# ── nested semantics ─────────────────────────────────────────────────────────

DESC = "cim:Desc a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ."
# b1 named → bay1 (named), b2 described → sub1 (bare), b3 bare
TRIO = (breaker("b1", ("IdentifiedObject.name", "B1"), ("Equipment.EquipmentContainer", "bay1"))
        + breaker("b2", ("IdentifiedObject.description", "d"), ("Equipment.EquipmentContainer", "sub1"))
        + breaker("b3")
        + [("bay1", "Type", "Bay", "eq"), ("bay1", "IdentifiedObject.name", "Bay 1", "eq"),
           ("sub1", "Type", "Substation", "eq")])
# breakers referencing each other: b1 (named) → b2, b2 (bare) → b1; b3 described
RING = (breaker("b1", ("IdentifiedObject.name", "n"), ("Equipment.EquipmentContainer", "b2"))
        + breaker("b2", ("Equipment.EquipmentContainer", "b1")) + breaker("b3", ("IdentifiedObject.description", "d")))
NODE_B = "cim:B a sh:NodeShape ; sh:targetClass cim:Breaker ;"


def prop(body):
    return f"{NODE_B} sh:property [ {body} ] ."


@pytest.mark.parametrize("shape, rows, component, expected", [
    (prop("sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Named, cim:Desc") + NAMED + DESC, RING,
     "sh:node", {("b1", "b2"), ("b2", "b1")}),
    (f"{NODE_B} sh:node cim:Named, cim:Desc ." + NAMED + DESC, TRIO, "sh:node", {("b1", None), ("b2", None), ("b3", None)}),
    (f"{NODE_B} sh:or ( cim:Named ), ( cim:Desc ) ." + NAMED + DESC, TRIO, "sh:or", {("b1", None), ("b2", None), ("b3", None)}),
    (f"{NODE_B} sh:xone ( cim:Named ), ( cim:Desc ) ." + NAMED + DESC, TRIO, "sh:xone", {("b1", None), ("b2", None), ("b3", None)}),
    (f"{NODE_B} sh:not cim:Named, cim:Desc ." + NAMED + DESC, TRIO, "sh:not", {("b1", None), ("b2", None)}),
    (prop('sh:path cim:IdentifiedObject.name ; sh:pattern "^B", "1$"'),
     breaker("b1", ("IdentifiedObject.name", "B1")) + breaker("b2", ("IdentifiedObject.name", "B2"))
     + breaker("b3", ("IdentifiedObject.name", "x1")), "sh:pattern", {("b2", "B2"), ("b3", "x1")}),
    (prop("sh:path cim:Equipment.EquipmentContainer ; sh:class cim:Bay, cim:Substation"), TRIO,
     "sh:class", {("b1", "bay1"), ("b2", "sub1")}),
    (prop('sh:path cim:IdentifiedObject.name ; sh:hasValue "B1", "B2"'),
     breaker("b1", ("IdentifiedObject.name", "B1")) + breaker("b2", ("IdentifiedObject.name", "B2"))
     + breaker("b3", ("IdentifiedObject.name", "B1"), ("IdentifiedObject.name", "B2")),
     "sh:hasValue", {("b1", None), ("b2", None)}),
], ids=["prop_node", "node_node", "or", "xone", "not", "pattern", "class", "hasValue"])
def test_every_value_of_a_parameter_is_a_constraint(engine, shape, rows, component, expected):
    """A parameter given twice is two constraints (SHACL §4.1), as pyshacl
    evaluates them: issue #163 for sh:node, the same for the other repeatable ones."""
    assert violating(run(rows, shape, engine), component) == expected


def operator_rows(violations, component):
    rows = violations[violations["VIOLATION_TYPE"] == component]
    return sorted(zip(rows["ID"], rows["KEY"].astype(object).where(rows["KEY"].notna(), None),
                      rows["VALUE"].astype(object).where(rows["VALUE"].notna(), None), rows["SOURCE_SHAPE"]))


BOTH = """cim:Both a sh:NodeShape ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ;
    sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ."""


@pytest.mark.parametrize("shape", [
    f"{NODE_B} sh:and ( cim:Both ) ." + BOTH,
    f"{NODE_B} sh:and ( cim:Named cim:Desc ) ." + NAMED + DESC,
    f"{NODE_B} sh:and ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] "
    f"[ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) .",
    f"{NODE_B} sh:and ( cim:Named ), ( cim:Desc ) ." + NAMED + DESC,
], ids=["one_shape", "two_shapes", "two_paths", "two_lists"])
def test_node_level_and_reports_the_focus(engine, shape):
    """Node-level sh:and: one row per non-conforming focus node with the
    operator's shape and no KEY, as pyshacl (#164) — not one per nested finding."""
    rows = (breaker("b1", ("IdentifiedObject.name", "n")) + breaker("b2")
            + breaker("b3", ("IdentifiedObject.description", "d")) + breaker("b4", ("IdentifiedObject.name", "n"),
                                                                              ("IdentifiedObject.description", "d")))
    found = set(operator_rows(run(rows, shape, engine), "sh:and"))
    assert found == {(focus, None, None, CIM + "B") for focus in ("b1", "b2", "b3")}


def test_property_level_and_carries_the_operator_shape(engine):
    """Path-level sh:and: one row per failing value node, SOURCE_SHAPE the
    property shape holding the operator (#164)."""
    shape = f"""{NODE_B} sh:property cim:P .
        cim:P sh:path cim:IdentifiedObject.name ; sh:and ( [ sh:minLength 2 ] [ sh:maxLength 3 ] ) ."""
    rows = breaker("b1", ("IdentifiedObject.name", "A"), ("IdentifiedObject.name", "ABCD"), ("IdentifiedObject.name", "AB"))
    assert operator_rows(run(rows, shape, engine), "sh:and") == [
        ("b1", "IdentifiedObject.name", "A", CIM + "P"), ("b1", "IdentifiedObject.name", "ABCD", CIM + "P")]


MEMBER_NAMED = "[ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]"
MEMBER_DESCRIBED = "[ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ]"


@pytest.mark.parametrize("operator, expected", [
    (f"sh:or ( {MEMBER_NAMED} )", {("b1", "b2")}),
    (f"sh:and ( {MEMBER_NAMED} )", {("b1", "b2")}),
    (f"sh:xone ( {MEMBER_NAMED} {MEMBER_DESCRIBED} )", {("b1", "b2")}),
    (f"sh:not {MEMBER_NAMED}", {("b2", "b1")}),
], ids=["or", "and", "xone", "not"])
def test_member_with_its_own_path_judges_the_value_nodes(engine, operator, expected):
    """Under a path, a member with its own sh:path is checked on each value node
    (#166): b1's value b2 has no name, b2's value b1 has one."""
    shape = prop(f"sh:path cim:Equipment.EquipmentContainer ; {operator}")
    assert violating(run(RING, shape, engine), operator.split()[0]) == expected


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
def test_switched_off_member_with_its_own_path(engine_name):
    """A switched-off member with its own path conforms for every value node:
    sh:or and sh:and hold, sh:xone holds with one alternative, sh:not flags every value."""
    pytest.importorskip(engine_name)
    shape = prop("sh:path cim:Equipment.EquipmentContainer ; sh:or ( cim:M ) ; sh:and ( cim:M ) ; "
                 "sh:xone ( cim:M cim:D ) ; sh:not cim:M") + \
        "cim:M sh:path cim:IdentifiedObject.name ; sh:minCount 1 . " \
        "cim:D sh:path cim:IdentifiedObject.description ; sh:minCount 1 ."
    found = switched_off(RING, compiled_shapes(shape), engine_name, ["M"])
    assert violating(found, "sh:or") == violating(found, "sh:and") == violating(found, "sh:xone") == set()
    assert violating(found, "sh:not") == {("b1", "b2"), ("b2", "b1")}


ON_VALUE = "sh:path cim:Equipment.EquipmentContainer ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]"


def test_property_on_a_property_shape_reports_on_the_value_node(engine):
    """sh:property on a property shape: the inner property shape judges each
    value node and reports its own finding there (SHACL §4.7.2) — no sh:node
    row on the referring focus."""
    found = run(TRIO, prop(ON_VALUE), engine)
    assert [row[:3] for row in operator_rows(found, "sh:minCount")] == [("sub1", "IdentifiedObject.name", None)]
    assert violating(found, "sh:node") == set()


def test_property_on_a_judged_property_shape_stays_a_conformance_check(engine):
    """Below a sh:node target the same property shape is judged for conformance
    (§4.7.1): the referring focus gets the sh:node row."""
    found = run(TRIO, f"{NODE_B} sh:node cim:X . cim:X a sh:NodeShape ; sh:property [ {ON_VALUE} ] .", engine)
    assert violating(found, "sh:node") == {("b2", None)}
    assert violating(found, "sh:minCount") == set()


@pytest.mark.parametrize("engine_name", [*ENGINES, "pyshacl"])
def test_property_on_a_property_shape_switched_off(engine_name):
    pytest.importorskip(engine_name)
    shape = prop("sh:path cim:Equipment.EquipmentContainer ; sh:property cim:Inner") + \
        " cim:Inner sh:path cim:IdentifiedObject.name ; sh:minCount 1 ."
    assert len(switched_off(TRIO, compiled_shapes(shape), engine_name, ["Inner"])) == 0
