"""Comprehensive tests for all rdf_parser.py data manipulation functions.

Written BEFORE the tools/export refactor as a safety net.
All tests must pass both before and after the refactor.

Uses Svedala IGM data (EQ+SSH+TP+SV, ~95K rows, 50 types, 4 instances).
Benchmarked operations are marked for performance tracking.
"""
import pytest
import pandas
import triplets
from pathlib import Path

SVEDALA_DIR = Path("test_data/relicapgrid/Instance/Grid/IGM_Svedala")
SVEDALA_FILES = [
    str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SSH_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_TP_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SV_1.xml"),
]

SKIP_REASON = "Svedala test data not available (needs git submodule)"


@pytest.fixture(scope="module")
def svedala_data():
    """Load Svedala IGM dataset (module-scoped, loaded once)."""
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


@pytest.fixture(scope="module")
def svedala_eq():
    """Just the EQ instance (single file, for simpler tests)."""
    eq_file = SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"
    if not eq_file.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF([str(eq_file)])


# ── Query functions ─────────────────────────────────────────────────────────

class TestTypeTableview:
    def test_returns_dataframe(self, svedala_data):
        tv = svedala_data.type_tableview("ACLineSegment")
        assert isinstance(tv, pandas.DataFrame)
        assert len(tv) == 97

    def test_index_is_id(self, svedala_data):
        tv = svedala_data.type_tableview("ACLineSegment")
        assert tv.index.name == "ID"

    def test_columns_are_properties(self, svedala_data):
        tv = svedala_data.type_tableview("ACLineSegment")
        assert "Type" in tv.columns
        assert "IdentifiedObject.name" in tv.columns

    def test_string_to_number(self, svedala_data):
        tv = svedala_data.type_tableview("ACLineSegment", string_to_number=True)
        # May be float64, int64, or double[pyarrow] depending on backend
        assert pandas.api.types.is_numeric_dtype(tv["Conductor.length"])

    def test_string_to_number_false(self, svedala_data):
        tv = svedala_data.type_tableview("ACLineSegment", string_to_number=False)
        assert tv["Conductor.length"].dtype == object or "str" in str(tv["Conductor.length"].dtype)

    def test_nonexistent_type_returns_none(self, svedala_data):
        tv = svedala_data.type_tableview("NonExistentType123")
        assert tv is None

    def test_multivalue(self, svedala_data):
        tv = svedala_data.type_tableview("FullModel", multivalue=True)
        assert tv is not None
        assert len(tv) > 0

    @pytest.mark.benchmark(group="tools-query")
    def test_benchmark(self, benchmark, svedala_data):
        benchmark(svedala_data.type_tableview, "Terminal", string_to_number=False)


class TestKeyTableview:
    def test_returns_dataframe(self, svedala_data):
        tv = svedala_data.key_tableview("IdentifiedObject.name")
        assert isinstance(tv, pandas.DataFrame)
        assert len(tv) > 0

    def test_nonexistent_key_returns_none(self, svedala_data):
        tv = svedala_data.key_tableview("NonExistent.key")
        assert tv is None


class TestIdTableview:
    def test_returns_dataframe(self, svedala_data):
        # Get a known Substation ID
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]
        sub_id = subs["ID"].iloc[0]
        tv = triplets.rdf_parser.id_tableview(svedala_data, sub_id)
        assert isinstance(tv, pandas.DataFrame)
        assert len(tv) > 0


class TestTypesDict:
    def test_returns_dict(self, svedala_data):
        td = svedala_data.types_dict()
        assert isinstance(td, dict)
        assert "ACLineSegment" in td
        assert "Terminal" in td
        assert "Substation" in td

    def test_count_matches(self, svedala_data):
        td = svedala_data.types_dict()
        assert td["ACLineSegment"] == 97
        assert td["Substation"] == 56

    @pytest.mark.benchmark(group="tools-query")
    def test_benchmark(self, benchmark, svedala_data):
        benchmark(svedala_data.types_dict)


class TestGetObjectData:
    def test_returns_data(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]
        sub_id = subs["ID"].iloc[0]
        obj = svedala_data.get_object_data(sub_id)
        # Returns Series (single object) or DataFrame
        assert len(obj) > 0


class TestGetNamespaceMap:
    def test_returns_data(self, svedala_data):
        nsmap = triplets.rdf_parser.get_namespace_map(svedala_data)
        assert nsmap is not None
        # get_namespace_map returns a DataFrame with namespace info
        assert len(nsmap) > 0


