"""Wheel-CI guard: the compiled extensions must import where a built wheel is
under test (TRIPLETS_REQUIRE_COMPILED=1, set by build-wheels.yml). Everywhere
else the compiled-engine tests skip when the extension is not built, so a
broken extension build would otherwise turn CI green by skipping."""
import importlib
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TRIPLETS_REQUIRE_COMPILED"),
                                reason="TRIPLETS_REQUIRE_COMPILED not set")


@pytest.mark.parametrize("module", ["triplets.parser.cython_pugixml_arrow",
                                    "triplets.export.cimxml_cython_pugixml"])
def test_compiled_module_imports(module):
    importlib.import_module(module)


def test_cimxml_extension_rejects_non_string_column():
    pyarrow = pytest.importorskip("pyarrow")
    ext = pytest.importorskip("triplets.export.cimxml_cython_pugixml")
    bad = pyarrow.record_batch({"ID": ["a"], "KEY": ["Type"], "VALUE": pyarrow.array([1.5])})
    with pytest.raises(RuntimeError, match="column 'VALUE' must be a .*string column"):
        ext.generate_xml_from_arrow(bad, {}, {}, {}, "f.xml")
