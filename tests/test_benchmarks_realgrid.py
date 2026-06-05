"""Performance benchmarks for parsing RealGrid and type_tableview (pandas + polars).

Run with:
  python -m pytest tests/test_benchmarks_realgrid.py --benchmark-only \
    --benchmark-json=documents/parsers_performance.json -q -k "parse"

  python -m pytest tests/test_benchmarks_realgrid.py --benchmark-only \
    --benchmark-json=documents/dataframe_performance.json -q -k "tableview"
"""
import pytest
import pandas as pd
import polars as pl

from triplets.parser import parse
from tests.conftest import HAS_PYARROW, HAS_CYTHON_PUGIXML_ARROW

REALGRID_ZIP = "test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"

# Common type for type_tableview
TYPE_FOR_VIEW = "ACLineSegment"

# Build engine list dynamically based on what's available
_PARSE_ENGINES = ["python_lxml_pandas"]
if HAS_PYARROW:
    _PARSE_ENGINES.append("python_lxml_arrow")
if HAS_CYTHON_PUGIXML_ARROW:
    _PARSE_ENGINES.append("cython_pugixml_arrow")


def _polars_type_tableview(pdf: pl.DataFrame, type_name: str, string_to_number: bool = True, type_key: str = "Type"):
    """Polars equivalent of rdf_parser.type_tableview (for benchmark 'from polars')."""
    type_id = pdf.filter((pl.col("VALUE") == type_name) & (pl.col("KEY") == type_key)).select("ID")
    if type_id.is_empty():
        return None
    type_data = type_id.join(pdf, on="ID", how="inner").unique(subset=["ID", "KEY"])
    data_view = type_data.pivot(on="KEY", index="ID", values="VALUE")
    if string_to_number:
        for col in data_view.columns:
            if col == "ID":
                continue
            try:
                data_view = data_view.with_columns(pl.col(col).cast(pl.Float64, strict=False))
            except Exception:
                pass
    return data_view


@pytest.mark.benchmark(group="parse-realgrid")
@pytest.mark.parametrize("engine", _PARSE_ENGINES)
def test_parse_realgrid_to_pandas(benchmark, engine):
    """Parse full RealGrid zip to pandas DF using the engine."""
    benchmark.extra_info.update({"engine": engine, "return": "pandas"})
    def _run():
        return parse(REALGRID_ZIP, engine=engine, return_type="pandas")
    df = benchmark(_run)
    assert len(df) > 1_000_000
    assert "ACLineSegment" in df["VALUE"].values


@pytest.mark.benchmark(group="parse-realgrid")
@pytest.mark.parametrize("engine", _PARSE_ENGINES)
def test_parse_realgrid_to_polars(benchmark, engine):
    """Parse full RealGrid zip to polars DF (via arrow) using the engine."""
    benchmark.extra_info.update({"engine": engine, "return": "polars"})
    def _run():
        return parse(REALGRID_ZIP, engine=engine, return_type="polars")
    pdf = benchmark(_run)
    assert len(pdf) > 1_000_000


@pytest.mark.benchmark(group="dataframe-type-tableview")
def test_type_tableview_pandas(benchmark):
    """type_tableview call on pandas DF."""
    best_engine = _PARSE_ENGINES[-1]  # fastest available
    df = parse(REALGRID_ZIP, engine=best_engine, return_type="pandas")
    benchmark.extra_info.update({"on": "pandas", "type": TYPE_FOR_VIEW})
    def _run():
        return df.type_tableview(TYPE_FOR_VIEW, string_to_number=False)
    view = benchmark(_run)
    assert view is not None and len(view) > 0


@pytest.mark.benchmark(group="dataframe-type-tableview")
def test_type_tableview_polars(benchmark):
    """type_tableview-equivalent call starting from polars DF."""
    best_engine = _PARSE_ENGINES[-1]
    pdf = parse(REALGRID_ZIP, engine=best_engine, return_type="polars")
    benchmark.extra_info.update({"on": "polars", "type": TYPE_FOR_VIEW})
    def _run():
        return _polars_type_tableview(pdf, TYPE_FOR_VIEW, string_to_number=False)
    view = benchmark(_run)
    assert view is not None and len(view) > 0
