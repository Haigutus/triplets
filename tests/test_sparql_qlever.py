"""Parity tests for the embedded qlever SPARQL engine (vs the rdflib reference).

Skip entirely when the compiled extension (setup_qlever.py) is not built —
the registry then auto-falls back to rdflib and nothing else changes.
"""
import pytest

pytest.importorskip("rdflib")
pytest.importorskip("triplets.sparql._qlever", reason="qlever extension not built (setup_qlever.py)")

import pandas
import triplets

from _parity import SVEDALA_DIR, SVEDALA_FILES, SKIP_REASON

PREFIXES = ("PREFIX cim: <http://iec.ch/TC57/CIM100#> "
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> ")


@pytest.fixture(scope="module")
def svedala():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


def test_auto_prefers_qlever():
    assert triplets.sparql.get_engine("auto")[0] == "qlever"
    assert triplets.sparql.get_engine("performance")[0] == "qlever"


def test_select_parity(svedala):
    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    reference = int(triplets.sparql.query(svedala, q, engine="rdflib")["n"].iloc[0])
    fast = int(triplets.sparql.query(svedala, q, engine="qlever")["n"].iloc[0])
    assert fast == reference > 0


def test_select_columns_and_rows(svedala):
    result = triplets.sparql.query(
        svedala, PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name } LIMIT 5",
        engine="qlever")
    assert list(result.columns) == ["s", "name"]
    assert len(result) == 5


def test_ask(svedala):
    assert triplets.sparql.query(svedala, PREFIXES + "ASK { ?s rdf:type cim:Substation }",
                                 engine="qlever") is True
    assert triplets.sparql.query(svedala, PREFIXES + "ASK { ?s rdf:type cim:NoSuchClass }",
                                 engine="qlever") is False


def test_typed_values_with_rdf_map(svedala):
    from triplets.export_schema import schemas
    result = triplets.sparql.query(
        svedala, PREFIXES + "SELECT ?l WHERE { ?s cim:Conductor.length ?l } LIMIT 1",
        rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, engine="qlever")
    assert isinstance(result["l"].iloc[0], float)


def test_construct_returns_triplets(svedala):
    result = triplets.sparql.query(
        svedala,
        PREFIXES + "CONSTRUCT { ?s rdf:type cim:ACLineSegment } WHERE { ?s rdf:type cim:ACLineSegment }",
        engine="qlever")
    assert list(result.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    assert (result["KEY"] == "Type").all()


def test_scope_parity(svedala):
    instances = svedala[(svedala["KEY"] == "Type") & (svedala["VALUE"] == "ACLineSegment")]["INSTANCE_ID"]
    eq_instance = str(instances.astype(str).iloc[0])
    other = next(i for i in set(svedala["INSTANCE_ID"].astype(str).unique()) if i != eq_instance)

    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    in_scope = int(triplets.sparql.query(svedala, q, scope=[eq_instance], engine="qlever")["n"].iloc[0])
    out_scope = int(triplets.sparql.query(svedala, q, scope=[other], engine="qlever")["n"].iloc[0])
    reference = int(triplets.sparql.query(svedala, q, scope=[eq_instance], engine="rdflib")["n"].iloc[0])
    assert in_scope == reference > 0
    assert out_scope == 0


def test_index_cache_reused(svedala):
    from triplets.sparql import sparql_qlever
    q = PREFIXES + "ASK { ?s rdf:type cim:Substation }"
    triplets.sparql.query(svedala, q, engine="qlever")
    cached = len(sparql_qlever._INDEXES)
    triplets.sparql.query(svedala, q, engine="qlever")   # same data → same index object
    assert len(sparql_qlever._INDEXES) == cached


def test_shacl_sparql_delegation_via_qlever(svedala):
    """sh:sparql constraints in the vectorized SHACL engines ride on qlever automatically."""
    pytest.importorskip("pyshacl")
    shape = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [
        sh:path cim:IdentifiedObject.name ;
        sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "no-such-name") }' ] ;
    ] .
"""
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=shape, format="turtle")
    violations = triplets.validation.validate(svedala, graph, engine="pandas")
    assert len(violations.loc[violations["VIOLATION_TYPE"] == "sh:sparql"]) == 0  # clean data, but executed
