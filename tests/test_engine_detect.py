"""Shared flavor conversion (_engine_detect)."""
import pandas
import pytest

import triplets  # noqa: F401
from triplets._engine_detect import (
    as_frame,
    flavor,
    match_flavor,
    to_arrow,
    to_pandas,
    to_polars,
)

COLUMNS = ["ID", "KEY", "VALUE", "INSTANCE_ID"]


def _frame():
    return pandas.DataFrame({
        "ID": ["a", "a"],
        "KEY": ["Type", "IdentifiedObject.name"],
        "VALUE": ["Breaker", "b1"],
        "INSTANCE_ID": ["i1", "i1"],
    })


def test_pandas_identity():
    frame = _frame()
    assert to_pandas(frame) is frame
    assert flavor(frame) == "pandas"


def test_polars_roundtrip():
    polars = pytest.importorskip("polars")
    frame = _frame()
    pl_frame = polars.from_pandas(frame)
    assert to_polars(pl_frame) is pl_frame
    back = to_pandas(pl_frame)
    pandas.testing.assert_frame_equal(back, frame, check_dtype=False)


def test_arrow_paths():
    pyarrow = pytest.importorskip("pyarrow")
    frame = _frame()
    table = pyarrow.Table.from_pandas(frame, preserve_index=False)
    assert flavor(table) == "pyarrow"
    assert to_arrow(table) is table
    projected = to_arrow(table, columns=COLUMNS)
    assert projected.column_names == COLUMNS
    plain = to_pandas(table, plain=True)
    assert not any(str(dtype).startswith("arrow") or "pyarrow" in str(type(dtype)).lower()
                   for dtype in plain.dtypes)
    pl_frame = to_polars(table)
    assert flavor(pl_frame) == "polars"
    assert len(pl_frame) == 2


def test_duckdb_paths():
    duckdb = pytest.importorskip("duckdb")
    frame = _frame()
    con = duckdb.connect()
    con.register("_src", frame)
    con.execute("CREATE TABLE triplets AS SELECT * FROM _src")

    assert flavor(con) == "duckdb"
    assert len(to_pandas(con)) == 2
    arrow = to_arrow(con, columns=COLUMNS)
    assert arrow.num_rows == 2
    assert list(arrow.column_names) == COLUMNS
    assert len(to_polars(con)) == 2
    # as_frame materializes duckdb, leaves pandas alone
    assert as_frame(frame) is frame
    assert len(as_frame(con)) == 2


def test_match_flavor():
    polars = pytest.importorskip("polars")
    pyarrow = pytest.importorskip("pyarrow")
    result = _frame()
    template_pl = polars.from_pandas(result)
    matched = match_flavor(result, template_pl)
    assert flavor(matched) == "polars"
    matched_arrow = match_flavor(result, pyarrow.Table.from_pandas(result, preserve_index=False))
    assert flavor(matched_arrow) == "pyarrow"
    assert match_flavor(result, result) is result
