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

    def test_domain_and_domainincludes_both_bind_attributes(self, tmp_path):
        """Attribute→class binding is read from rdfs:domain (CIM-owned terms) and
        schema:domainIncludes (reused external terms) alike — the DatasetMetadata
        convention (see application-profiles-library#92)."""
        rdfs = tmp_path / "mini.rdf"
        rdfs.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
         xmlns:cims="http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#"
         xmlns:schema="https://schema.org/">
  <rdf:Description rdf:about="http://www.w3.org/ns/dcat#Dataset">
    <rdf:type rdf:resource="http://www.w3.org/2000/01/rdf-schema#Class"/>
  </rdf:Description>
  <rdf:Description rdf:about="https://cim4.eu/ns/Metadata-European#usedSettings">
    <rdf:type rdf:resource="http://www.w3.org/1999/02/22-rdf-syntax-ns#Property"/>
    <rdfs:domain rdf:resource="http://www.w3.org/ns/dcat#Dataset"/>
  </rdf:Description>
  <rdf:Description rdf:about="http://purl.org/dc/terms/accessRights">
    <rdf:type rdf:resource="http://www.w3.org/1999/02/22-rdf-syntax-ns#Property"/>
    <schema:domainIncludes rdf:resource="http://www.w3.org/ns/dcat#Dataset"/>
  </rdf:Description>
</rdf:RDF>
""", encoding="utf-8")
        data = rdfs_tools.load_all_to_dataframe(str(rdfs))
        found = set(rdfs_tools.get_class_parameters(data, "http://www.w3.org/ns/dcat#Dataset")["parameters"]["ID"])
        assert "https://cim4.eu/ns/Metadata-European#usedSettings" in found  # via rdfs:domain
        assert "http://purl.org/dc/terms/accessRights" in found              # via schema:domainIncludes


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
    def test_convert_profile(self, rdfs_profile):
        from triplets.rdfs_tools import cim_rdfs_to_json
        result = cim_rdfs_to_json.convert_profile(rdfs_profile)
        assert isinstance(result, dict)
        assert len(result) > 0


class TestOrphanedAttributes:
    """An attribute with no class binding (no rdfs:domain / schema:domainIncludes)
    is still emitted as a top-level schema entry — just not listed under any class —
    with a warning. A consistent profile produces no orphan warning."""
    DM = Path("rdfs/ENTSOE_NC_2.4.1/DatasetMetadata-AP-Voc-RDFS2020.rdf")
    LOG = "triplets.rdfs_tools.cim_rdfs_to_json"

    def _data(self):
        if not self.DM.exists():
            pytest.skip("NC 2.4.1 DatasetMetadata RDFS not available")
        return rdfs_tools.load_all_to_dataframe(str(self.DM))

    def test_consistent_profile_has_no_orphans(self, caplog):
        from triplets.rdfs_tools import cim_rdfs_to_json
        with caplog.at_level("WARNING", logger=self.LOG):
            profile = cim_rdfs_to_json.convert_profile(self._data())
        assert "title" in profile["Dataset"]["parameters"]      # bound attribute, listed
        assert not any("no class binding" in r.getMessage() for r in caplog.records)

    def test_orphaned_attribute_emitted_without_class(self, caplog):
        from triplets.rdfs_tools import cim_rdfs_to_json
        data = self._data()
        title = "http://purl.org/dc/terms/title"
        # strip title's class binding → orphan it
        orphaned = data[~((data.ID == title) & (data.KEY.isin(["domain", "domainIncludes"])))].copy()
        with caplog.at_level("WARNING", logger=self.LOG):
            profile = cim_rdfs_to_json.convert_profile(orphaned)
        assert "title" in profile                               # definition still emitted
        assert profile["title"].get("dataType") == "String"     # with its datatype preserved
        assert "title" not in profile["Dataset"]["parameters"]  # but no class references it
        assert any("no class binding" in r.getMessage() for r in caplog.records)