# ── Reference functions ─────────────────────────────────────────────────────

class TestReferencesTo:
    def test_returns_dataframe(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]
        sub_id = subs["ID"].iloc[0]
        refs = svedala_data.references_to(sub_id)
        assert isinstance(refs, pandas.DataFrame)
        assert len(refs) > 0

    @pytest.mark.benchmark(group="tools-references")
    def test_benchmark(self, benchmark, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]
        sub_id = subs["ID"].iloc[0]
        benchmark(svedala_data.references_to, sub_id)


class TestReferencesFrom:
    def test_returns_dataframe(self, svedala_data):
        terms = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Terminal")]
        term_id = terms["ID"].iloc[0]
        refs = svedala_data.references_from(term_id)
        assert isinstance(refs, pandas.DataFrame)
        assert len(refs) > 0


class TestReferencesSimple:
    def test_returns_dataframe(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]
        sub_id = subs["ID"].iloc[0]
        refs = svedala_data.references_simple(sub_id)
        assert isinstance(refs, pandas.DataFrame)
        assert len(refs) > 0


class TestReferences:
    def test_returns_dataframe(self, svedala_data):
        subs = svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "Substation")]
        sub_id = subs["ID"].iloc[0]
        refs = svedala_data.references(sub_id)
        assert isinstance(refs, pandas.DataFrame)


# ── Filter functions ────────────────────────────────────────────────────────

class TestFilterByType:
    def test_returns_correct_type(self, svedala_data):
        filtered = triplets.rdf_parser.filter_by_type(svedala_data, "ACLineSegment")
        assert isinstance(filtered, pandas.DataFrame)
        assert len(filtered) > 0
        # All IDs in the result should be ACLineSegment IDs
        acl_ids = set(svedala_data[(svedala_data["KEY"] == "Type") & (svedala_data["VALUE"] == "ACLineSegment")]["ID"])
        assert set(filtered["ID"].unique()).issubset(acl_ids)

    @pytest.mark.benchmark(group="tools-filter")
    def test_benchmark(self, benchmark, svedala_data):
        benchmark(triplets.rdf_parser.filter_by_type, svedala_data, "ACLineSegment")


class TestFilterByTriplet:
    def test_returns_dataframe(self, svedala_data):
        filter_df = pandas.DataFrame({
            "ID": [svedala_data["ID"].iloc[10]],
            "KEY": [svedala_data["KEY"].iloc[10]],
            "VALUE": [svedala_data["VALUE"].iloc[10]],
        })
        filtered = triplets.rdf_parser.filter_by_triplet(svedala_data, filter_df)
        assert isinstance(filtered, pandas.DataFrame)
        assert len(filtered) > 0


# ── Mutate functions ────────────────────────────────────────────────────────

class TestSetValueAtKey:
    def test_modifies_data(self, svedala_eq):
        data = svedala_eq.copy()
        data.set_triplets_value_at_key("Model.description", "test_value")
        modified = data[data["KEY"] == "Model.description"]["VALUE"]
        if len(modified) > 0:
            assert all(v == "test_value" for v in modified.values)


class TestSetValueAtKeyAndID:
    def test_modifies_specific_row(self, svedala_data):
        data = svedala_data.copy()
        target = data[(data["KEY"] == "Type") & (data["VALUE"] == "Substation")].iloc[0]
        data.set_triplets_value_at_key_and_id("Type", "TestType", target["ID"])
        modified = data[(data["ID"] == target["ID"]) & (data["KEY"] == "Type")]
        assert modified["VALUE"].iloc[0] == "TestType"


class TestUpdateTripletFromTriplet:
    def test_update(self, svedala_eq):
        data = svedala_eq.copy()
        update = pandas.DataFrame({
            "ID": [data["ID"].iloc[5]],
            "KEY": [data["KEY"].iloc[5]],
            "VALUE": ["UPDATED_VALUE"],
            "INSTANCE_ID": [data["INSTANCE_ID"].iloc[5]],
        })
        result = data.update_triplets_from_triplets(update)
        assert isinstance(result, pandas.DataFrame)


class TestUpdateTripletFromTableview:
    def test_update(self, svedala_data):
        tv = svedala_data.type_tableview("Substation", string_to_number=False)
        if tv is not None and len(tv) > 0:
            data = svedala_data.copy()
            result = data.update_triplets_from_tableview(tv)
            assert isinstance(result, pandas.DataFrame)


