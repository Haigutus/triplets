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

# New dcat:Dataset-header NC instances + hybrid FullModel example (same submodule)
NC_DIR = Path("test_data/relicapgrid/Instance/NetworkCode/Svedala/Svedala_instance")
SVEDALA_ER = str(NC_DIR / "Svedala_ER.xml")        # Dataset; requires the Svedala EQ FullModel
SVEDALA_RA = str(NC_DIR / "Svedala_RA.xml")        # Dataset; target of other instances' requires
SVEDALA_RAS = str(NC_DIR / "Svedala_RAS.xml")      # Dataset; requires Svedala_RA
HYBRID_FULLMODEL = str(Path("test_data/relicapgrid/Instance/BoundaryConfigurationExamples")
                       / "TC-Boundary-Header-FullModelExtended" / "20241223T0642Z_ENTSO-E_EQ_BD_1.xml")


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
        ids = cgmes_tools.generate_instance_ids()
        assert isinstance(ids, dict)
        assert "EQ" in ids
        assert "SSH" in ids
        assert "SV" in ids

    def test_all_unique(self):
        ids = cgmes_tools.generate_instance_ids()
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
    SV_ID = "2ffd3555-f572-494e-b35e-e56ae740eeb2"  # Svedala SV FullModel

    def test_default_graph_roots(self, svedala_data):
        models = cgmes_tools.get_loaded_models(svedala_data)
        assert list(models) == [self.SV_ID]  # only header nothing depends on
        assert models[self.SV_ID]["INSTANCE_ID"].nunique() == 4  # SV, TP, SSH, EQ

    def test_root_filter(self, svedala_data):
        # "SV" matches the 3.0 StateVariables URI via the legacy section map
        assert list(cgmes_tools.get_loaded_models(svedala_data, root="SV")) == [self.SV_ID]
        assert list(cgmes_tools.get_loaded_models(svedala_data, root="StateVariables")) == [self.SV_ID]
        assert cgmes_tools.get_loaded_models(svedala_data, root="ZZ") == {}

    def test_mixed_frames_are_two_models(self, mixed_data):
        assert len(cgmes_tools.get_loaded_models(mixed_data)) == 2  # SV root + ER root
        er = cgmes_tools.get_loaded_models(mixed_data, root="ER")
        assert len(er) == 1
        parts = next(iter(er.values()))
        assert parts["INSTANCE_ID"].nunique() == 2  # ER Dataset + the EQ it requires

    def test_cycle_does_not_hang(self):
        rows = pandas.DataFrame([
            {"ID": "a", "KEY": "keyword", "VALUE": "A", "INSTANCE_ID": "i1"},
            {"ID": "a", "KEY": "requires", "VALUE": "b", "INSTANCE_ID": "i1"},
            {"ID": "b", "KEY": "keyword", "VALUE": "B", "INSTANCE_ID": "i2"},
            {"ID": "b", "KEY": "requires", "VALUE": "c", "INSTANCE_ID": "i2"},
            {"ID": "c", "KEY": "keyword", "VALUE": "C", "INSTANCE_ID": "i3"},
            {"ID": "c", "KEY": "requires", "VALUE": "b", "INSTANCE_ID": "i3"},  # cycle b <-> c
        ])
        models = cgmes_tools.get_loaded_models(rows)
        assert list(models) == ["a"]
        assert set(models["a"]["ID"]) == {"a", "b", "c"}


class TestGetLoadedModelParts:
    def test_returns_dataframe(self, svedala_data):
        parts = cgmes_tools.get_loaded_model_parts(svedala_data)
        assert isinstance(parts, pandas.DataFrame)
        assert len(parts) == 4  # EQ, SSH, TP, SV

    def test_metadata_stays_text(self, svedala_data):
        parts = cgmes_tools.get_loaded_model_parts(svedala_data)
        assert parts["Model.version"].map(type).eq(str).all()

    def test_multivalue_dependenton(self, svedala_data):
        eq = "bea45848-a05d-496b-9ab2-f42c6714183e"
        # default keeps the first value (list cells cannot convert back to arrow)
        first_only = cgmes_tools.get_loaded_model_parts(svedala_data)
        assert isinstance(first_only.loc[eq, "Model.DependentOn"], str)
        listed = cgmes_tools.get_loaded_model_parts(svedala_data, multivalue=True)
        assert isinstance(listed.loc[eq, "Model.DependentOn"], list)
        assert len(listed.loc[eq, "Model.DependentOn"]) == 3

    def test_both_header_kinds(self, svedala_data):
        if not Path(SVEDALA_ER).exists():
            pytest.skip(SKIP_REASON)
        data = pandas.concat([svedala_data, pandas.read_RDF([SVEDALA_ER])], ignore_index=True)
        parts = cgmes_tools.get_loaded_model_parts(data)
        assert len(parts) == 5
        assert set(parts["Type"]) == {"FullModel", "Dataset"}
        # union of columns across header kinds
        assert "Model.profile" in parts.columns and "conformsTo" in parts.columns


