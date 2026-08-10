"""pathlib.Path inputs across the parse and export boundaries (issue #75).

Every public entry point coerces os.PathLike once; the engines receive str
paths (keeping the cython mmap fast path) or file-like objects.
"""
import io
import zipfile

from pathlib import Path

import pandas
import pytest

import triplets
from triplets.export import export_to_csv, export_to_excel, export_to_nquads

from _parity import SVEDALA_DIR, SVEDALA_FILES, SKIP_REASON

pytestmark = pytest.mark.skipif(not SVEDALA_DIR.exists(), reason=SKIP_REASON)

XML = SVEDALA_FILES[0]   # one instance file, str path


def rows(frame):
    """Comparable content: parse-generated INSTANCE_IDs/meta ids differ per run."""
    data = frame[~frame["VALUE"].astype(str).isin(["Distribution", "NamespaceMap"])]
    return len(data)


@pytest.fixture(scope="module")
def reference():
    return triplets.parse(XML)


@pytest.mark.parametrize("form", ["path", "bare_path", "path_list", "file_like"])
def test_parse_input_forms(reference, form):
    source = {
        "path": lambda: Path(XML),
        "bare_path": lambda: Path(XML),
        "path_list": lambda: [Path(XML)],
        "file_like": lambda: open(XML, "rb"),
    }[form]()
    try:
        result = triplets.parse(source)
    finally:
        if hasattr(source, "close"):
            source.close()
    assert rows(result) == rows(reference) > 0


def test_parse_zip_path(reference, tmp_path):
    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(XML, arcname=Path(XML).name)
    result = triplets.parse(archive)                 # bare Path to a .zip
    assert rows(result) == rows(reference)


@pytest.mark.parametrize("engine", ["python_lxml_pandas", "python_lxml_arrow", "cython_pugixml_arrow"])
def test_engines_accept_path_directly(reference, engine):
    """Defensive coercion inside each engine (bypassing find_all_xml)."""
    try:
        _, module = triplets.parser.get_engine(engine)
    except ImportError as error:                     # compiled engine not built
        pytest.skip(str(error))
    result = module.load_rdf_to_dataframe(Path(XML))
    length = result.num_rows if hasattr(result, "num_rows") else len(result)
    assert length > 0


EXPORT_FRAME = pandas.DataFrame(
    [
        ("11111111-2222-3333-4444-555555555555", "Type", "ACLineSegment", "g1"),
        ("11111111-2222-3333-4444-555555555555", "IdentifiedObject.name", "Line 1", "g1"),
        ("dddddddd-2222-3333-4444-555555555555", "Type", "Distribution", "g1"),
        ("dddddddd-2222-3333-4444-555555555555", "label", "model.xml", "g1"),
    ],
    columns=["ID", "KEY", "VALUE", "INSTANCE_ID"],
)


def test_export_excel_path(tmp_path):
    pytest.importorskip("openpyxl")
    target = tmp_path / "out.xlsx"                   # Path where .endswith used to crash
    export_to_excel(EXPORT_FRAME, path=target, single_file=True)
    assert target.exists()
    export_to_excel(EXPORT_FRAME, path=tmp_path)     # directory as Path (per-instance mode)
    assert (tmp_path / "model.xlsx").exists()


def test_export_csv_path(tmp_path):
    export_to_csv(EXPORT_FRAME, path=tmp_path)
    assert list(tmp_path.glob("*.csv"))


def test_export_nquads_path_and_default(tmp_path, monkeypatch):
    target = tmp_path / "out.nq"
    export_to_nquads(EXPORT_FRAME, path=target)
    assert target.exists()
    monkeypatch.chdir(tmp_path)
    export_to_nquads(EXPORT_FRAME)                   # path=None → export.nq in cwd
    assert (tmp_path / "export.nq").exists()


def test_export_nquads_memory_unchanged():
    buffer = export_to_nquads(EXPORT_FRAME, export_to_memory=True)
    assert isinstance(buffer, io.BytesIO) and buffer.name.endswith(".nq")
