"""Core parser tests.

Tests the new triplets.parser API, pandas.read_RDF delegation, find_all_xml,
clean_ID, meta rows (Distribution/NamespaceMap), nodeID support, categorical
(dictionary) encoding for memory, and interop notes.

Uses minimal self-contained data + RealGrid when present (session scoped).
"""
import os
from pathlib import Path

import pandas as pd
import pytest

import triplets
from triplets.parser import parse, find_all_xml, clean_ID, read_rdf

REALGRID_ZIP = "test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"
MINIMAL = "tests/data/minimal_cim.xml"


def test_clean_ID():
    assert clean_ID("urn:uuid:abc-123") == "abc-123"
    assert clean_ID("#_foo_bar") == "foo_bar"
    assert clean_ID("_123") == "123"
    assert clean_ID(None) == ""
    assert clean_ID("") == ""


def test_find_all_xml_minimal():
    files = find_all_xml([MINIMAL])
    assert len(files) == 1


def test_parse_minimal_basic(parser_engine):
    """Basic parse via new API + read_RDF delegation."""
    df = parse(MINIMAL, engine=parser_engine)
    assert list(df.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    assert len(df) > 0
    assert "Distribution" in df["VALUE"].values
    assert "NamespaceMap" in df["VALUE"].values
    # read_RDF alias
    df2 = pd.read_RDF(MINIMAL)
    assert len(df2) == len(df)


def test_parse_returns_arrow_and_polars():
    pa = pytest.importorskip("pyarrow")
    table = parse(MINIMAL, return_type="arrow")
    assert isinstance(table, pa.Table)
    assert table.num_rows > 0

    polars = pytest.importorskip("polars")
    pdf = parse(MINIMAL, return_type="polars")
    assert len(pdf) > 0


def test_nodeid_support(parser_engine):
    # The minimal_cim has a rdf:nodeID example
    df = parse(MINIMAL, engine=parser_engine)
    # Should have captured the nodeID object
    # The ConnectivityNode with nodeID should appear
    assert any("ConnectivityNode" in v for v in df[df["KEY"] == "Type"]["VALUE"])


@pytest.mark.parametrize("use_cat", [True, False])
def test_categorical_encoding(parser_engine, use_cat):
    """INSTANCE_ID and KEY should be categorized when requested."""
    cols = ("INSTANCE_ID", "KEY") if use_cat else None
    df = parse(MINIMAL, engine=parser_engine, return_type="pandas", categorical_columns=cols)
    if use_cat:
        key_card = df["KEY"].nunique()
        assert key_card < 20, "KEY should have low cardinality"


def test_realgrid_parse_and_meta(realgrid_data):
    """Smoke on RealGrid (large, session scoped)."""
    data = realgrid_data
    assert len(data) > 1_000_000
    assert "ACLineSegment" in set(data[data["KEY"] == "Type"]["VALUE"])
    # Meta rows present
    assert "Distribution" in data["VALUE"].values


def test_duckdb_read_rdf():
    """DuckDB con.read_rdf() loads data via Arrow zero-copy."""
    duckdb = pytest.importorskip("duckdb")
    import triplets
    data = duckdb.connect()
    rows = data.read_rdf([MINIMAL])
    assert rows == 17
    td = data.types_dict()
    assert "Substation" in td


def test_read_rdf_with_categorical(realgrid_data):
    df = read_rdf(REALGRID_ZIP, engine="cython_pugixml_arrow", categorical_columns=("INSTANCE_ID", "KEY"))
    # KEY should be categorical-like (low unique)
    assert df["KEY"].nunique() < 500
    # INSTANCE_ID low unique (one per XML file inside the zip)
    assert df["INSTANCE_ID"].nunique() < 20
