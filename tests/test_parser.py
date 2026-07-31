"""Parser tests — API, engines, parity, registration, DuckDB load.

Uses minimal_cim.xml (committed) + RealGrid when present.
"""
import pytest
import pandas

import triplets
from triplets.parser import parse, find_all_xml, clean_ID, read_rdf

from pathlib import Path

_TEST_DIR = Path(__file__).parent
_PROJECT_DIR = _TEST_DIR.parent

REALGRID_ZIP = str(_PROJECT_DIR / "test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip")
MINIMAL = str(_TEST_DIR / "data" / "minimal_cim.xml")

try:
    import pyarrow  # noqa: F401
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

try:
    from triplets.parser import cython_pugixml_arrow  # noqa: F401
    HAS_CYTHON_PUGIXML_ARROW = True
except Exception:
    HAS_CYTHON_PUGIXML_ARROW = False


# ── Utilities ───────────────────────────────────────────────────────────────

def test_clean_ID():
    assert clean_ID("urn:uuid:abc-123") == "abc-123"
    assert clean_ID("#_foo_bar") == "foo_bar"
    assert clean_ID("_123") == "123"
    assert clean_ID(None) == ""
    assert clean_ID("") == ""


def test_find_all_xml_minimal():
    files = find_all_xml([MINIMAL])
    assert len(files) == 1


# ── Registration ────────────────────────────────────────────────────────────

class TestRegistration:
    def test_pandas_read_RDF_registered(self):
        assert hasattr(pandas, "read_RDF")
        assert hasattr(pandas, "read_rdf")
        assert callable(pandas.read_RDF)

    def test_polars_read_rdf_registered(self):
        polars = pytest.importorskip("polars")
        assert hasattr(polars, "read_rdf")
        assert callable(polars.read_rdf)


# ── Parse API (parametrized across engines) ─────────────────────────────────

