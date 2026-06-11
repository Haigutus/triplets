"""Tests for cgmes_tools module.

Uses Svedala IGM data (EQ+SSH+TP+SV) for CGMES-specific function tests.
"""
import os
import pytest
import pandas
import tempfile
from pathlib import Path

from triplets import cgmes_tools

SVEDALA_DIR = Path("test_data/relicapgrid/Instance/Grid/IGM_Svedala")
SVEDALA_EQ = str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml")
SVEDALA_FILES = [
    str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SSH_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_TP_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SV_1.xml"),
]

SKIP_REASON = "Svedala test data not available (needs git submodule)"


@pytest.fixture(scope="module")
def svedala_data():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


@pytest.fixture(scope="module")
def svedala_eq():
    if not Path(SVEDALA_EQ).exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF([SVEDALA_EQ])


# ── Metadata functions (no data needed) ─────────────────────────────────────

class TestGenerateInstancesID:
    def test_returns_dict(self):
        ids = cgmes_tools.generate_instances_ID()
        assert isinstance(ids, dict)
        assert "EQ" in ids
        assert "SSH" in ids
        assert "SV" in ids

    def test_all_unique(self):
        ids = cgmes_tools.generate_instances_ID()
        values = list(ids.values())
        assert len(values) == len(set(values))


class TestGetMetadataFromFilename:
    def test_parses_eq(self):
        meta = cgmes_tools.get_metadata_from_filename("20220615T2230Z__Svedala_EQ_1.xml")
        assert meta["Model.scenarioTime"] == "20220615T2230Z"
        assert meta["Model.modelingEntity"] == "Svedala"
        assert meta["Model.messageType"] == "EQ"
        assert meta["Model.version"] == "1"

    def test_parses_ssh(self):
        meta = cgmes_tools.get_metadata_from_filename("20220615T2230Z_2D_Svedala_SSH_1.xml")
        assert meta["Model.messageType"] == "SSH"
        assert meta["Model.processType"] == "2D"


class TestGetFilenameFromMetadata:
    def test_roundtrip(self):
        original = "20220615T2230Z__Svedala_EQ_001.xml"
        meta = cgmes_tools.get_metadata_from_filename(original)
        reconstructed = cgmes_tools.get_filename_from_metadata(meta)
        assert reconstructed == original


# ── Metadata functions (need data) ──────────────────────────────────────────

class TestGetMetadataFromXml:
    def test_returns_dataframe(self):
        if not Path(SVEDALA_EQ).exists():
            pytest.skip(SKIP_REASON)
        meta = cgmes_tools.get_metadata_from_xml(SVEDALA_EQ)
        assert isinstance(meta, pandas.DataFrame)
        assert len(meta) > 0


class TestGetMetadataFromFullModel:
    def test_returns_dict(self, svedala_eq):
        meta = cgmes_tools.get_metadata_from_FullModel(svedala_eq)
        assert isinstance(meta, dict)
        assert "Model.profile" in meta or "Model.created" in meta


class TestUpdateFullModelFromDict:
    def test_updates_data(self, svedala_eq):
        data = svedala_eq.copy()
        result = cgmes_tools.update_FullModel_from_dict(data, {"Model.description": "test_desc"})
        assert isinstance(result, pandas.DataFrame)


class TestGetLoadedModels:
    def test_returns_dict(self, svedala_data):
        models = cgmes_tools.get_loaded_models(svedala_data)
        assert isinstance(models, dict)


class TestGetLoadedModelParts:
    def test_returns_dataframe(self, svedala_data):
        parts = cgmes_tools.get_loaded_model_parts(svedala_data)
        assert isinstance(parts, pandas.DataFrame)
        assert len(parts) == 4  # EQ, SSH, TP, SV


# ── Input flavors (polars / arrow / duckdb converted at the boundary) ───────

class TestInputFlavors:
    def test_polars_input(self, svedala_data):
        polars = pytest.importorskip("polars")
        pl_data = polars.from_pandas(svedala_data)

        models = cgmes_tools.get_loaded_models(pl_data)
        assert isinstance(models, dict)

        # DataFrame results come back as polars
        parts = cgmes_tools.get_loaded_model_parts(pl_data)
        assert isinstance(parts, polars.DataFrame)
        assert len(parts) == 4

    def test_arrow_input(self, svedala_data):
        pyarrow = pytest.importorskip("pyarrow")
        table = pyarrow.Table.from_pandas(svedala_data, preserve_index=False)

        models = cgmes_tools.get_loaded_models(table)
        assert isinstance(models, dict)

        # DataFrame results come back as arrow
        parts = cgmes_tools.get_loaded_model_parts(table)
        assert isinstance(parts, pyarrow.Table)
        assert parts.num_rows == 4

    def test_duckdb_input(self, svedala_data):
        duckdb = pytest.importorskip("duckdb")
        con = duckdb.connect()
        con.register("triplets_arrow", svedala_data)
        con.execute("CREATE TABLE triplets AS SELECT * FROM triplets_arrow")

        models = cgmes_tools.get_loaded_models(con)
        assert isinstance(models, dict)

        # duckdb input returns pandas
        parts = cgmes_tools.get_loaded_model_parts(con)
        assert isinstance(parts, pandas.DataFrame)
        assert len(parts) == 4

    def test_results_match_pandas(self, svedala_data):
        polars = pytest.importorskip("polars")
        expected = cgmes_tools.get_loaded_models(svedala_data)
        assert cgmes_tools.get_loaded_models(polars.from_pandas(svedala_data)) == expected


# ── Data quality ────────────────────────────────────────────────────────────

class TestGetDanglingReferences:
    def test_returns_series(self, svedala_data):
        result = cgmes_tools.get_dangling_references(svedala_data)
        assert isinstance(result, pandas.Series)

    def test_detailed_returns_dataframe(self, svedala_data):
        result = cgmes_tools.get_dangling_references(svedala_data, detailed=True)
        assert isinstance(result, pandas.DataFrame)


# ── Visualization (vis-network) ────────────────────────────────────────────

class TestVisualization:
    def test_draw_relations_to_notebook(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        result = cgmes_tools.draw_relations_to(svedala_data, subs, notebook=True)
        assert isinstance(result, str)
        assert "new vis.Network" in result
        assert hasattr(result, "_repr_html_")  # displays inline in Jupyter

    def test_draw_relations_from_notebook(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        result = cgmes_tools.draw_relations_from(svedala_data, subs, notebook=True)
        assert isinstance(result, str)
        assert "new vis.Network" in result

    def test_draw_relations_notebook(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        result = cgmes_tools.draw_relations(svedala_data, subs, notebook=True, levels=1)
        assert isinstance(result, str)
        assert "new vis.Network" in result

    def test_draw_relations_to_file(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = cgmes_tools.draw_relations_to(svedala_data, subs, notebook=False, open_browser=False)
                assert os.path.exists(result)
                assert os.path.getsize(result) > 0
                with open(result, encoding="utf-8") as f:
                    content = f.read()
                # self-contained: vendored vis-network JS + node data table for the panel
                assert "vis-network" in content
                assert "objectTable" in content
            finally:
                os.chdir(orig_cwd)


# ── Statistics ──────────────────────────────────────────────────────────────

class TestStatisticsGeneratingUnitTypes:
    def test_returns_dataframe(self, svedala_data):
        result = cgmes_tools.statistics_GeneratingUnit_types(svedala_data)
        assert isinstance(result, pandas.DataFrame)