class TestRemoveTripletFromTriplet:
    def test_removes_rows(self, svedala_data):
        to_remove = svedala_data.head(5)[["ID", "KEY", "VALUE"]]
        original_len = len(svedala_data)
        result = triplets.rdf_parser.remove_triplet_from_triplet(svedala_data, to_remove)
        assert len(result) < original_len


# ── Transform functions ─────────────────────────────────────────────────────

class TestTripletToTableviews:
    def test_returns_dict(self, svedala_eq):
        tvs = triplets.rdf_parser.triplet_to_tableviews(svedala_eq)
        assert isinstance(tvs, dict)
        assert len(tvs) > 0
        for name, df in tvs.items():
            assert isinstance(df, pandas.DataFrame)

    def test_multivalue(self, svedala_eq):
        tvs = triplets.rdf_parser.triplet_to_tableviews(svedala_eq, multivalue=True)
        assert len(tvs) > 0


class TestTableviewsToTriplet:
    def test_roundtrip(self, svedala_eq):
        tvs = triplets.rdf_parser.triplet_to_tableviews(svedala_eq)
        result = triplets.rdf_parser.tableviews_to_triplet(tvs)
        assert isinstance(result, pandas.DataFrame)
        assert "ID" in result.columns
        assert "KEY" in result.columns
        assert "VALUE" in result.columns


class TestTableviewToTriplet:
    def test_returns_dataframe(self, svedala_data):
        tv = svedala_data.type_tableview("ACLineSegment", string_to_number=False)
        result = tv.tableview_to_triplets()
        assert isinstance(result, pandas.DataFrame)
        assert "ID" in result.columns
        assert "KEY" in result.columns
        assert "VALUE" in result.columns

    def test_multivalue(self, svedala_data):
        tv = svedala_data.type_tableview("FullModel", multivalue=True)
        result = tv.tableview_to_triplets(multivalue=True)
        assert isinstance(result, pandas.DataFrame)


# ── Diff functions ──────────────────────────────────────────────────────────

class TestDiffBetweenTriplet:
    def test_returns_dataframe(self, svedala_eq):
        modified = svedala_eq.copy()
        modified.iloc[5, modified.columns.get_loc("VALUE")] = "CHANGED"
        diff = triplets.rdf_parser.diff_between_triplet(svedala_eq, modified)
        assert isinstance(diff, pandas.DataFrame)
        assert len(diff) > 0


class TestDiffBetweenInstance:
    def test_returns_dataframe(self, svedala_data):
        instances = svedala_data["INSTANCE_ID"].unique()
        if len(instances) >= 2:
            diff = svedala_data.diff_triplets_by_instance(instances[0], instances[1])
            assert isinstance(diff, pandas.DataFrame)


class TestPrintTripletDiff:
    def test_runs_without_error(self, svedala_eq):
        # Create modified copy using loc to avoid categorical setitem issues
        modified = svedala_eq.copy()
        # Use a row with plain string VALUE (not categorical)
        idx = modified.index[5]
        modified.at[idx, "VALUE"] = str(modified.at[idx, "VALUE"]) + "_CHANGED"
        triplets.rdf_parser.print_triplet_diff(svedala_eq, modified)


# ── Deprecated tools aliases (renamed in 0.1) ───────────────────────────────

class TestToolsDeprecatedAliases:
    def test_module_alias_warns_and_works(self, svedala_data):
        with pytest.warns(DeprecationWarning, match="filter_triplets_by_type"):
            result = triplets.tools.filter_by_type(svedala_data, "ACLineSegment")
        assert len(result) > 0

    def test_dataframe_method_alias_warns(self, svedala_data):
        with pytest.warns(DeprecationWarning, match="set_triplets_value_at_key"):
            svedala_data.copy().set_VALUE_at_KEY("Model.description", "x")

    def test_accessor_alias_warns(self, svedala_data):
        with pytest.warns(DeprecationWarning, match="filter_triplets_by_type"):
            result = svedala_data.triplets.filter_by_type("ACLineSegment")
        assert len(result) > 0

    def test_all_aliases_resolve(self):
        for old_name, new_name in triplets.tools.DEPRECATED_ALIASES.items():
            assert callable(getattr(triplets.tools, old_name)), old_name
            assert callable(getattr(triplets.tools, new_name)), new_name