def test_parse_minimal_basic(parser_engine):
    df = parse(MINIMAL, engine=parser_engine)
    assert list(df.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    assert len(df) > 0
    assert "Distribution" in df["VALUE"].values
    assert "NamespaceMap" in df["VALUE"].values


def test_nodeid_support(parser_engine):
    df = parse(MINIMAL, engine=parser_engine)
    assert any("ConnectivityNode" in v for v in df[df["KEY"] == "Type"]["VALUE"])


@pytest.mark.parametrize("use_cat", [True, False])
def test_categorical_encoding(parser_engine, use_cat):
    cols = ("INSTANCE_ID", "KEY") if use_cat else None
    df = parse(MINIMAL, engine=parser_engine, return_type="pandas", categorical_columns=cols)
    if use_cat:
        assert df["KEY"].nunique() < 20


# ── Return types ────────────────────────────────────────────────────────────

def test_parse_returns_arrow_and_polars():
    pa = pytest.importorskip("pyarrow")
    table = parse(MINIMAL, return_type="arrow")
    assert isinstance(table, pa.Table)
    assert table.num_rows > 0

    polars = pytest.importorskip("polars")
    pdf = parse(MINIMAL, return_type="polars")
    assert len(pdf) > 0


# ── parse_batches: lazy RecordBatchReader for out-of-core ingest ─────────────

def test_parse_batches_matches_parse():
    pa = pytest.importorskip("pyarrow")
    reader = triplets.parser.parse_batches(MINIMAL)
    table = reader.read_all()
    assert table.schema == pa.schema([(c, pa.string())
                                      for c in ("ID", "KEY", "VALUE", "INSTANCE_ID")])
    assert table.num_rows == len(parse(MINIMAL))


def test_parse_batches_zip_one_batch_per_file(tmp_path):
    pytest.importorskip("pyarrow")
    import zipfile
    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(MINIMAL, "a.xml")
        bundle.write(MINIMAL, "b.xml")
    batches = list(triplets.parser.parse_batches(archive))
    assert len(batches) == 2
    assert sum(b.num_rows for b in batches) == 2 * len(parse(MINIMAL))


def test_parse_batches_empty_and_errors():
    pa = pytest.importorskip("pyarrow")
    empty = triplets.parser.parse_batches([]).read_all()
    assert empty.num_rows == 0
    assert empty.schema.names == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    with pytest.raises(ValueError, match="arrow parser engine"):
        triplets.parser.parse_batches(MINIMAL, engine="python_lxml_pandas")


@pytest.mark.parametrize("cols,engine", [
    (("INSTANCE_ID", "KEY"), "auto"),
    # the cython engine dictionary-encodes KEY/INSTANCE_ID structurally, so the
    # no-categoricals (all plain string) contract only holds for the lxml engine
    (None, "python_lxml_arrow"),
])
def test_empty_parse_schema_matches_nonempty(cols, engine):
    """An empty parse carries the same arrow/polars schema as a non-empty one."""
    pa = pytest.importorskip("pyarrow")
    empty = parse([], return_type="arrow", categorical_columns=cols)
    full = parse(MINIMAL, return_type="arrow", categorical_columns=cols, engine=engine)
    assert empty.schema == full.schema
    assert pa.concat_tables([empty, full]).num_rows == full.num_rows

    polars = pytest.importorskip("polars")
    empty_pl = parse([], return_type="polars", categorical_columns=cols)
    full_pl = parse(MINIMAL, return_type="polars", categorical_columns=cols, engine=engine)
    assert empty_pl.schema == full_pl.schema
    assert len(polars.concat([empty_pl, full_pl])) == len(full_pl)


# ── Per-engine tests ────────────────────────────────────────────────────────

class TestPythonLxmlPandas:
    def test_basic_load(self):
        df = parse(MINIMAL, engine="python_lxml_pandas")
        assert isinstance(df, pandas.DataFrame)
        assert len(df) > 0
        types = set(df[df["KEY"] == "Type"]["VALUE"])
        assert "Substation" in types


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
class TestPythonLxmlArrow:
    def test_basic_load(self):
        df = parse(MINIMAL, engine="python_lxml_arrow")
        assert isinstance(df, pandas.DataFrame)
        assert len(df) > 0

    def test_return_arrow(self):
        import pyarrow as pa
        t = parse(MINIMAL, engine="python_lxml_arrow", return_type="arrow")
        assert isinstance(t, pa.Table)
        assert t.num_rows > 0


@pytest.mark.skipif(not HAS_CYTHON_PUGIXML_ARROW, reason="cython_pugixml_arrow not built")
class TestCythonPugixmlArrow:
    def test_basic_load(self):
        df = parse(MINIMAL, engine="cython_pugixml_arrow")
        assert isinstance(df, pandas.DataFrame)
        assert len(df) > 0


# ── Engine parity ───────────────────────────────────────────────────────────

class TestParity:
    def test_row_count_parity(self):
        engines = ["python_lxml_pandas"]
        if HAS_PYARROW:
            engines.append("python_lxml_arrow")
        if HAS_CYTHON_PUGIXML_ARROW:
            engines.append("cython_pugixml_arrow")

        results = {eng: parse(MINIMAL, engine=eng, categorical_columns=None) for eng in engines}
        counts = [len(df) for df in results.values()]
        assert all(c == counts[0] for c in counts), f"Row count mismatch: {dict(zip(results.keys(), counts))}"

    def test_content_parity(self):
        engines = ["python_lxml_pandas"]
        if HAS_PYARROW:
            engines.append("python_lxml_arrow")
        if HAS_CYTHON_PUGIXML_ARROW:
            engines.append("cython_pugixml_arrow")
        if len(engines) < 2:
            pytest.skip("Need at least 2 engines")

        results = {}
        for eng in engines:
            df = parse(MINIMAL, engine=eng, categorical_columns=None).astype(str)
            types = df[df["KEY"] == "Type"]
            meta_ids = set(types[types["VALUE"].isin(["Distribution", "NamespaceMap"])]["ID"])
            data_df = df[~df["ID"].isin(meta_ids)]
            results[eng] = data_df[["KEY", "VALUE"]].sort_values(["KEY", "VALUE"]).reset_index(drop=True).values.tolist()

        ref_name = engines[0]
        for eng in engines[1:]:
            assert results[eng] == results[ref_name], f"{eng} differs from {ref_name}"


# ── DuckDB load ─────────────────────────────────────────────────────────────

def test_duckdb_read_rdf():
    duckdb = pytest.importorskip("duckdb")
    import triplets
    data = duckdb.connect()
    rows = data.read_rdf([MINIMAL])
    assert rows == 17
    assert "Substation" in data.types_dict()


# ── RealGrid (large data, session scoped) ───────────────────────────────────

def test_realgrid_parse_and_meta(realgrid_data):
    assert len(realgrid_data) > 1_000_000
    assert "ACLineSegment" in set(realgrid_data[realgrid_data["KEY"] == "Type"]["VALUE"])
    assert "Distribution" in realgrid_data["VALUE"].values
