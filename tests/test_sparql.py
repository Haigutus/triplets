"""Tests for the rdflib reference SPARQL engine (triplets.sparql)."""
import pytest

pytest.importorskip("rdflib")

import pandas
import triplets
from pathlib import Path

SVEDALA_DIR = Path("test_data/relicapgrid/Instance/Grid/IGM_Svedala")
SVEDALA_FILES = [
    str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SSH_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_TP_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SV_1.xml"),
]
SKIP_REASON = "Svedala test data not available"

PREFIXES = ("PREFIX cim: <http://iec.ch/TC57/CIM100#> "
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> ")


@pytest.fixture(scope="module")
def svedala():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


@pytest.fixture(scope="module")
def svedala_eq():
    eq = SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"
    if not eq.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF([str(eq)])


def test_select_count_matches_tableview(svedala):
    """SPARQL count of a type == type_tableview row count (cross-engine consistency)."""
    result = svedala.sparql.query(
        PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }")
    assert isinstance(result, pandas.DataFrame)
    assert int(result["n"].iloc[0]) == len(svedala.triplets.type_tableview("ACLineSegment"))


def test_select_returns_columns_and_rows(svedala):
    result = svedala.sparql.query(
        PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name } LIMIT 5")
    assert list(result.columns) == ["s", "name"]
    assert len(result) == 5


def test_ask(svedala):
    assert svedala.sparql.query(PREFIXES + "ASK { ?s rdf:type cim:Substation }") is True
    assert svedala.sparql.query(PREFIXES + "ASK { ?s rdf:type cim:NoSuchClass }") is False


def test_construct_returns_triplets(svedala):
    result = svedala.sparql.query(
        PREFIXES + "CONSTRUCT { ?s rdf:type cim:ACLineSegment } WHERE { ?s rdf:type cim:ACLineSegment }")
    assert list(result.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    assert (result["KEY"] == "Type").all()


def test_typed_values_with_rdf_map(svedala):
    """With rdf_map, numeric literals come back as python floats (xsd datatype survives)."""
    from triplets.export_schema import schemas
    result = svedala.sparql.query(
        PREFIXES + "SELECT ?l WHERE { ?s cim:Conductor.length ?l } LIMIT 1",
        rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
    assert isinstance(result["l"].iloc[0], float)


def test_scope_restricts_to_named_graph(svedala):
    """Scoping to the SSH instance finds no ACLineSegment (they live in EQ)."""
    instances = svedala[(svedala["KEY"] == "Type") & (svedala["VALUE"] == "ACLineSegment")]["INSTANCE_ID"]
    eq_instance = str(instances.astype(str).iloc[0])
    all_instances = set(svedala["INSTANCE_ID"].astype(str).unique())
    other = next(i for i in all_instances if i != eq_instance)

    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    in_scope = int(svedala.sparql.query(q, scope=[eq_instance])["n"].iloc[0])
    out_scope = int(svedala.sparql.query(q, scope=[other])["n"].iloc[0])
    assert in_scope > 0
    assert out_scope == 0


def test_polars_input_parity(svedala):
    polars = pytest.importorskip("polars")
    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    pandas_n = int(triplets.sparql.query(svedala, q)["n"].iloc[0])
    polars_n = int(triplets.sparql.query(polars.from_pandas(svedala), q)["n"].iloc[0])
    assert pandas_n == polars_n


def test_duckdb_input(svedala):
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.register("src", svedala)
    con.execute("CREATE TABLE triplets AS SELECT * FROM src")
    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    assert int(triplets.sparql.query(con, q)["n"].iloc[0]) == len(svedala.triplets.type_tableview("ACLineSegment"))