class TestTripletsStringInvariant:
    """ID/KEY/VALUE are always strings or null — never mixed with numbers (issue #55)."""

    def test_pandas_roundtrip_keeps_nulls_null(self):
        tableview = pandas.DataFrame(
            {"ID": ["a", "b"], "Type": ["T", "T"], "x.y": ["1", None]}
        ).set_index("ID")
        trip = triplets.tools.tableview_to_triplets(tableview, engine="pandas")
        hole = trip[(trip["ID"] == "b") & (trip["KEY"] == "x.y")]["VALUE"]
        assert hole.isna().all()                      # null stays null, not "nan"
        non_null = trip["VALUE"].dropna()
        assert all(isinstance(v, str) for v in non_null)

    def test_pandas_roundtrip_stringifies_numbers(self, svedala_eq):
        tableview = svedala_eq.tableview_by_type("ACLineSegment", string_to_number=True)
        trip = triplets.tools.tableview_to_triplets(tableview, engine="pandas")
        non_null = trip["VALUE"].dropna()
        assert all(isinstance(v, str) for v in non_null)

    @pytest.mark.parametrize("engine", ["pandas", "polars"])
    def test_set_value_int_becomes_string(self, engine):
        frame = pandas.DataFrame({"ID": ["a"], "KEY": ["k"], "VALUE": ["old"], "INSTANCE_ID": ["i"]})
        if engine == "polars":
            polars = pytest.importorskip("polars")
            data = polars.from_pandas(frame)
            result = triplets.tools.set_triplets_value_at_key(data, "k", 42)
            assert result["VALUE"][0] == "42"
        else:
            triplets.tools.set_triplets_value_at_key(frame, "k", 42)
            assert frame["VALUE"].iloc[0] == "42"

    @pytest.mark.parametrize("engine", ["pandas", "polars"])
    def test_set_value_none_stays_null(self, engine):
        frame = pandas.DataFrame({"ID": ["a"], "KEY": ["k"], "VALUE": ["old"], "INSTANCE_ID": ["i"]})
        if engine == "polars":
            polars = pytest.importorskip("polars")
            data = polars.from_pandas(frame)
            result = triplets.tools.set_triplets_value_at_key(data, "k", None)
            assert result["VALUE"][0] is None         # not the string "None"
        else:
            triplets.tools.set_triplets_value_at_key(frame, "k", None)
            assert pandas.isna(frame["VALUE"].iloc[0])


class TestConvenienceAliases:
    """First-class aliases (no deprecation) that group functions by prefix for IDE autocomplete."""

    def test_aliases_are_same_function(self):
        for alias, target in triplets.tools.ALIASES.items():
            assert getattr(triplets.tools, alias) is getattr(triplets.tools, target), alias

    def test_aliases_work_without_warning(self, svedala_data):
        import warnings as warnings_module
        with warnings_module.catch_warnings():
            warnings_module.simplefilter("error", DeprecationWarning)
            counts = svedala_data.get_types_count()
            tv = svedala_data.tableview_by_type("ACLineSegment")
            accessor_tv = svedala_data.triplets.tableview_by_type("ACLineSegment")
        assert counts == svedala_data.types_dict()
        assert len(tv) == len(accessor_tv)


# ── Export functions ────────────────────────────────────────────────────────

class TestExportToExcel:
    @pytest.fixture(autouse=True)
    def _require_openpyxl(self):
        pytest.importorskip("openpyxl")

    def test_export_to_memory(self, svedala_eq):
        result = svedala_eq.export_to_excel(export_to_memory=True)
        assert result is not None
        if isinstance(result, list):
            assert len(result) > 0
            assert hasattr(result[0], 'read')
        else:
            assert hasattr(result, 'read')

    def test_export_single_file(self, svedala_eq):
        result = svedala_eq.export_to_excel(
            export_to_memory=True,
            single_file=True,
            filename="test.xlsx"
        )
        assert hasattr(result, 'read')
        assert result.name == "test.xlsx"

    @pytest.mark.benchmark(group="tools-export")
    def test_benchmark(self, benchmark, svedala_eq):
        benchmark(svedala_eq.export_to_excel, export_to_memory=True, single_file=True)


