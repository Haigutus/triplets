"""Shared fixtures for triplets tests."""
import pytest
import pandas
import triplets
from pathlib import Path

BASE_PATH = Path(__file__).parent.parent.absolute()
INSTANCE_PATH = BASE_PATH / "test_data/relicapgrid/Instance"
PROFILES_PATH = BASE_PATH / "test_data/entsoe-profiles"

# Small NC files for fast tests
NC_FILES = {
    "AE": INSTANCE_PATH / "Belgovia/NetworkCode/cimxml/Belgovia_AE.xml",
    "CO": INSTANCE_PATH / "Belgovia/NetworkCode/cimxml/Belgovia_CO.xml",
    "OR": INSTANCE_PATH / "Espheim/NetworkCode/cimxml/Espheim_OR.xml",
}

# CGMES files
CGMES_FILES = {
    "EQ": INSTANCE_PATH / "Belgovia/Grid/cimxml/20220615T2230Z__Belgovia_EQ_1.xml",
    "SSH": INSTANCE_PATH / "Belgovia/Grid/cimxml/20220615T2230Z_2D_Belgovia_SSH_1.xml",
    "TP": INSTANCE_PATH / "Belgovia/Grid/cimxml/20220615T2230Z_2D_Belgovia_TP_1.xml",
    "SV": INSTANCE_PATH / "Belgovia/Grid/cimxml/20220615T2230Z_2D_Belgovia_SV_1.xml",
}

EXPORT_SCHEMAS = {
    "nc": str(BASE_PATH / "triplets/export_schema/ENTSOE_NC_552_ED1.json"),
    "cgmes": str(BASE_PATH / "triplets/export_schema/ENTSOE_CGMES_3.0.0_552_ED2.json"),
}

REALGRID_ZIP = str(BASE_PATH / "test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip")


@pytest.fixture(params=list(NC_FILES.keys()), ids=lambda k: f"NC-{k}")
def nc_file(request):
    """Parametrized fixture yielding (key, path) for each NC file."""
    key = request.param
    path = NC_FILES[key]
    if not path.exists():
        pytest.skip(f"Test data not available: {path}")
    return key, path


@pytest.fixture(params=list(CGMES_FILES.keys()), ids=lambda k: f"CGMES-{k}")
def cgmes_file(request):
    """Parametrized fixture yielding (key, path) for each CGMES file."""
    key = request.param
    path = CGMES_FILES[key]
    if not path.exists():
        pytest.skip(f"Test data not available: {path}")
    return key, path


@pytest.fixture
def nc_data(nc_file):
    """Load NC file into DataFrame."""
    key, path = nc_file
    return key, pandas.read_RDF([str(path)])


@pytest.fixture
def cgmes_data(cgmes_file):
    """Load CGMES file into DataFrame."""
    key, path = cgmes_file
    return key, pandas.read_RDF([str(path)])


@pytest.fixture(scope="session")
def realgrid_data():
    """Load RealGrid zip (session-scoped, loaded once)."""
    if not Path(REALGRID_ZIP).exists():
        pytest.skip("RealGrid test data not available")
    return pandas.read_RDF([REALGRID_ZIP])


# --- Engine detection for parser tests ---

def _has_pyarrow():
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def _has_cython_pugixml_arrow():
    try:
        from triplets.parser import cython_pugixml_arrow  # noqa: F401
        return True
    except Exception:
        return False


HAS_PYARROW = _has_pyarrow()
HAS_CYTHON_PUGIXML_ARROW = _has_cython_pugixml_arrow()

_engine_params = ["python_lxml_pandas"]
if HAS_PYARROW:
    _engine_params.append("python_lxml_arrow")
if HAS_CYTHON_PUGIXML_ARROW:
    _engine_params.append("cython_pugixml_arrow")


@pytest.fixture
def minimal_cim(tmp_path):
    """Provide path to committed minimal_cim.xml (self-contained parser test data)."""
    src = Path(__file__).parent / "data" / "minimal_cim.xml"
    if src.exists():
        return str(src)
    # fallback: write a tiny one
    p = tmp_path / "min.xml"
    p.write_text('''<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://example">
  <cim:Substation rdf:ID="S1"><cim:IdentifiedObject.name>Sub</cim:IdentifiedObject.name></cim:Substation>
</rdf:RDF>''')
    return str(p)


@pytest.fixture(params=_engine_params)
def parser_engine(request):
    return request.param
