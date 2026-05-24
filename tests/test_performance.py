"""Performance tests using RealGrid data."""
import time
import pytest
import pandas
import triplets
from triplets.rdf_parser import ExportType
from tests.conftest import REALGRID_ZIP, EXPORT_SCHEMAS


@pytest.mark.performance
class TestPerformanceImport:
    """Import performance tests."""

    def test_import_realgrid(self, realgrid_data):
        assert len(realgrid_data) > 1_000_000, f"Expected >1M rows, got {len(realgrid_data)}"

    def test_import_realgrid_time(self):
        start = time.time()
        data = pandas.read_RDF([REALGRID_ZIP])
        elapsed = time.time() - start
        print(f"\nRealGrid import: {len(data)} rows in {elapsed:.2f}s")
        assert elapsed < 60, f"Import took {elapsed:.2f}s, expected <60s"


@pytest.mark.performance
class TestPerformanceTableview:
    """type_tableview performance tests."""

    def test_tableview_large_type(self, realgrid_data):
        types = realgrid_data.types_dict()
        # Find the largest type
        largest = max((k for k in types if k not in ("Distribution", "NamespaceMap", "FullModel", "")),
                      key=lambda k: types[k])
        start = time.time()
        tv = realgrid_data.type_tableview(largest)
        elapsed = time.time() - start
        print(f"\ntype_tableview({largest}): {len(tv)} rows in {elapsed:.2f}s (types_dict: {types[largest]})")
        assert len(tv) > 0
        assert elapsed < 30, f"Tableview took {elapsed:.2f}s, expected <30s"


@pytest.mark.performance
class TestPerformanceExport:
    """Export performance tests."""

    def test_export_realgrid(self, realgrid_data):
        start = time.time()
        exported = realgrid_data.export_to_cimxml(
            rdf_map=EXPORT_SCHEMAS["cgmes"],
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        elapsed = time.time() - start
        total_bytes = sum(len(buf.getvalue()) for buf in exported)
        print(f"\nRealGrid export: {len(exported)} files, {total_bytes / 1024 / 1024:.1f}MB in {elapsed:.2f}s")
        assert len(exported) > 0
        assert elapsed < 120, f"Export took {elapsed:.2f}s, expected <120s"