class TestExportToCsv:
    def test_export_to_memory(self, svedala_eq):
        result = svedala_eq.export_to_csv(export_to_memory=True)
        assert isinstance(result, list)
        assert len(result) > 0
        assert hasattr(result[0], 'read')


# CIM XML export engines; cython_pugixml skipped when the extension is not built
CIMXML_ENGINES = ["python_lxml", "cython_pugixml"]


def require_cimxml_engine(engine):
    if engine == "cython_pugixml":
        pytest.importorskip("triplets.export.cimxml_cython_pugixml")


class TestExportToCimxml:
    @pytest.mark.parametrize("engine", CIMXML_ENGINES)
    def test_export_to_memory(self, svedala_eq, engine):
        require_cimxml_engine(engine)
        from triplets.export_schema import schemas
        result = svedala_eq.export_to_cimxml(
            rdf_map=schemas.ENTSOE_CGMES_2_4_15_552_ED1,
            export_type="xml_per_instance",
            export_to_memory=True,
            engine=engine,
        )
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.parametrize("engine", CIMXML_ENGINES)
    def test_numeric_values_export(self, svedala_eq, engine):
        """Non-string VALUEs (e.g. numbers from edited tableviews) must export — issue #50."""
        require_cimxml_engine(engine)
        from triplets.export_schema import schemas
        data = svedala_eq.copy()
        data["VALUE"] = data["VALUE"].astype(object)
        data.loc[data["KEY"] == "Conductor.length", "VALUE"] = 42
        data.loc[data["KEY"] == "ACLineSegment.r", "VALUE"] = 1.5

        result = data.export_to_cimxml(
            rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
            export_type="xml_per_instance",
            export_to_memory=True,
            engine=engine,
        )
        result[0].seek(0)
        reimported = pandas.read_RDF([result[0]])
        lengths = reimported[reimported["KEY"] == "Conductor.length"]["VALUE"].astype(str).unique()
        assert list(lengths) == ["42"]

    def test_missing_columns_raise(self, svedala_eq, tmp_path):
        broken = svedala_eq.rename(columns={"VALUE": "VAL"})
        with pytest.raises(ValueError, match="missing columns.*VALUE"):
            broken.export_to_cimxml(export_to_memory=True)
        with pytest.raises(ValueError, match="missing columns.*VALUE"):
            triplets.export.export_to_nquads(broken, str(tmp_path / "x.nq"))

    def test_polars_input_exports(self, svedala_eq):
        """Polars input converts to pandas at the orchestrator and exports.

        Note: the mixed string/int VALUE problem from issue #50 cannot occur
        with polars input — polars columns are strictly typed, so a mixed
        object VALUE column is rejected at frame construction already.
        """
        polars = pytest.importorskip("polars")
        from triplets.export_schema import schemas
        result = polars.from_pandas(svedala_eq).triplets.export_to_cimxml(
            rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
            export_type="xml_per_instance",
            export_to_memory=True,
        )
        result[0].seek(0)
        reimported = pandas.read_RDF([result[0]])
        assert reimported.get_types_count()["ACLineSegment"] == svedala_eq.get_types_count()["ACLineSegment"]

    def test_duckdb_input_exports(self, svedala_eq, tmp_path):
        """DuckDB exports fetch the triplets table into pandas and export.

        As with polars, DuckDB columns are strictly typed (VALUE is VARCHAR),
        so the mixed string/int case from issue #50 cannot occur here.
        """
        duckdb = pytest.importorskip("duckdb")
        from triplets.export_schema import schemas
        con = duckdb.connect()
        con.register("source", svedala_eq)
        con.execute("CREATE TABLE triplets AS SELECT * FROM source")

        result = con.export_to_cimxml(
            rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
            export_type="xml_per_instance",
            export_to_memory=True,
        )
        result[0].seek(0)
        reimported = pandas.read_RDF([result[0]])
        assert reimported.get_types_count()["ACLineSegment"] == svedala_eq.get_types_count()["ACLineSegment"]

    def test_engines_produce_identical_output(self, svedala_eq):
        require_cimxml_engine("cython_pugixml")
        from triplets.export_schema import schemas
        outputs = {}
        for engine in CIMXML_ENGINES:
            result = svedala_eq.export_to_cimxml(
                rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
                export_type="xml_per_instance",
                export_to_memory=True,
                engine=engine,
            )
            outputs[engine] = pandas.read_RDF([result[0]])
        diff = triplets.tools.diff_between_triplet(outputs["python_lxml"], outputs["cython_pugixml"])
        # Distribution/NamespaceMap meta objects get fresh IDs per parse — exclude them
        meta_ids = set(diff[(diff["KEY"] == "Type") & diff["VALUE"].isin(["Distribution", "NamespaceMap"])]["ID"])
        real_diff = diff[~diff["ID"].isin(meta_ids) & (diff["KEY"] != "label")]
        assert len(real_diff) == 0, f"Engines differ:\n{real_diff.head(10)}"


