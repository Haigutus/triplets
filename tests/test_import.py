"""Tests for RDF import (pandas.read_RDF)."""
import pandas
import triplets


class TestImportNC:
    """Import tests for Network Code files."""

    def test_columns(self, nc_data):
        key, data = nc_data
        assert list(data.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]

    def test_not_empty(self, nc_data):
        key, data = nc_data
        assert len(data) > 0

    def test_types_dict_no_empty(self, nc_data):
        key, data = nc_data
        types = data.types_dict()
        assert "" not in types, f"Empty type found in {key}"

    def test_has_distribution(self, nc_data):
        key, data = nc_data
        types = data.types_dict()
        assert "Distribution" in types

    def test_has_namespace_map(self, nc_data):
        key, data = nc_data
        types = data.types_dict()
        assert "NamespaceMap" in types

    def test_single_instance_id(self, nc_data):
        key, data = nc_data
        assert data["INSTANCE_ID"].nunique() == 1

    def test_has_keyword(self, nc_data):
        key, data = nc_data
        keywords = data[data["KEY"] == "keyword"]["VALUE"]
        assert len(keywords) > 0, f"No keyword found in {key}"

    def test_conforms_to_not_empty(self, nc_data):
        key, data = nc_data
        ct = data[data["KEY"] == "conformsTo"]
        if len(ct) > 0:
            for val in ct["VALUE"].values:
                assert val and str(val).strip(), f"Empty conformsTo in {key}"


class TestImportCGMES:
    """Import tests for CGMES files."""

    def test_columns(self, cgmes_data):
        key, data = cgmes_data
        assert list(data.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]

    def test_not_empty(self, cgmes_data):
        key, data = cgmes_data
        assert len(data) > 0

    def test_types_dict_no_empty(self, cgmes_data):
        key, data = cgmes_data
        types = data.types_dict()
        assert "" not in types, f"Empty type found in {key}"

    def test_single_instance_id(self, cgmes_data):
        key, data = cgmes_data
        assert data["INSTANCE_ID"].nunique() == 1

    def test_has_model_profile_or_keyword(self, cgmes_data):
        key, data = cgmes_data
        has_profile = len(data[data["KEY"] == "Model.profile"]) > 0
        has_keyword = len(data[data["KEY"] == "keyword"]) > 0
        assert has_profile or has_keyword, f"No profile or keyword in {key}"
