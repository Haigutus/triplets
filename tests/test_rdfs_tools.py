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
    """Load first RDFS profile file the way the schema generator does (absolute form)."""
    if not RDFS_DIR.exists():
        pytest.skip(SKIP_REASON)
    files = sorted(rdfs_tools.list_of_files(str(RDFS_DIR), ".rdf"))   # os.walk order differs per machine
    if not files:
        pytest.skip(SKIP_REASON)
    return rdfs_tools.load_all_to_dataframe([files[0]])


# ── Pure utility functions (no data needed) ─────────────────────────────────

def test_get_namespace_and_name_forms():
    base = "http://iec.ch/TC57/CIM100"
    assert rdfs_tools.get_namespace_and_name("http://iec.ch/TC57/CIM100#Breaker", base) == ("http://iec.ch/TC57/CIM100#", "Breaker")
    assert rdfs_tools.get_namespace_and_name("#Breaker", base) == ("http://iec.ch/TC57/CIM100#", "Breaker")
    assert rdfs_tools.get_namespace_and_name("Breaker", base) == ("http://iec.ch/TC57/CIM100#", "Breaker")   # bare: a fragment, never '/'
    assert rdfs_tools.get_namespace_and_name("https://schema.org/name", base) == ("https://schema.org/", "name")

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

    def test_relative_reference_without_default_raises(self):
        """The generator passes no default namespace: its parse is absolute, so a relative
        reference is a parse bug, not something to guess a namespace for."""
        assert rdfs_tools.get_namespace_and_name("http://iec.ch/TC57/CIM100#ACLineSegment") == \
            ("http://iec.ch/TC57/CIM100#", "ACLineSegment")
        assert rdfs_tools.get_namespace_and_name("http://purl.org/dc/terms/issued") == ("http://purl.org/dc/terms/", "issued")
        assert rdfs_tools.get_namespace_and_name("#DiagramStyle", "http://iec.ch/TC57/CIM100") == \
            ("http://iec.ch/TC57/CIM100#", "DiagramStyle")
        with pytest.raises(ValueError, match="absolute IRI"):
            rdfs_tools.get_namespace_and_name("#DiagramStyle")


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


@pytest.mark.parametrize("uri", ["foo/Bar", "./Name"])
def test_get_namespace_and_name_rejects_other_relative_forms(uri):
    """Only a bare or #name takes the default; any other relative form raises, default or not."""
    for default in (None, "http://iec.ch/TC57/CIM100"):
        with pytest.raises(ValueError, match="absolute IRI"):
            rdfs_tools.get_namespace_and_name(uri, default)


def test_get_namespace_and_name_any_scheme_is_absolute():
    assert rdfs_tools.get_namespace_and_name("urn:uuid:abc") == ("urn:uuid:", "abc")
    assert rdfs_tools.get_namespace_and_name("urn:uuid:abc", "http://x") == ("urn:uuid:", "abc")   # never rewritten


def test_list_of_files_is_sorted(tmp_path):
    for name in ("b.rdf", "a.rdf", "c.rdf"):
        (tmp_path / name).write_text("")
    assert [Path(f).name for f in rdfs_tools.list_of_files(str(tmp_path), ".rdf")] == ["a.rdf", "b.rdf", "c.rdf"]


def test_shipped_bundles_first_wins_is_order_independent():
    """The flat maps merge profile sections first-wins; that is safe only while no name has a
    conflicting namespace or entry type across sections (primitives DateTime / URI excepted)."""
    import json
    import triplets.export_schema
    for path in sorted(Path(triplets.export_schema.__file__).parent.glob("*.json")):
        seen, conflicts = {}, set()
        for section in json.loads(path.read_text()).values():
            if not isinstance(section, dict):
                continue
            for name, entry in section.items():
                if isinstance(entry, dict) and (entry.get("namespace") or entry.get("type")):
                    key = (entry.get("namespace"), entry.get("type"))
                    if seen.setdefault(name, key) != key:
                        conflicts.add(name)
        assert conflicts <= {"DateTime", "URI"}, (path.name, sorted(conflicts)[:10])


def test_get_namespace_and_name_prefixed_name_is_not_absolute():
    """A prefixed name (cim:Foo) or a multiplicity (M:1..1) is no IRI — without a default it raises."""
    for text in ("cim:Foo", "M:1..1"):
        with pytest.raises(ValueError, match="absolute IRI"):
            rdfs_tools.get_namespace_and_name(text)