class TestExportToNetworkx:
    def test_returns_graph(self, svedala_eq):
        networkx = pytest.importorskip("networkx")
        G = svedala_eq.export_to_networkx()
        assert isinstance(G, networkx.Graph)
        assert G.number_of_nodes() > 0


# ── Polars engine tests ─────────────────────────────────────────────────────

class TestPolarsMultivalue:
    """Test multivalue support in polars engine."""

    @pytest.fixture(scope="class")
    def pl_data(self):
        polars = pytest.importorskip("polars")
        if not SVEDALA_DIR.exists():
            pytest.skip(SKIP_REASON)
        return polars.read_rdf(SVEDALA_FILES)

    def test_type_tableview_multivalue(self, pl_data):
        from triplets.tools import polars_engine as pe
        tv = pe.type_tableview(pl_data, "FullModel", multivalue=True)
        assert tv is not None
        assert len(tv) > 0
        # DependentOn should be comma-separated for multi-value
        dep = tv["Model.DependentOn"][0]
        assert isinstance(dep, str)

    def test_type_tableview_multivalue_single_values(self, pl_data):
        import polars
        from triplets.tools import polars_engine as pe
        tv = pe.type_tableview(pl_data, "Substation", multivalue=True, string_to_number=False)
        assert tv is not None
        assert len(tv) > 0
        # Substation shouldn't have multi-values, so all should be plain strings
        for col in tv.columns:
            if col == "ID":
                continue
            assert tv[col].dtype == polars.Utf8 or tv[col].dtype == polars.String

    def test_type_tableview_parity_with_pandas(self, pl_data, svedala_data):
        """Polars multivalue and pandas multivalue should have same row count."""
        from triplets.tools import polars_engine as pe
        tv_pl = pe.type_tableview(pl_data, "ACLineSegment", multivalue=True, string_to_number=False)
        tv_pd = svedala_data.type_tableview("ACLineSegment", multivalue=True)
        assert len(tv_pl) == len(tv_pd)


class TestPolarsExportCsv:
    """Test CSV export via polars engine."""

    @pytest.fixture(scope="class")
    def pl_eq(self):
        polars = pytest.importorskip("polars")
        eq_file = SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"
        if not eq_file.exists():
            pytest.skip(SKIP_REASON)
        return polars.read_rdf([str(eq_file)])

    def test_csv_export_to_memory(self, pl_eq):
        result = triplets.export.export_to_csv(pl_eq, export_to_memory=True, single_file=True)
        assert isinstance(result, list)
        assert len(result) > 0
        assert hasattr(result[0], "read")

    def test_csv_export_multivalue(self, pl_eq):
        result = triplets.export.export_to_csv(pl_eq, export_to_memory=True, single_file=True, multivalue=True)
        assert isinstance(result, list)
        assert len(result) > 0


class TestPolarsExportNquads:
    """Test N-Quads export via polars engine."""

    @pytest.fixture(scope="class")
    def pl_eq(self):
        polars = pytest.importorskip("polars")
        eq_file = SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"
        if not eq_file.exists():
            pytest.skip(SKIP_REASON)
        return polars.read_rdf([str(eq_file)])

    def test_nquads_export(self, pl_eq, tmp_path):
        output = str(tmp_path / "test.nq")
        triplets.export.export_to_nquads(pl_eq, output)
        import os
        assert os.path.exists(output)
        with open(output) as f:
            lines = f.readlines()
        assert len(lines) == len(pl_eq)
        # Each line should be a valid N-Quad
        assert lines[0].endswith(" .\n")
        assert "<urn:uuid:" in lines[0]


