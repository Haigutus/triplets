"""Tests for the optional context enrichment pass (triplets.validation.context)."""
import pandas
import pytest

pytest.importorskip("rdflib")

import triplets
from triplets.validation.context import ENRICHMENT_COLUMNS, enrich

LINE = "11111111-2222-3333-4444-555555555555"
DIST = "dddddddd-2222-3333-4444-555555555555"

DATA = pandas.DataFrame(
    [
        (LINE, "Type", "ACLineSegment", "g1"),
        (LINE, "IdentifiedObject.name", "Line 1", "g1"),
        (DIST, "Type", "Distribution", "g1"),
        (DIST, "label", "20220615T2230Z_Svedala_EQ.xml", "g1"),
    ],
    columns=["ID", "KEY", "VALUE", "INSTANCE_ID"],
)

SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:name "ACLineSegment completeness" ;
    sh:description "Every line needs a length." ;
    sh:property [ sh:path cim:Conductor.length ; sh:minCount 1 ] .
"""

RDF_MAP = {"EQ": {
    "ACLineSegment": {"type": "Class",
                      "description": "A wire or combination of wires used to carry current."},
    "Conductor.length": {"type": "Attribute", "description": "Segment length for calculating line section capabilities.",
                         "multiplicity": "1..1", "xsd:type": "xsd:float"},
}}


@pytest.fixture(scope="module")
def shapes():
    """One parsed graph for the whole module — anonymous property shapes get
    fresh BNode ids per parse, so enrichment must see the same shapes object
    the validation ran with."""
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=SHAPE, format="turtle")
    return triplets.validation.compile(graph)


@pytest.fixture()
def violations(shapes):
    return triplets.validation.validate(DATA, shapes, engine="pandas")


def test_enrich_all_sources(violations, shapes):
    result = enrich(violations, data=DATA, shapes=shapes, rdf_map=RDF_MAP)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["VIOLATION_TYPE"] == "sh:minCount"
    assert row["INSTANCE_ID"] == "g1"
    assert row["INSTANCE_LABEL"] == "20220615T2230Z_Svedala_EQ.xml"
    assert row["OBJECT_TYPE"] == "ACLineSegment"
    assert row["OBJECT_NAME"] == "Line 1"
    assert row["SHAPE_NAME"] == "ACLineSegment completeness"
    assert row["SHAPE_DESCRIPTION"] == "Every line needs a length."
    assert row["SCHEMA_DESCRIPTION"].startswith("Segment length")
    assert row["SCHEMA_MULTIPLICITY"] == "1..1"
    assert row["CLASS_DESCRIPTION"].startswith("A wire")


def test_enrich_sources_optional(violations):
    result = enrich(violations)
    assert list(result.columns[-len(ENRICHMENT_COLUMNS):]) == ENRICHMENT_COLUMNS
    assert result[ENRICHMENT_COLUMNS].isna().all().all()

    only_data = enrich(violations, data=DATA)
    assert only_data["OBJECT_NAME"].iloc[0] == "Line 1"
    assert only_data["SHAPE_NAME"].isna().all()

    only_schema = enrich(violations, rdf_map=RDF_MAP)
    assert only_schema["SCHEMA_MULTIPLICITY"].iloc[0] == "1..1"
    assert only_schema["CLASS_DESCRIPTION"].isna().all()   # needs OBJECT_TYPE from data


def test_validate_context_flag(shapes):
    result = triplets.validation.validate(DATA, shapes, engine="pandas",
                                          rdf_map=RDF_MAP, context=True)
    assert set(ENRICHMENT_COLUMNS) <= set(result.columns)
    assert result["OBJECT_NAME"].iloc[0] == "Line 1"
    assert result["SCHEMA_DESCRIPTION"].iloc[0].startswith("Segment length")
    assert result["SHAPE_NAME"].iloc[0] == "ACLineSegment completeness"


def test_pyshacl_engine_enrichment_degrades_gracefully(shapes):
    """pyshacl SOURCE_SHAPE joins on the same shape IRIs; anonymous property
    shapes fall back to null shape columns — never an error."""
    pytest.importorskip("pyshacl")
    violations = triplets.validation.validate(DATA, shapes, engine="pyshacl",
                                              context=True)
    assert set(ENRICHMENT_COLUMNS) <= set(violations.columns)
    assert (violations["OBJECT_TYPE"] == "ACLineSegment").any()


def test_flatten_schema_first_wins_and_shapes():
    from triplets.export.nquads_utils import flatten_schema
    duplicated = {"EQ": RDF_MAP["EQ"],
                  "TP": {"Conductor.length": {"type": "Attribute", "description": "other",
                                              "multiplicity": "0..1"}}}
    key_info, class_info = flatten_schema(duplicated)
    assert key_info["Conductor.length"]["multiplicity"] == "1..1"   # first profile wins
    assert class_info["ACLineSegment"].startswith("A wire")