PROFILE_COLUMNS = ["INSTANCE_ID", "label", "HEADER", "HEADER_ID", "KEY", "VALUE"]


@pytest.fixture(scope="module")
def mixed_data(svedala_data):
    """Old-header IGM + new-header NC instance in one frame."""
    if not Path(SVEDALA_ER).exists():
        pytest.skip(SKIP_REASON)
    return pandas.concat([svedala_data, pandas.read_RDF([SVEDALA_ER])], ignore_index=True)


class TestGetLoadedProfiles:
    def test_fullmodel_header(self, svedala_data):
        profiles = cgmes_tools.get_loaded_profiles(svedala_data)
        assert list(profiles.columns) == PROFILE_COLUMNS
        assert set(profiles["HEADER"]) == {"FullModel"}
        assert profiles["INSTANCE_ID"].nunique() == 4
        assert set(profiles["KEY"]) == {"Model.profile"}
        assert profiles["label"].str.endswith(".xml").all()

    def test_dataset_header(self, mixed_data):
        profiles = cgmes_tools.get_loaded_profiles(mixed_data)
        er = profiles[profiles["HEADER"] == "Dataset"]
        assert set(er["KEY"]) == {"keyword", "conformsTo"}
        assert er.loc[er["KEY"] == "keyword", "VALUE"].tolist() == ["ER"]

    def test_hybrid_fullmodel_extended(self):
        if not Path(HYBRID_FULLMODEL).exists():
            pytest.skip(SKIP_REASON)
        profiles = cgmes_tools.get_loaded_profiles(pandas.read_RDF([HYBRID_FULLMODEL]))
        assert set(profiles["HEADER"]) == {"FullModel"}
        # extended header declares identity through old AND new keys
        assert {"Model.profile", "keyword", "conformsTo"} <= set(profiles["KEY"])

    def test_non_header_conformsto_reported_verbatim(self):
        # profile-registry objects (e.g. prof:Profile carrying dcterms:conformsTo)
        # are picked up too — pinned: distinguishable by the verbatim HEADER column
        rows = pandas.DataFrame([
            {"ID": "fm", "KEY": "Type", "VALUE": "FullModel", "INSTANCE_ID": "i1"},
            {"ID": "fm", "KEY": "Model.profile", "VALUE": "http://entsoe.eu/CIM/EquipmentCore/3/1", "INSTANCE_ID": "i1"},
            {"ID": "reg", "KEY": "Type", "VALUE": "Profile", "INSTANCE_ID": "i2"},
            {"ID": "reg", "KEY": "conformsTo", "VALUE": "https://ap.cim4.eu/Example/1.0", "INSTANCE_ID": "i2"},
        ])
        profiles = cgmes_tools.get_loaded_profiles(rows)
        assert set(profiles["HEADER"]) == {"FullModel", "Profile"}

    def test_null_and_empty_values_dropped(self):
        rows = pandas.DataFrame([
            {"ID": "fm", "KEY": "Type", "VALUE": "FullModel", "INSTANCE_ID": "i1"},
            {"ID": "fm", "KEY": "Model.profile", "VALUE": None, "INSTANCE_ID": "i1"},
            {"ID": "fm", "KEY": "conformsTo", "VALUE": "", "INSTANCE_ID": "i1"},
            {"ID": "fm", "KEY": "keyword", "VALUE": "EQ", "INSTANCE_ID": "i1"},
        ])
        profiles = cgmes_tools.get_loaded_profiles(rows)
        assert profiles["VALUE"].tolist() == ["EQ"]
        polars = pytest.importorskip("polars")
        assert cgmes_tools.get_loaded_profiles(polars.from_pandas(rows))["VALUE"].to_list() == ["EQ"]

    def test_empty_data(self):
        empty = pandas.DataFrame(columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
        profiles = cgmes_tools.get_loaded_profiles(empty)
        assert list(profiles.columns) == PROFILE_COLUMNS and profiles.empty


class TestGetModelRelations:
    RELATION_COLUMNS = ["ID_FROM", "KEY", "ID_TO", "INSTANCE_ID_FROM", "INSTANCE_ID_TO"]

    def test_dependenton_edges(self, svedala_data):
        relations = cgmes_tools.get_model_relations(svedala_data)
        assert list(relations.columns) == self.RELATION_COLUMNS
        assert set(relations["KEY"]) == {"Model.DependentOn"}
        # SSH/TP/SV depend on loaded parts; EQ depends on unloaded boundary parts
        assert relations["INSTANCE_ID_TO"].notna().any()
        assert relations["INSTANCE_ID_TO"].isna().any()

    def test_cross_header_requires(self, mixed_data):
        relations = cgmes_tools.get_model_relations(mixed_data)
        requires = relations[relations["KEY"] == "requires"]
        # new-header ER requires the old-header EQ FullModel — resolved across kinds
        assert requires["ID_TO"].tolist() == ["bea45848-a05d-496b-9ab2-f42c6714183e"]
        assert requires["INSTANCE_ID_TO"].notna().all()

    def test_missing_dependency(self):
        if not Path(SVEDALA_ER).exists():
            pytest.skip(SKIP_REASON)
        relations = cgmes_tools.get_model_relations(pandas.read_RDF([SVEDALA_ER]))
        requires = relations[relations["KEY"] == "requires"]
        assert requires["INSTANCE_ID_TO"].isna().all()  # EQ not loaded

    def test_duplicate_loaded_part_fans_out(self):
        # a part loaded under two INSTANCE_IDs yields one edge row per target
        # instance — deliberate: the reference resolves to both loads
        rows = pandas.DataFrame([
            {"ID": "eq", "KEY": "Model.profile", "VALUE": "p", "INSTANCE_ID": "i1"},
            {"ID": "eq", "KEY": "Model.profile", "VALUE": "p", "INSTANCE_ID": "i2"},
            {"ID": "sv", "KEY": "Model.DependentOn", "VALUE": "eq", "INSTANCE_ID": "i3"},
        ])
        relations = cgmes_tools.get_model_relations(rows)
        assert sorted(relations["INSTANCE_ID_TO"]) == ["i1", "i2"]

    def test_empty_data(self):
        empty = pandas.DataFrame(columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
        relations = cgmes_tools.get_model_relations(empty)
        assert list(relations.columns) == self.RELATION_COLUMNS and relations.empty


# ── Deprecated aliases ──────────────────────────────────────────────────────

class TestDeprecatedAliases:
    def test_old_names_warn_and_work(self, svedala_data):
        with pytest.warns(DeprecationWarning, match="generate_instance_ids"):
            ids = cgmes_tools.generate_instances_ID()
        assert isinstance(ids, dict)

        with pytest.warns(DeprecationWarning, match="count_GeneratingUnit_types"):
            result = cgmes_tools.statistics_GeneratingUnit_types(svedala_data)
        assert result is not None


# ── Visualization (draw_references_* render via _draw_references_graph) ─────────

class TestDrawReferences:
    def test_draw_references_to_renders_html(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        result = cgmes_tools.draw_references_to(svedala_data, subs, notebook=True)
        assert "new vis.Network" in result

    def test_draw_relations_aliases_warn(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        with pytest.warns(DeprecationWarning, match="draw_references_to"):
            cgmes_tools.draw_relations_to(svedala_data, subs, notebook=True)


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
        result = cgmes_tools.get_loaded_models(polars.from_pandas(svedala_data))
        assert set(result) == set(expected)
        order = ["ID", "PROFILE", "INSTANCE_ID"]
        for key, frame in expected.items():
            want = frame.astype(str).sort_values(order).reset_index(drop=True)
            got = result[key].to_pandas().astype(str).sort_values(order).reset_index(drop=True)
            assert got.equals(want)


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
    def test_draw_references_to_notebook(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        result = cgmes_tools.draw_references_to(svedala_data, subs, notebook=True)
        assert isinstance(result, str)
        assert "new vis.Network" in result
        assert hasattr(result, "_repr_html_")  # displays inline in Jupyter

    def test_draw_references_from_notebook(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        result = cgmes_tools.draw_references_from(svedala_data, subs, notebook=True)
        assert isinstance(result, str)
        assert "new vis.Network" in result

    def test_draw_references_notebook(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        result = cgmes_tools.draw_references(svedala_data, subs, notebook=True, levels=1)
        assert isinstance(result, str)
        assert "new vis.Network" in result

    def test_draw_references_polars(self, svedala_data):
        polars = pytest.importorskip("polars")
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        result = cgmes_tools.draw_references_to(polars.from_pandas(svedala_data), subs, notebook=True)
        assert "new vis.Network" in result

    def test_draw_references_to_file(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]["ID"].iloc[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = cgmes_tools.draw_references_to(svedala_data, subs, notebook=False, open_browser=False)
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
        result = cgmes_tools.count_GeneratingUnit_types(svedala_data)
        assert isinstance(result, pandas.DataFrame)


def test_unsupported_explicit_engine_rejected():
    tiny = pandas.DataFrame({"ID": ["a"], "KEY": ["Type"],
                             "VALUE": ["Breaker"], "INSTANCE_ID": ["i1"]})
    with pytest.raises(ValueError, match="cgmes_tools supports engine="):
        cgmes_tools.get_loaded_models(tiny, engine="duckdb")
