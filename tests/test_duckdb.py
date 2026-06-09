"""Tests for DuckDB engine (monkey-patched on duckdb.DuckDBPyConnection).

Uses minimal_cim.xml for functional tests, Svedala for performance benchmarks.
"""
import os
import pytest
import tempfile
from pathlib import Path

duckdb = pytest.importorskip("duckdb")
import triplets

MINIMAL = "tests/data/minimal_cim.xml"
SVEDALA_DIR = Path("test_data/relicapgrid/Instance/Grid/IGM_Svedala")
SVEDALA_FILES = [
    str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SSH_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_TP_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SV_1.xml"),
]
SKIP_SVEDALA = "Svedala test data not available"


@pytest.fixture
def data():
    """In-memory DuckDB with minimal_cim.xml loaded."""
    data = duckdb.connect()
    data.read_rdf([MINIMAL])
    return data


@pytest.fixture(scope="module")
def svedala():
    """In-memory DuckDB with Svedala IGM loaded."""
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_SVEDALA)
    data = duckdb.connect()
    data.read_rdf(SVEDALA_FILES)
    return data


# ── Load ────────────────────────────────────────────────────────────────────

class TestReadRdf:
    def test_loads_data(self, data):
        count = data.execute("SELECT COUNT(*) FROM triplets").fetchone()[0]
        assert count == 17

    def test_returns_row_count(self):
        data = duckdb.connect()
        rows = data.read_rdf([MINIMAL])
        assert rows == 17


# ── Query ───────────────────────────────────────────────────────────────────

class TestTypesDict:
    def test_returns_dict(self, data):
        td = data.types_dict()
        assert isinstance(td, dict)
        assert "Substation" in td
        assert "VoltageLevel" in td
        assert td["Substation"] == 1

    @pytest.mark.benchmark(group="duckdb-query")
    def test_benchmark(self, benchmark, svedala):
        benchmark(svedala.types_dict)


class TestTypeTableview:
    def test_returns_relation(self, data):
        rel = data.type_tableview("Substation")
        assert type(rel).__name__ == "DuckDBPyRelation"

    def test_to_pandas(self, data):
        import pandas
        df = data.type_tableview("Substation").df()
        assert isinstance(df, pandas.DataFrame)
        assert len(df) == 1
        assert "IdentifiedObject.name" in df.columns

    def test_to_polars(self, data):
        import polars
        df = data.type_tableview("Substation").pl()
        assert isinstance(df, polars.DataFrame)
        assert len(df) == 1

    def test_to_arrow(self, data):
        import pyarrow
        result = data.type_tableview("Substation").arrow()
        # DuckDB returns RecordBatchReader for .arrow()
        assert result is not None

    @pytest.mark.benchmark(group="duckdb-query")
    def test_benchmark(self, benchmark, svedala):
        benchmark(lambda: svedala.type_tableview("Terminal").df())


# ── Filter ──────────────────────────────────────────────────────────────────

class TestFilterTriplets:
    def test_by_key(self, data):
        df = data.filter_triplets(KEY="Type").df()
        assert len(df) > 0
        assert all(df["KEY"] == "Type")

    def test_by_key_and_value(self, data):
        df = data.filter_triplets(KEY="Type", VALUE="Substation").df()
        assert len(df) == 1

    def test_regex(self, data):
        df = data.filter_triplets(KEY="Model.*", regex=True).df()
        assert len(df) > 0
        assert all(df["KEY"].str.startswith("Model."))

    def test_no_filters_returns_all(self, data):
        df = data.filter_triplets().df()
        assert len(df) == 17

    @pytest.mark.benchmark(group="duckdb-filter")
    def test_benchmark(self, benchmark, svedala):
        benchmark(lambda: svedala.filter_triplets(KEY="Type", VALUE="ACLineSegment").df())


class TestFilterByType:
    def test_returns_only_matching_type(self, data):
        df = data.filter_by_type("VoltageLevel").df()
        assert len(df) > 0
        types = set(df[df["KEY"] == "Type"]["VALUE"])
        assert types == {"VoltageLevel"}

    @pytest.mark.benchmark(group="duckdb-filter")
    def test_benchmark(self, benchmark, svedala):
        benchmark(lambda: svedala.filter_by_type("ACLineSegment").df())


# ── References ──────────────────────────────────────────────────────────────

class TestReferencesTo:
    def test_returns_relation(self, svedala):
        sub_id = svedala.filter_triplets(KEY="Type", VALUE="Substation").df()["ID"].iloc[0]
        df = svedala.references_to(sub_id).df()
        assert isinstance(df, type(svedala.filter_triplets().df()))

    @pytest.mark.benchmark(group="duckdb-references")
    def test_benchmark(self, benchmark, svedala):
        sub_id = svedala.filter_triplets(KEY="Type", VALUE="Substation").df()["ID"].iloc[0]
        benchmark(lambda: svedala.references_to(sub_id).df())


class TestReferencesFrom:
    def test_returns_data(self, svedala):
        term_id = svedala.filter_triplets(KEY="Type", VALUE="Terminal").df()["ID"].iloc[0]
        df = svedala.references_from(term_id).df()
        assert len(df) > 0


# ── Persistence ─────────────────────────────────────────────────────────────

class TestPersistence:
    def test_data_survives_reconnect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.duckdb")

            # Session 1: load
            data = duckdb.connect(db_path)
            data.read_rdf([MINIMAL])
            td1 = data.types_dict()
            data.close()

            # Session 2: reopen (no read_rdf)
            data = duckdb.connect(db_path)
            td2 = data.types_dict()
            data.close()

            assert td1 == td2


# ── Export ──────────────────────────────────────────────────────────────────

class TestExportCsv:
    def test_exports_file(self, data, tmp_path):
        path = str(tmp_path / "test.csv")
        data.export_to_csv(path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0


class TestExportNquads:
    def test_exports_file(self, data, tmp_path):
        path = str(tmp_path / "test.nq")
        data.export_to_nquads(path)
        assert os.path.exists(path)
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 17
        assert lines[0].endswith(" .\n")
        assert "<urn:uuid:" in lines[0]
