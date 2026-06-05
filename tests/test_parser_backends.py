"""Focused tests for the refactored CIMXML parser engines.

All engines must produce compatible content (parity).
"""

import pytest
import pandas as pd

import triplets  # registers read_RDF etc
from triplets.parser import parse, read_rdf

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


@pytest.fixture(scope="module")
def minimal_path():
    from pathlib import Path
    p = Path(__file__).parent / "data" / "minimal_cim.xml"
    if not p.exists():
        pytest.skip(f"minimal test data not found: {p}")
    return str(p)


class TestRegistration:
    def test_read_RDF_and_read_rdf_registered(self):
        assert hasattr(pd, "read_RDF")
        assert hasattr(pd, "read_rdf")
        assert callable(pd.read_RDF)
        assert callable(pd.read_rdf)


class TestPythonLxmlPandas:
    def test_basic_load(self, minimal_path):
        df = parse(minimal_path, engine="python_lxml_pandas")
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
        assert len(df) > 0
        types = set(df[df["KEY"] == "Type"]["VALUE"])
        assert "Distribution" in types
        assert "NamespaceMap" in types
        assert "Substation" in types

    def test_nodeid_present(self, minimal_path):
        df = parse(minimal_path, engine="python_lxml_pandas")
        ids = set(df["ID"].dropna().astype(str))
        assert any("nodeA" in i or i == "nodeA" for i in ids) or "nodeA" in set(df["VALUE"].astype(str))


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
class TestPythonLxmlArrow:
    def test_basic_load(self, minimal_path):
        df = parse(minimal_path, engine="python_lxml_arrow")
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
        assert len(df) > 0
        types = set(df[df["KEY"] == "Type"]["VALUE"])
        assert "Distribution" in types
        assert "NamespaceMap" in types
        assert "Substation" in types

    def test_return_arrow(self, minimal_path):
        import pyarrow as pa
        t = parse(minimal_path, engine="python_lxml_arrow", return_type="arrow")
        assert isinstance(t, pa.Table)
        assert t.num_rows > 0
        assert "ID" in t.schema.names

    def test_return_polars_if_available(self, minimal_path):
        polars = pytest.importorskip("polars")
        pdf = parse(minimal_path, engine="python_lxml_arrow", return_type="polars")
        assert len(pdf) > 0

    def test_nodeid_and_refs_present(self, minimal_path):
        df = parse(minimal_path, engine="python_lxml_arrow")
        ids = set(df["ID"].dropna().astype(str))
        assert any("nodeA" in i or i == "nodeA" for i in ids) or "nodeA" in set(df["VALUE"].astype(str))


@pytest.mark.skipif(not HAS_CYTHON_PUGIXML_ARROW, reason="cython_pugixml_arrow not built")
class TestCythonPugixmlArrow:
    def test_basic_load(self, minimal_path):
        df = parse(minimal_path, engine="cython_pugixml_arrow")
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_parity_with_python_lxml_arrow(self, minimal_path):
        df_py = parse(minimal_path, engine="python_lxml_arrow")
        df_cy = parse(minimal_path, engine="cython_pugixml_arrow")
        assert len(df_py) == len(df_cy)
        for d in (df_py, df_cy):
            types = set(d[d["KEY"] == "Type"]["VALUE"])
            assert "Distribution" in types and "NamespaceMap" in types


class TestParity:
    """Verify all available engines produce identical content."""

    def test_row_count_parity(self, minimal_path):
        engines = ["python_lxml_pandas"]
        if HAS_PYARROW:
            engines.append("python_lxml_arrow")
        if HAS_CYTHON_PUGIXML_ARROW:
            engines.append("cython_pugixml_arrow")

        results = {}
        for eng in engines:
            results[eng] = parse(minimal_path, engine=eng, categorical_columns=None)

        row_counts = {eng: len(df) for eng, df in results.items()}
        counts = list(row_counts.values())
        assert all(c == counts[0] for c in counts), f"Row count mismatch: {row_counts}"

    def test_content_parity(self, minimal_path):
        engines = ["python_lxml_pandas"]
        if HAS_PYARROW:
            engines.append("python_lxml_arrow")
        if HAS_CYTHON_PUGIXML_ARROW:
            engines.append("cython_pugixml_arrow")

        if len(engines) < 2:
            pytest.skip("Need at least 2 engines for parity test")

        results = {}
        for eng in engines:
            df = parse(minimal_path, engine=eng, categorical_columns=None)
            df = df.astype(str)
            # Exclude meta rows (Distribution/NamespaceMap) — their IDs are random UUIDs
            # and xml_base values differ between lxml (abs path) and pugixml (filename).
            # Focus on actual RDF object data for parity.
            types = df[df["KEY"] == "Type"]
            meta_ids = set(types[types["VALUE"].isin(["Distribution", "NamespaceMap"])]["ID"])
            data_df = df[~df["ID"].isin(meta_ids)]
            kv = data_df[["KEY", "VALUE"]].sort_values(["KEY", "VALUE"]).reset_index(drop=True)
            results[eng] = kv.values.tolist()

        ref_name = engines[0]
        ref = results[ref_name]
        for eng in engines[1:]:
            assert results[eng] == ref, f"{eng} differs from {ref_name}"


def test_read_RDF_monkey_batching_and_kwargs(minimal_path):
    df = pd.read_RDF(minimal_path)
    assert len(df) > 0
    df2 = pd.read_RDF([minimal_path], engine="python_lxml_pandas", return_type="pandas")
    assert len(df2) == len(df)


def test_find_all_and_clean_via_public():
    from triplets.parser import find_all_xml, clean_ID
    assert callable(find_all_xml)
    assert clean_ID("urn:uuid:abc") == "abc"
    assert clean_ID("#_foo") == "foo"
