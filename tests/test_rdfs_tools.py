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


# ── xsd:type: RDFS first, optional lookup table, else omit ──────────────────

_M01 = "http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#M:0..1"
_NS = "http://example.org/cim"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_CIMS = "http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#"
_XSD = "http://www.w3.org/2001/XMLSchema#"


def _parse_rdfs(tmp_path, body, name="mini.rdf"):
    rdfs = tmp_path / name
    rdfs.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="{_RDF}" xmlns:rdfs="{_RDFS}" xmlns:cims="{_CIMS}">
  <rdf:Description rdf:about="{_NS}#Thing">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>concrete</cims:stereotype>
  </rdf:Description>
{body}
</rdf:RDF>
""",
        encoding="utf-8",
    )
    return rdfs_tools.load_all_to_dataframe(str(rdfs))


def _attr(name, extra):
    return f"""  <rdf:Description rdf:about="{_NS}#{name}">
    <rdf:type rdf:resource="{_RDF}Property"/>
    <rdfs:domain rdf:resource="{_NS}#Thing"/>
    <cims:multiplicity rdf:resource="{_M01}"/>
    {extra}
  </rdf:Description>"""


class TestXsdTypeResolution:
    """xsd:type on attributes: RDFS XMLSchema range, then lookup table, else omit."""

    def test_rdfs_range_xsd_wins_without_lookup(self, tmp_path):
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        data = _parse_rdfs(tmp_path, _attr(
            "Thing.x", f'<rdfs:range rdf:resource="{_XSD}float"/>'))
        profile = c.convert_profile(data, data_types_map=None)
        assert profile["Thing.x"]["type"] == "Attribute"
        assert profile["Thing.x"]["xsd:type"] == "xsd:float"
        assert "dataType" not in profile["Thing.x"]

    def test_ed2_range_to_cimdatatype_without_cims_datatype(self, tmp_path):
        """501 Ed2: attribute rdfs:range is the CIMDatatype; XSD sits on .value."""
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        body = "\n".join([
            _attr("Thing.rotation", f'<rdfs:range rdf:resource="{_NS}#AngleDegrees"/>'),
            f"""  <rdf:Description rdf:about="{_NS}#AngleDegrees">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>CIMDatatype</cims:stereotype>
  </rdf:Description>
  <rdf:Description rdf:about="{_NS}#AngleDegrees.value">
    <rdf:type rdf:resource="{_RDF}Property"/>
    <rdfs:domain rdf:resource="{_NS}#AngleDegrees"/>
    <rdfs:range rdf:resource="{_XSD}float"/>
    <cims:multiplicity rdf:resource="{_M01}"/>
  </rdf:Description>""",
        ])
        data = _parse_rdfs(tmp_path, body)
        profile = c.convert_profile(data, data_types_map=None)
        assert profile["Thing.rotation"]["type"] == "Attribute"
        assert profile["Thing.rotation"]["dataType"] == "AngleDegrees"
        assert profile["Thing.rotation"]["xsd:type"] == "xsd:float"

    def test_cimdatatype_value_xsd_range_without_lookup(self, tmp_path):
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        body = "\n".join([
            _attr("Thing.rotation", f'<cims:dataType rdf:resource="{_NS}#AngleDegrees"/>'),
            f"""  <rdf:Description rdf:about="{_NS}#AngleDegrees">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>CIMDatatype</cims:stereotype>
  </rdf:Description>
  <rdf:Description rdf:about="{_NS}#AngleDegrees.value">
    <rdf:type rdf:resource="{_RDF}Property"/>
    <rdfs:domain rdf:resource="{_NS}#AngleDegrees"/>
    <rdfs:range rdf:resource="{_XSD}float"/>
    <cims:multiplicity rdf:resource="{_M01}"/>
  </rdf:Description>""",
        ])
        data = _parse_rdfs(tmp_path, body)
        profile = c.convert_profile(data, data_types_map=None)
        assert profile["Thing.rotation"]["xsd:type"] == "xsd:float"
        assert profile["Thing.rotation"]["dataType"] == "AngleDegrees"
        assert profile["AngleDegrees"]["xsd:type"] == "xsd:float"

    def test_lookup_table_when_rdfs_has_no_xsd(self, tmp_path):
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        body = "\n".join([
            _attr("Thing.length", f'<cims:dataType rdf:resource="{_NS}#Float"/>'),
            f"""  <rdf:Description rdf:about="{_NS}#Float">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>Primitive</cims:stereotype>
  </rdf:Description>""",
        ])
        data = _parse_rdfs(tmp_path, body)
        profile = c.convert_profile(data)  # default cgmes_data_types_map
        assert profile["Thing.length"]["xsd:type"] == "xsd:float"
        assert profile["Float"]["xsd:type"] == "xsd:float"

    def test_lookup_walks_cimdatatype_value_to_primitive(self, tmp_path):
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        body = "\n".join([
            _attr("Thing.energy", f'<cims:dataType rdf:resource="{_NS}#RealEnergy"/>'),
            f"""  <rdf:Description rdf:about="{_NS}#RealEnergy">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>CIMDatatype</cims:stereotype>
  </rdf:Description>
  <rdf:Description rdf:about="{_NS}#RealEnergy.value">
    <rdf:type rdf:resource="{_RDF}Property"/>
    <rdfs:domain rdf:resource="{_NS}#RealEnergy"/>
    <cims:dataType rdf:resource="{_NS}#Float"/>
    <cims:multiplicity rdf:resource="{_M01}"/>
  </rdf:Description>
  <rdf:Description rdf:about="{_NS}#Float">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>Primitive</cims:stereotype>
  </rdf:Description>""",
        ])
        data = _parse_rdfs(tmp_path, body)
        # RealEnergy is not in the default map; Float is
        profile = c.convert_profile(data, data_types_map={"Float": "xsd:float"})
        assert profile["Thing.energy"]["xsd:type"] == "xsd:float"

    def test_omit_xsd_type_when_neither_rdfs_nor_table(self, tmp_path):
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        body = "\n".join([
            _attr("Thing.d", f'<cims:dataType rdf:resource="{_NS}#Duration"/>'),
            f"""  <rdf:Description rdf:about="{_NS}#Duration">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>Primitive</cims:stereotype>
  </rdf:Description>""",
        ])
        data = _parse_rdfs(tmp_path, body)
        profile = c.convert_profile(data, data_types_map=None)
        assert profile["Thing.d"]["type"] == "Attribute"
        assert profile["Thing.d"]["dataType"] == "Duration"
        assert "xsd:type" not in profile["Thing.d"]
        assert "xsd:type" not in profile["Duration"]

    def test_default_map_also_omits_unknown_cim_names(self, tmp_path):
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        body = "\n".join([
            _attr("Thing.d", f'<cims:dataType rdf:resource="{_NS}#Duration"/>'),
            f"""  <rdf:Description rdf:about="{_NS}#Duration">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>Primitive</cims:stereotype>
  </rdf:Description>""",
        ])
        data = _parse_rdfs(tmp_path, body)
        profile = c.convert_profile(data)  # Duration is not in cgmes_data_types_map
        assert "xsd:type" not in profile["Thing.d"]

    def test_rdfs_xsd_beats_lookup_table(self, tmp_path):
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        data = _parse_rdfs(tmp_path, _attr(
            "Thing.flag",
            f'<cims:dataType rdf:resource="{_NS}#Float"/>'
            f'<rdfs:range rdf:resource="{_XSD}boolean"/>',
        ) + f"""
  <rdf:Description rdf:about="{_NS}#Float">
    <rdf:type rdf:resource="{_RDFS}Class"/>
    <cims:stereotype>Primitive</cims:stereotype>
  </rdf:Description>""")
        profile = c.convert_profile(data, data_types_map={"Float": "xsd:float"})
        assert profile["Thing.flag"]["xsd:type"] == "xsd:boolean"

    def test_resolve_xsd_type_helper_direct(self, tmp_path):
        from triplets.rdfs_tools import cim_rdfs_to_json as c
        data = _parse_rdfs(tmp_path, _attr(
            "Thing.x", f'<rdfs:range rdf:resource="{_XSD}integer"/>'))
        assert c.resolve_xsd_type(
            data, range_uri=f"{_XSD}integer", data_types_map=None
        ) == "xsd:integer"
        assert c.resolve_xsd_type(
            data, data_type_uri=f"{_NS}#NoSuchType", data_types_map=None
        ) is None
        assert c.resolve_xsd_type(
            data, data_type_uri=f"{_NS}#Float",
            data_types_map={"Float": "xsd:float"},
        ) == "xsd:float"
