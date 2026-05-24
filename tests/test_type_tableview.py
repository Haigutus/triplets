"""Tests for type_tableview and types_dict."""
import pandas
import triplets


class TestTypeTableviewNC:
    """type_tableview tests for NC files."""

    def test_row_count_matches_types_dict(self, nc_data):
        key, data = nc_data
        types = data.types_dict()
        for type_name, count in types.items():
            if type_name in ("Distribution", "NamespaceMap", "Dataset", ""):
                continue
            tv = data.type_tableview(type_name)
            assert len(tv) == count, f"{key}/{type_name}: tableview has {len(tv)} rows, expected {count}"

    def test_index_is_id(self, nc_data):
        key, data = nc_data
        types = data.types_dict()
        for type_name in types:
            if type_name in ("Distribution", "NamespaceMap", "Dataset", ""):
                continue
            tv = data.type_tableview(type_name)
            if len(tv) > 0:
                assert tv.index.name == "ID" or tv.index.name is None
                break

    def test_columns_are_properties(self, nc_data):
        key, data = nc_data
        types = data.types_dict()
        for type_name in types:
            if type_name in ("Distribution", "NamespaceMap", "Dataset", ""):
                continue
            tv = data.type_tableview(type_name)
            if len(tv) > 0:
                assert len(tv.columns) > 0, f"{key}/{type_name}: no columns"
                break

    def test_nonexistent_type_returns_none(self, nc_data):
        key, data = nc_data
        tv = data.type_tableview("NonExistentType12345")
        assert tv is None


class TestTypeTableviewCGMES:
    """type_tableview tests for CGMES files."""

    def test_row_count_matches_types_dict(self, cgmes_data):
        key, data = cgmes_data
        types = data.types_dict()
        for type_name, count in types.items():
            if type_name in ("Distribution", "NamespaceMap", "FullModel", ""):
                continue
            tv = data.type_tableview(type_name)
            assert len(tv) == count, f"{key}/{type_name}: tableview has {len(tv)} rows, expected {count}"

    def test_eq_has_acline_segment(self, cgmes_data):
        key, data = cgmes_data
        if key != "EQ":
            return
        types = data.types_dict()
        assert "ACLineSegment" in types, "EQ should contain ACLineSegment"
        tv = data.type_tableview("ACLineSegment")
        assert len(tv) > 0

    def test_eq_has_terminal(self, cgmes_data):
        key, data = cgmes_data
        if key != "EQ":
            return
        types = data.types_dict()
        assert "Terminal" in types, "EQ should contain Terminal"