class TestNquadsDatatypes:
    """With an export schema, literal attributes get xsd datatype annotations."""

    @pytest.fixture(scope="class")
    def nquads_lines(self, svedala_eq, tmp_path_factory):
        from triplets.export_schema import schemas
        output = str(tmp_path_factory.mktemp("nq") / "typed.nq")
        triplets.export.export_to_nquads(svedala_eq, output, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
        with open(output) as f:
            return f.readlines()

    def test_numeric_literal_gets_datatype(self, nquads_lines):
        length_lines = [l for l in nquads_lines if "Conductor.length" in l]
        assert length_lines
        assert all('^^<http://www.w3.org/2001/XMLSchema#float>' in l for l in length_lines)

    def test_reference_keys_stay_iris(self, nquads_lines):
        # xsd:anyURI keys (e.g. Model.DependentOn) are references, not typed literals
        dep_lines = [l for l in nquads_lines if "Model.DependentOn" in l]
        assert dep_lines
        assert all("^^" not in l for l in dep_lines)

    def test_rdflib_parses_export(self, nquads_lines, tmp_path):
        rdflib = pytest.importorskip("rdflib")
        path = tmp_path / "validate.nq"
        path.write_text("".join(nquads_lines))

        dataset = rdflib.Dataset()
        dataset.parse(str(path), format="nquads")
        assert len(dataset) == len(nquads_lines)

        # typed literals round-trip through rdflib with the right python type
        length_predicate = rdflib.URIRef("http://iec.ch/TC57/CIM100#Conductor.length")
        lengths = [obj for _, _, obj, _ in dataset.quads((None, length_predicate, None, None))]
        assert lengths
        for literal in lengths:
            assert literal.datatype == rdflib.XSD.float
            assert isinstance(literal.toPython(), float)

    def test_references_resolve_within_dataset(self, svedala_eq, nquads_lines, tmp_path):
        """Every urn:uuid reference resolves to a subject — except the references
        the source data itself knows are dangling (boundary objects, other models)."""
        rdflib = pytest.importorskip("rdflib")
        from triplets import cgmes_tools

        path = tmp_path / "refs.nq"
        path.write_text("".join(nquads_lines))
        dataset = rdflib.Dataset()
        dataset.parse(str(path), format="nquads")

        subjects = set()
        uuid_objects = set()
        for s, _, o, _ in dataset.quads((None, None, None, None)):
            subjects.add(str(s))
            if isinstance(o, rdflib.URIRef) and str(o).startswith("urn:uuid:"):
                uuid_objects.add(str(o))
        unresolved = {o.removeprefix("urn:uuid:") for o in uuid_objects - subjects}

        dangling = cgmes_tools.get_dangling_references(svedala_eq, detailed=True)
        known_dangling = set(dangling["VALUE_FROM"].astype(str))

        assert unresolved, "single EQ file must have boundary references"
        assert unresolved == unresolved & known_dangling, \
            f"references neither resolved nor known-dangling: {sorted(unresolved - known_dangling)[:5]}"

    def test_string_literal_stays_plain(self, nquads_lines):
        # xsd:string is the RDF 1.1 default — no annotation
        name_lines = [l for l in nquads_lines if "IdentifiedObject.name>" in l]
        assert name_lines
        assert all("^^" not in l for l in name_lines)

    def test_mrid_is_literal_not_reference(self, nquads_lines):
        # mRID is a string attribute by schema; the UUID heuristic must not turn it into a urn:uuid reference
        mrid_lines = [l for l in nquads_lines if "IdentifiedObject.mRID>" in l]
        assert mrid_lines
        for line in mrid_lines:
            obj = line.split("> ", 2)[2]  # object + graph part after subject and predicate
            assert obj.startswith('"'), line

    def test_without_schema_no_datatypes(self, svedala_eq, tmp_path):
        output = str(tmp_path / "untyped.nq")
        triplets.export.export_to_nquads(svedala_eq, output)
        with open(output) as f:
            content = f.read()
        assert "^^<" not in content

    def test_polars_engine_matches_pandas(self, svedala_eq, tmp_path, nquads_lines):
        polars = pytest.importorskip("polars")
        from triplets.export_schema import schemas
        output = str(tmp_path / "typed_pl.nq")
        triplets.export.export_to_nquads(polars.from_pandas(svedala_eq), output, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
        with open(output) as f:
            pl_lines = f.readlines()
        assert sorted(pl_lines) == sorted(nquads_lines)


# ── Roundtrip test (export CIM XML → reimport → compare) ────────────────────

class TestCimxmlRoundtrip:
    """Export Svedala EQ (CGMES 3.0) to CIM XML, reimport, verify data is identical."""

    @pytest.mark.parametrize("engine", CIMXML_ENGINES)
    def test_roundtrip_types_match(self, svedala_eq, engine):
        require_cimxml_engine(engine)
        from triplets.export_schema import schemas

        result = svedala_eq.export_to_cimxml(
            rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
            export_type="xml_per_instance",
            export_to_memory=True,
            engine=engine,
        )
        assert len(result) > 0
        result[0].seek(0)
        reimported = pandas.read_RDF([result[0]])

        # All object types should survive the roundtrip
        orig_types = set(svedala_eq.types_dict().keys()) - {"Distribution", "NamespaceMap"}
        reimp_types = set(reimported.types_dict().keys()) - {"Distribution", "NamespaceMap"}
        assert orig_types == reimp_types, f"Missing: {orig_types - reimp_types}, Extra: {reimp_types - orig_types}"

    @pytest.mark.parametrize("engine", CIMXML_ENGINES)
    def test_roundtrip_object_counts_match(self, svedala_eq, engine):
        require_cimxml_engine(engine)
        from triplets.export_schema import schemas

        result = svedala_eq.export_to_cimxml(
            rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
            export_type="xml_per_instance",
            export_to_memory=True,
            engine=engine,
        )
        result[0].seek(0)
        reimported = pandas.read_RDF([result[0]])

        orig_td = svedala_eq.types_dict()
        reimp_td = reimported.types_dict()
        for type_name in orig_td:
            if type_name in ("Distribution", "NamespaceMap"):
                continue
            assert orig_td[type_name] == reimp_td.get(type_name, 0), \
                f"{type_name}: {orig_td[type_name]} -> {reimp_td.get(type_name, 0)}"

    @pytest.mark.parametrize("engine", CIMXML_ENGINES)
    def test_roundtrip_no_data_diff(self, svedala_eq, engine):
        require_cimxml_engine(engine)
        from triplets.export_schema import schemas

        result = svedala_eq.export_to_cimxml(
            rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
            export_type="xml_per_instance",
            export_to_memory=True,
            engine=engine,
        )
        result[0].seek(0)
        reimported = pandas.read_RDF([result[0]])

        # Diff excluding meta rows (Distribution, NamespaceMap have different IDs per parse)
        diff = triplets.tools.diff_triplets(svedala_eq, reimported)
        meta_ids = set()
        for meta_type in ("Distribution", "NamespaceMap"):
            ids = diff[
                (diff["KEY"].astype(str) == "Type") &
                (diff["VALUE"].astype(str) == meta_type)
            ]["ID"].astype(str).unique()
            meta_ids.update(ids)
        data_diff = diff[~diff["ID"].astype(str).isin(meta_ids)]
        assert len(data_diff) == 0, f"Data diff has {len(data_diff)} rows:\n{data_diff.head(10)}"


# ── DuckDB tools tests ──────────────────────────────────────────────────────

class TestDuckdbTools:
    """Test triplet tools operations via DuckDB monkey-patched connection."""

    @pytest.fixture(scope="class")
    def db(self):
        duckdb = pytest.importorskip("duckdb")
        import triplets
        if not SVEDALA_DIR.exists():
            pytest.skip(SKIP_REASON)
        data = duckdb.connect()
        data.read_rdf(SVEDALA_FILES)
        return data

    def test_types_dict(self, db):
        td = db.types_dict()
        assert isinstance(td, dict)
        assert "ACLineSegment" in td
        assert "Substation" in td

    def test_type_tableview(self, db):
        tv = db.type_tableview("Substation").df()
        assert len(tv) > 0
        assert "IdentifiedObject.name" in tv.columns

    def test_filter_triplets_exact(self, db):
        df = db.filter_triplets(KEY="Type", VALUE="Substation").df()
        assert len(df) > 0
        assert all(df["KEY"] == "Type")

    def test_filter_triplets_regex(self, db):
        df = db.filter_triplets(KEY="Model.*", regex=True).df()
        assert len(df) > 0

    def test_filter_by_type(self, db):
        df = db.filter_triplets_by_type("ACLineSegment").df()
        assert len(df) > 0

    def test_references_to(self, db):
        sub_id = db.filter_triplets(KEY="Type", VALUE="Substation").df()["ID"].iloc[0]
        df = db.references_to(sub_id).df()
        assert isinstance(df, pandas.DataFrame)
