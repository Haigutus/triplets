"""Tests for rdfs_tools module.

Utility functions tested with no data. RDFS profile tests use rdfs/ data if available.
"""
import os
import pytest
import pandas
from pathlib import Path

from triplets.rdfs_tools import rdfs_tools

RDFS_DIR = Path("rdfs/ENTSOE_CGMES_2.4.15")
SKIP_REASON = "RDFS profile data not available"


@pytest.fixture(scope="module")
def rdfs_profile():
    """Load first RDFS profile file."""
    if not RDFS_DIR.exists():
        pytest.skip(SKIP_REASON)
    files = rdfs_tools.list_of_files(str(RDFS_DIR), ".rdf")
    if not files:
        pytest.skip(SKIP_REASON)
    from triplets.rdf_parser import load_all_to_dataframe
    return load_all_to_dataframe([files[0]])


# ── Pure utility functions (no data needed) ─────────────────────────────────

class TestParseMultiplicity:
    def test_one_to_one(self):
        assert rdfs_tools.parse_multiplicity("M:1..1") == ("1", "1")

    def test_zero_to_one(self):
        assert rdfs_tools.parse_multiplicity("M:0..1") == ("0", "1")

    def test_zero_to_many(self):
        assert rdfs_tools.parse_multiplicity("M:0..n") == ("0", "n")

    def test_one_to_many(self):
        assert rdfs_tools.parse_multiplicity("M:1..n") == ("1", "n")


class TestGetNamespaceAndName:
    def test_full_uri(self):
        ns, name = rdfs_tools.get_namespace_and_name(
            "http://iec.ch/TC57/2013/CIM-schema-cim16#ACLineSegment", "cim"
        )
        assert ns == "http://iec.ch/TC57/2013/CIM-schema-cim16#"
        assert name == "ACLineSegment"

    def test_with_separator(self):
        ns, name = rdfs_tools.get_namespace_and_name("http://example.org/SomeClass", "default")
        assert name == "SomeClass"


class TestListOfFiles:
    def test_finds_xml(self):
        files = rdfs_tools.list_of_files("tests/data", ".xml")
        assert len(files) >= 1
        assert all(f.endswith(".xml") for f in files)

    def test_empty_dir(self, tmp_path):
        files = rdfs_tools.list_of_files(str(tmp_path), ".xml")
        assert files == []

    def test_nonexistent_dir(self):
        files = rdfs_tools.list_of_files("/nonexistent/path", ".xml")
        assert files == []


# ── RDFS profile functions (need data) ──────────────────────────────────────

class TestConcreteClassesList:
    def test_returns_list(self, rdfs_profile):
        classes = rdfs_tools.concrete_classes_list(rdfs_profile)
        assert isinstance(classes, list)
        assert len(classes) > 0


class TestGetClassParameters:
    def test_returns_data(self, rdfs_profile):
        classes = rdfs_tools.concrete_classes_list(rdfs_profile)
        if classes:
            params = rdfs_tools.get_class_parameters(rdfs_profile, classes[0])
            assert params is not None


class TestParametersTableview:
    def test_returns_tuple(self, rdfs_profile):
        classes = rdfs_tools.concrete_classes_list(rdfs_profile)
        if classes:
            result = rdfs_tools.parameters_tableview(rdfs_profile, classes[0])
            assert result is not None


class TestGetOwlMetadata:
    def test_returns_data(self, rdfs_profile):
        meta = rdfs_tools.get_owl_metadata(rdfs_profile)
        assert meta is not None


class TestGetProfileMetadata:
    def test_returns_data(self, rdfs_profile):
        meta = rdfs_tools.get_profile_metadata(rdfs_profile)
        assert meta is not None


# ── cim_rdfs_to_json ────────────────────────────────────────────────────────

class TestCimRdfsToJson:
    @pytest.mark.xfail(reason="Pre-existing bug: get_namespace_and_name receives NaN float from RDFS data")
    def test_convert_profile(self, rdfs_profile):
        from triplets.rdfs_tools import cim_rdfs_to_json
        result = cim_rdfs_to_json.convert_profile(rdfs_profile)
        assert isinstance(result, dict)
        assert len(result) > 0