_PROF = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:prof="http://www.w3.org/ns/dx/prof/">
  <rdf:Description rdf:about="{profile}">
    <owl:versionIRI rdf:resource="{version}"/>
    <prof:hasResource rdf:resource="{vocabulary}"/>
    <prof:hasResource rdf:resource="https://ap-con.cim4.eu/DatasetMetadata-Simple/3.0"/>
  </rdf:Description>
</rdf:RDF>
"""


def test_profile_version_iris_links_ncp_and_cgmes_shapes(tmp_path):
    """NCP: the vocabulary resource is the RDFS versionIRI. CGMES: the RDFS versionIRI is
    the profile's own. A resource two profiles share (constraints) identifies neither."""
    from triplets.rdfs_tools.cim_rdfs_to_json import profile_version_iris
    (tmp_path / "ER.rdf").write_text(_PROF.format(
        profile="https://ap.cim4.eu/EquipmentReliability",
        version="https://ap.cim4.eu/EquipmentReliability/2.5",
        vocabulary="https://ap-voc.cim4.eu/EquipmentReliability/2.5"), encoding="utf-8")
    (tmp_path / "EQ.rdf").write_text(_PROF.format(
        profile="http://iec.ch/TC57/ns/CIM/CoreEquipment-EU#Ontology",
        version="http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0",
        vocabulary="http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0/vocabulary"), encoding="utf-8")

    iris = profile_version_iris(tmp_path)

    assert iris["https://ap-voc.cim4.eu/EquipmentReliability/2.5"] == "https://ap.cim4.eu/EquipmentReliability/2.5"
    assert iris["http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0"] == "http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0"
    assert "https://ap-con.cim4.eu/DatasetMetadata-Simple/3.0" not in iris
    assert profile_version_iris(tmp_path / "missing") == {}


@pytest.mark.parametrize("bundle", ["ENTSOE_CGMES_2.4.15", "ENTSOE_CGMES_3.0.0", "ENTSOE_NC_2.4.1", "ENTSOE_NC_2.5.0"])
def test_shipped_bundles_carry_both_iris(bundle):
    """Every section has the RDFS versionIRI (CGMES 2.4.15: its entsoeURI) and the
    profileVersionIRI headers declare (PROF if present, else the versionIRI); CGMES uses
    one IRI for both, NC two."""
    import json
    import triplets.export_schema
    for serialization in ("552_ED1", "552_ED2"):
        path = Path(triplets.export_schema.__file__).parent / f"{bundle}_{serialization}.json"
        sections = {name: section["ProfileMetadata"] for name, section in json.loads(path.read_text()).items()
                    if isinstance(section, dict) and "ProfileMetadata" in section}
        missing = [name for name, metadata in sections.items()
                   if not (metadata.get("versionIRI") and metadata.get("profileVersionIRI"))]
        assert sections and not missing, (path.name, missing)
        same = {metadata["versionIRI"] == metadata["profileVersionIRI"] for metadata in sections.values()}
        assert same == ({True} if bundle.startswith("ENTSOE_CGMES") else {False}), path.name



def test_xsd_types_from_rdfs():
    """CIMDatatype .value → XSD range, Primitive or another CIMDatatype; cycles,
    non-XSD ranges and ordinary classes with a .value resolve to nothing."""
    from triplets.rdfs_tools.cim_rdfs_to_json import xsd_types_from_rdfs
    cim, xsd = "http://iec.ch/TC57/CIM100#", "http://www.w3.org/2001/XMLSchema#"
    datatype = [("stereotype", "CIMDatatype")]
    classes = {
        "Area": (datatype, ("dataType", cim + "Float")),                    # Primitive
        "Money": ([("isCIMDatatype", "True")], ("range", xsd + "decimal")),  # 501 Ed2
        "Alias": ([("stereotype", "http://iec.ch/TC57/NonStandard/UML#CIMDatatype")],
                  ("dataType", cim + "Area")),                              # chain
        "Loop": (datatype, ("dataType", cim + "Loop")),
        "Text": (datatype, ("range", "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString")),
        "CurrentLimit": ([], ("dataType", cim + "Float")),                  # not a datatype
    }
    rows = []
    for name, (markers, target) in classes.items():
        rows += [(cim + name, key, value) for key, value in markers]
        rows += [(cim + name + ".value", "domain", cim + name), (cim + name + ".value", *target)]
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE"])
    assert xsd_types_from_rdfs(data) == {"Area": "xsd:float", "Money": "xsd:decimal", "Alias": "xsd:float"}
