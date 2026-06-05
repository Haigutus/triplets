"""Dedicated type_tableview (and related) tests, separate from core parser tests.

Covers pandas DF method (legacy monkey) + polars interop via the view equivalent,
using both minimal data and RealGrid when available.
"""
import pytest
import pandas as pd
import polars as pl

from triplets.parser import parse

REALGRID_ZIP = "test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"
MINIMAL = "tests/data/minimal_cim.xml"


def test_type_tableview_basic_pandas():
    df = parse(MINIMAL, return_type="pandas")
    tv = df.type_tableview("Substation")
    assert tv is not None
    assert len(tv) > 0
    # index should be ID-like
    assert "ID" in str(tv.index.name) or tv.index.name is None


def test_type_tableview_pandas_realgrid(realgrid_data):
    data = realgrid_data
    tv = data.type_tableview("ACLineSegment", string_to_number=False)
    assert tv is not None
    assert len(tv) > 0
    # columns are the properties
    assert "ID" not in tv.columns  # pivoted away
    assert len(tv.columns) > 3


def test_type_tableview_from_polars_equiv():
    """Test that we can get a useful 'type view' starting from a polars load."""
    pdf = parse(MINIMAL, return_type="polars")
    # Here we just ensure roundtrip + pandas view works from polars data
    df_back = pdf.to_pandas()
    tv = df_back.type_tableview("Substation")
    assert tv is not None and len(tv) > 0


def test_type_tableview_polars_realgrid():
    """Polars-based view on large data (via to_pandas view or native equiv)."""
    polars = pytest.importorskip("polars")
    pdf = parse(REALGRID_ZIP, engine="cython_pugixml_arrow", return_type="polars")
    df = pdf.to_pandas()
    tv = df.type_tableview("Terminal", string_to_number=False)
    assert tv is not None
    assert len(tv) > 1000
