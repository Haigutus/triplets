"""Tests for RDF/XML export (export_to_cimxml)."""
import pandas
import triplets
from lxml import etree
from triplets.rdf_parser import ExportType
from tests.conftest import EXPORT_SCHEMAS


class TestExportNC:
    """Export tests for NC files."""

    def test_export_produces_valid_xml(self, nc_data):
        key, data = nc_data
        exported = data.export_to_cimxml(
            rdf_map=EXPORT_SCHEMAS["nc"],
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        assert len(exported) > 0
        for buf in exported:
            root = etree.fromstring(buf.getvalue())
            assert root.tag.endswith("RDF")

    def test_no_distribution_in_export(self, nc_data):
        key, data = nc_data
        exported = data.export_to_cimxml(
            rdf_map=EXPORT_SCHEMAS["nc"],
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        for buf in exported:
            xml = buf.getvalue().decode("utf-8")
            assert "<Distribution " not in xml
            assert "<NamespaceMap " not in xml

    def test_has_namespace_prefixes(self, nc_data):
        key, data = nc_data
        exported = data.export_to_cimxml(
            rdf_map=EXPORT_SCHEMAS["nc"],
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        xml = exported[0].getvalue().decode("utf-8")
        assert "xmlns:nc=" in xml or "xmlns:cim=" in xml

    def test_conforms_to_uses_rdf_resource(self, nc_data):
        key, data = nc_data
        exported = data.export_to_cimxml(
            rdf_map=EXPORT_SCHEMAS["nc"],
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        xml = exported[0].getvalue().decode("utf-8")
        if "conformsTo" in xml:
            assert 'conformsTo rdf:resource=' in xml

    def test_roundtrip_preserves_types(self, nc_data):
        key, data = nc_data
        original_types = {k for k in data.types_dict() if k not in ("Distribution", "NamespaceMap", "")}

        exported = data.export_to_cimxml(
            rdf_map=EXPORT_SCHEMAS["nc"],
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        # Write to temp file and re-import
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
            f.write(exported[0].getvalue())
            tmp_path = f.name
        try:
            reimported = pandas.read_RDF([tmp_path])
            reimported_types = {k for k in reimported.types_dict() if k not in ("Distribution", "NamespaceMap", "")}
            assert original_types == reimported_types, f"Types changed: lost={original_types - reimported_types}, gained={reimported_types - original_types}"
        finally:
            os.unlink(tmp_path)


class TestExportCGMES:
    """Export tests for CGMES files."""

    def test_export_produces_valid_xml(self, cgmes_data):
        key, data = cgmes_data
        exported = data.export_to_cimxml(
            rdf_map=EXPORT_SCHEMAS["cgmes"],
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        assert len(exported) > 0
        for buf in exported:
            root = etree.fromstring(buf.getvalue())
            assert root.tag.endswith("RDF")

    def test_no_distribution_in_export(self, cgmes_data):
        key, data = cgmes_data
        exported = data.export_to_cimxml(
            rdf_map=EXPORT_SCHEMAS["cgmes"],
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        for buf in exported:
            xml = buf.getvalue().decode("utf-8")
            assert "<Distribution " not in xml
            assert "<NamespaceMap " not in xml
