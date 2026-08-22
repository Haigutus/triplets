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
    def test_returns_dict(self, svedala_data):
        models = cgmes_tools.get_loaded_models(svedala_data)
        assert isinstance(models, dict)


class TestGetLoadedModelParts:
    def test_returns_dataframe(self, svedala_data):
        parts = cgmes_tools.get_loaded_model_parts(svedala_data)
        assert isinstance(parts, pandas.DataFrame)
        assert len(parts) == 4  # EQ, SSH, TP, SV

    def test_metadata_stays_text(self, svedala_data):
        parts = cgmes_tools.get_loaded_model_parts(svedala_data)
        assert parts["Model.version"].map(type).eq(str).all()

    def test_both_header_kinds(self, svedala_data):
        if not Path(SVEDALA_ER).exists():
            pytest.skip(SKIP_REASON)
        data = pandas.concat([svedala_data, pandas.read_RDF([SVEDALA_ER])], ignore_index=True)
        parts = cgmes_tools.get_loaded_model_parts(data)
        assert len(parts) == 5
        assert set(parts["Type"]) == {"FullModel", "Dataset"}
        # union of columns across header kinds
        assert "Model.profile" in parts.columns and "conformsTo" in parts.columns


PROFILE_COLUMNS = ["INSTANCE_ID", "label", "HEADER", "HEADER_ID", "KEY", "VALUE", "PROFILE"]


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
        assert profiles["PROFILE"].isna().all()  # no rdf_map given

    def test_dataset_header(self, mixed_data):
        profiles = cgmes_tools.get_loaded_profiles(mixed_data)
        er = profiles[profiles["HEADER"] == "Dataset"]
        assert set(er["KEY"]) == {"keyword", "conformsTo"}
        # keyword outranks conformsTo (priority order within the instance)
        assert er["KEY"].tolist() == ["keyword", "conformsTo"]
        assert er[er["KEY"] == "keyword"]["VALUE"].tolist() == ["ER"]

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

    def test_rdf_map_resolution(self, mixed_data):
        # exact ProfileMetadata identity — no URL prefixes hardcoded anywhere
        rdf_map = {"EQ": {"ProfileMetadata": {"versionIRI": "http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0"}},
                   "ER": {"ProfileMetadata": {"keyword": "ER",
                                              "conformsTo": "https://ap.cim4.eu/EquipmentReliability/2.4"}}}
        profiles = cgmes_tools.get_loaded_profiles(mixed_data, rdf_map=rdf_map)
        by_key = profiles.set_index("KEY")["PROFILE"]
        assert by_key["keyword"] == "ER" and by_key["conformsTo"] == "ER"
        assert set(profiles[profiles["VALUE"].str.contains("CoreEquipment")]["PROFILE"]) == {"EQ"}

    def test_rdf_map_legacy_url_fallback(self):
        legacy = pandas.DataFrame([
            {"ID": "fm", "KEY": "Type", "VALUE": "FullModel", "INSTANCE_ID": "i1"},
            {"ID": "fm", "KEY": "Model.profile", "VALUE": "http://entsoe.eu/CIM/EquipmentCore/3/1", "INSTANCE_ID": "i1"},
        ])
        profiles = cgmes_tools.get_loaded_profiles(legacy, rdf_map={"EQ": {}})
        assert profiles["PROFILE"].tolist() == ["EQ"]

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

    def test_identifier_alias_no_duplicate_edges(self):
        if not Path(SVEDALA_RA).exists():
            pytest.skip(SKIP_REASON)
        # RA declares the same uuid as rdf:about and dcterms:identifier — the
        # alias must not duplicate the resolved edge
        data = pandas.read_RDF([SVEDALA_RA, SVEDALA_RAS])
        relations = cgmes_tools.get_model_relations(data)
        to_ra = relations[relations["ID_TO"] == "f7b94ef6-e043-4d2a-a359-2718e6e20507"]
        assert len(to_ra) == 1 and to_ra["INSTANCE_ID_TO"].notna().all()

    def test_prefixed_identifier_alias_resolves(self):
        # a header whose identifier carries the urn:uuid: prefix (element text
        # is not clean_ID'd at parse — TC-Boundary-Header-Dataset style) still
        # resolves as a target through the normalized alias
        rows = pandas.DataFrame([
            {"ID": "aaa", "KEY": "conformsTo", "VALUE": "https://ap.cim4.eu/Example/1.0", "INSTANCE_ID": "i1"},
            {"ID": "aaa", "KEY": "identifier", "VALUE": "urn:uuid:1234", "INSTANCE_ID": "i1"},
            {"ID": "bbb", "KEY": "requires", "VALUE": "urn:uuid:1234", "INSTANCE_ID": "i2"},
        ])
        for frame in (rows, pytest.importorskip("polars").from_pandas(rows)):
            relations = cgmes_tools.get_model_relations(frame)
            edge = pandas.DataFrame(relations) if isinstance(relations, pandas.DataFrame) else relations.to_pandas()
            assert edge["ID_TO"].tolist() == ["1234"]
            assert edge["INSTANCE_ID_TO"].tolist() == ["i1"]

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
