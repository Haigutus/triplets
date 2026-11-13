"""Parser tests — API, engines, parity, registration, DuckDB load.

Uses minimal_cim.xml (committed) + RealGrid when present.
"""
import pytest
import pandas

import triplets
from triplets.parser import parse, find_all_xml, local_id, read_rdf

from pathlib import Path

_TEST_DIR = Path(__file__).parent
_PROJECT_DIR = _TEST_DIR.parent

REALGRID_ZIP = str(_PROJECT_DIR / "test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip")
MINIMAL = str(_TEST_DIR / "data" / "minimal_cim.xml")

try:
    import pyarrow  # noqa: F401
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

try:
    from triplets.parser import cython_pugixml_arrow  # noqa: F401
    HAS_CYTHON_PUGIXML_ARROW = True
except Exception:
    HAS_CYTHON_PUGIXML_ARROW = False


# ── Utilities ───────────────────────────────────────────────────────────────

def test_local_id():
    assert local_id("urn:uuid:abc-123") == "abc-123"
    assert local_id("#_foo_bar") == "foo_bar"
    assert local_id("_123") == "123"
    assert local_id("urn:uuid:_x") == "_x"      # one prefix, like the cython engine
    assert local_id(None) is None
    assert local_id("") == ""


def test_find_all_xml_minimal():
    files = find_all_xml([MINIMAL])
    assert len(files) == 1


# ── Registration ────────────────────────────────────────────────────────────

class TestRegistration:
    def test_pandas_read_RDF_registered(self):
        assert hasattr(pandas, "read_RDF")
        assert hasattr(pandas, "read_rdf")
        assert callable(pandas.read_RDF)

    def test_polars_read_rdf_registered(self):
        polars = pytest.importorskip("polars")
        assert hasattr(polars, "read_rdf")
        assert callable(polars.read_rdf)


# ── Parse API (parametrized across engines) ─────────────────────────────────

def test_parse_minimal_basic(parser_engine):
    df = parse(MINIMAL, engine=parser_engine)
    assert list(df.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    assert len(df) > 0
    assert "Distribution" in df["VALUE"].values
    assert "NamespaceMap" in df["VALUE"].values


def test_nodeid_support(parser_engine):
    df = parse(MINIMAL, engine=parser_engine)
    assert any("ConnectivityNode" in v for v in df[df["KEY"] == "Type"]["VALUE"])


@pytest.mark.parametrize("use_cat", [True, False])
def test_categorical_encoding(parser_engine, use_cat):
    cols = ("INSTANCE_ID", "KEY") if use_cat else None
    df = parse(MINIMAL, engine=parser_engine, return_type="pandas", categorical_columns=cols)
    if use_cat:
        assert df["KEY"].nunique() < 20


# ── Return types ────────────────────────────────────────────────────────────

def test_parse_returns_arrow_and_polars():
    pa = pytest.importorskip("pyarrow")
    table = parse(MINIMAL, return_type="arrow")
    assert isinstance(table, pa.Table)
    assert table.num_rows > 0

    polars = pytest.importorskip("polars")
    pdf = parse(MINIMAL, return_type="polars")
    assert len(pdf) > 0


# ── parse_batches: lazy RecordBatchReader for out-of-core ingest ─────────────

def test_parse_batches_matches_parse():
    pa = pytest.importorskip("pyarrow")
    reader = triplets.parser.parse_batches(MINIMAL)
    table = reader.read_all()
    assert table.schema == pa.schema([(c, pa.string())
                                      for c in ("ID", "KEY", "VALUE", "INSTANCE_ID")])
    assert table.num_rows == len(parse(MINIMAL))


def test_parse_batches_zip_one_batch_per_file(tmp_path):
    pytest.importorskip("pyarrow")
    import zipfile
    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(MINIMAL, "a.xml")
        bundle.write(MINIMAL, "b.xml")
    batches = list(triplets.parser.parse_batches(archive))
    assert len(batches) == 2
    assert sum(b.num_rows for b in batches) == 2 * (len(parse(MINIMAL)) + 1)   # + each member's belongsToContainer row


def test_parse_batches_empty_and_errors():
    pa = pytest.importorskip("pyarrow")
    empty = triplets.parser.parse_batches([]).read_all()
    assert empty.num_rows == 0
    assert empty.schema.names == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    with pytest.raises(ValueError, match="arrow parser engine"):
        triplets.parser.parse_batches(MINIMAL, engine="python_lxml_pandas")


@pytest.mark.parametrize("cols,engine", [
    (("INSTANCE_ID", "KEY"), "auto"),
    # the cython engine dictionary-encodes KEY/INSTANCE_ID structurally, so the
    # no-categoricals (all plain string) contract only holds for the lxml engine
    (None, "python_lxml_arrow"),
])
def test_empty_parse_schema_matches_nonempty(cols, engine):
    """An empty parse carries the same arrow/polars schema as a non-empty one."""
    pa = pytest.importorskip("pyarrow")
    empty = parse([], return_type="arrow", categorical_columns=cols)
    full = parse(MINIMAL, return_type="arrow", categorical_columns=cols, engine=engine)
    assert empty.schema == full.schema
    assert pa.concat_tables([empty, full]).num_rows == full.num_rows

    polars = pytest.importorskip("polars")
    empty_pl = parse([], return_type="polars", categorical_columns=cols)
    full_pl = parse(MINIMAL, return_type="polars", categorical_columns=cols, engine=engine)
    assert empty_pl.schema == full_pl.schema
    assert len(polars.concat([empty_pl, full_pl])) == len(full_pl)


# ── Distribution rows: file name (fileName), the zip it came from (belongsToContainer)

def _distributions(data):
    """label → belongsToContainer (None without that row) of every Distribution object, and
    the (KEY, VALUE) rows of each one in row order."""
    data = data.astype({"ID": str, "KEY": str, "VALUE": str})
    ids = data.loc[(data["KEY"] == "Type") & (data["VALUE"] == "Distribution"), "ID"]
    rows = data[data["ID"].isin(ids)]
    objects = [list(zip(group["KEY"], group["VALUE"])) for _, group in rows.groupby("ID", sort=False)]
    return {dict(o)["label"]: dict(o).get("belongsToContainer") for o in objects}, objects


def _zip(path, members):
    """Write a zip of {member name: bytes}; returns the path as str."""
    import zipfile
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in members.items():
            bundle.writestr(name, content)
    return str(path)


XML = Path(MINIMAL).read_bytes()


def test_container_zip_path(tmp_path, parser_engine):
    """A zip given as a path: each member gets belongsToContainer = the path as given; label stays the member name."""
    one = _zip(tmp_path / "one.zip", {"a.xml": XML})
    labels, objects = _distributions(parse(one, engine=parser_engine))
    assert labels == {"a.xml": one}
    assert objects == [[("Type", "Distribution"), ("label", "a.xml"), ("fileName", "a.xml"), ("belongsToContainer", one)]]

    many = _zip(tmp_path / "many.zip", {"a.xml": XML, "b.xml": XML, "c.rdf": XML})
    assert _distributions(parse(Path(many), engine=parser_engine))[0] == {"a.xml": many, "b.xml": many, "c.rdf": many}


def test_container_zip_file_object(tmp_path, parser_engine):
    """A zip given as a file object: belongsToContainer = its .name."""
    path = _zip(tmp_path / "model.zip", {"a.xml": XML, "b.xml": XML})
    with open(path, "rb") as handle:
        assert _distributions(parse(handle, engine=parser_engine))[0] == {"a.xml": path, "b.xml": path}
    from io import BytesIO
    memory = BytesIO(Path(path).read_bytes())
    memory.name = "in_memory.zip"
    assert _distributions(parse(memory, engine=parser_engine))[0] == {"a.xml": "in_memory.zip", "b.xml": "in_memory.zip"}


def test_container_zip_in_zip(tmp_path, parser_engine):
    """A zip inside a zip: its members get the inner zip's member name, the outer members the outer zip."""
    inner = Path(_zip(tmp_path / "inner.zip", {"a.xml": XML})).read_bytes()
    outer = _zip(tmp_path / "outer.zip", {"b.xml": XML, "nested/inner.zip": inner})
    assert _distributions(parse(outer, engine=parser_engine))[0] == {"b.xml": outer, "a.xml": "nested/inner.zip"}


def test_container_plain_xml(parser_engine):
    """A file that was not zipped has no belongsToContainer row."""
    labels, objects = _distributions(parse([MINIMAL, Path(MINIMAL)], engine=parser_engine))
    assert labels == {MINIMAL: None}
    assert all(key != "belongsToContainer" for rows in objects for key, _ in rows)


def test_container_find_all_xml_and_rdf_parser(tmp_path):
    """Zip members carry the zip next to their name; the old rdf_parser.load_RDF_to_list writes the same row."""
    from triplets import rdf_parser
    path = _zip(tmp_path / "model.zip", {"a.xml": XML})
    (member,) = find_all_xml(path)
    assert (member.name, member.container) == ("a.xml", path)
    rows = pandas.DataFrame(rdf_parser.load_RDF_to_list(member), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    assert _distributions(rows)[1] == [[("Type", "Distribution"), ("label", "a.xml"), ("fileName", "a.xml"),
                                        ("belongsToContainer", path)]]
    assert not hasattr(find_all_xml(MINIMAL)[0], "container")


@pytest.mark.parametrize("label, name", [
    ("/data/models/20220615T2230Z__Belgovia_EQ_1.xml", "20220615T2230Z__Belgovia_EQ_1.xml"),
    ("sub/dir/x.xml", "x.xml"),
    ("C:\\models\\x.xml", "x.xml"),
    ("models\\sub/x.xml", "x.xml"),
    ("x.xml", "x.xml"),
    ("", ""),
])
def test_base_name(label, name):
    """fileName rule: the part of the label after the last / or \\ (any platform)."""
    from triplets.parser.utils import base_name
    assert base_name(label) == name


def _file_names(data):
    """label → fileName (None without that row) of every Distribution object."""
    return {dict(o)["label"]: dict(o).get("fileName") for o in _distributions(data)[1]}


def test_file_name_paths(tmp_path, monkeypatch, parser_engine):
    """Every parsed file gets fileName = its label without directories: absolute, relative and bare paths."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "relative.xml").write_bytes(XML)
    (tmp_path / "bare.xml").write_bytes(XML)
    data = parse([MINIMAL, "sub/relative.xml", Path("bare.xml")], engine=parser_engine)
    assert _file_names(data) == {MINIMAL: "minimal_cim.xml", "sub/relative.xml": "relative.xml", "bare.xml": "bare.xml"}
    assert _distributions(data)[1][0] == [("Type", "Distribution"), ("label", MINIMAL), ("fileName", "minimal_cim.xml")]


def test_file_name_zip_members(tmp_path, parser_engine):
    """A zip member in a sub-directory and a member of a nested zip: fileName drops the member's directories."""
    inner = Path(_zip(tmp_path / "inner.zip", {"deep/a.xml": XML})).read_bytes()
    outer = _zip(tmp_path / "outer.zip", {"sub/dir/x.xml": XML, "nested/inner.zip": inner})
    objects = _distributions(parse(outer, engine=parser_engine))[1]
    assert objects == [
        [("Type", "Distribution"), ("label", "sub/dir/x.xml"), ("fileName", "x.xml"), ("belongsToContainer", outer)],
        [("Type", "Distribution"), ("label", "deep/a.xml"), ("fileName", "a.xml"), ("belongsToContainer", "nested/inner.zip")],
    ]


def test_file_name_file_objects(parser_engine):
    """A named file object: fileName from its .name (backslashes too); a nameless one gets no fileName row."""
    from io import BytesIO
    named = BytesIO(XML)
    named.name = "C:\\models\\windows.xml"
    assert _file_names(parse(named, engine=parser_engine)) == {"C:\\models\\windows.xml": "windows.xml"}
    nameless = triplets.parser.get_engine(parser_engine)[1].load_rdf_to_dataframe(BytesIO(XML))
    nameless = nameless.to_pandas() if hasattr(nameless, "to_pandas") else nameless
    (rows,) = _distributions(nameless)[1]
    assert [key for key, _ in rows] == ["Type", "label"]          # label: "" or "<file-like>" per engine, as before


# ── Per-engine tests ────────────────────────────────────────────────────────

class TestPythonLxmlPandas:
    def test_basic_load(self):
        df = parse(MINIMAL, engine="python_lxml_pandas")
        assert isinstance(df, pandas.DataFrame)
        assert len(df) > 0
        types = set(df[df["KEY"] == "Type"]["VALUE"])
        assert "Substation" in types


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow not installed")
class TestPythonLxmlArrow:
    def test_basic_load(self):
        df = parse(MINIMAL, engine="python_lxml_arrow")
        assert isinstance(df, pandas.DataFrame)
        assert len(df) > 0

    def test_return_arrow(self):
        import pyarrow as pa
        t = parse(MINIMAL, engine="python_lxml_arrow", return_type="arrow")
        assert isinstance(t, pa.Table)
        assert t.num_rows > 0


@pytest.mark.skipif(not HAS_CYTHON_PUGIXML_ARROW, reason="cython_pugixml_arrow not built")
class TestCythonPugixmlArrow:
    def test_basic_load(self):
        df = parse(MINIMAL, engine="cython_pugixml_arrow")
        assert isinstance(df, pandas.DataFrame)
        assert len(df) > 0


# ── Engine parity ───────────────────────────────────────────────────────────

class TestParity:
    def test_row_count_parity(self):
        engines = ["python_lxml_pandas"]
        if HAS_PYARROW:
            engines.append("python_lxml_arrow")
        if HAS_CYTHON_PUGIXML_ARROW:
            engines.append("cython_pugixml_arrow")

        results = {eng: parse(MINIMAL, engine=eng, categorical_columns=None) for eng in engines}
        counts = [len(df) for df in results.values()]
        assert all(c == counts[0] for c in counts), f"Row count mismatch: {dict(zip(results.keys(), counts))}"

    def test_content_parity(self):
        engines = ["python_lxml_pandas"]
        if HAS_PYARROW:
            engines.append("python_lxml_arrow")
        if HAS_CYTHON_PUGIXML_ARROW:
            engines.append("cython_pugixml_arrow")
        if len(engines) < 2:
            pytest.skip("Need at least 2 engines")

        results = {}
        for eng in engines:
            df = parse(MINIMAL, engine=eng, categorical_columns=None).astype(str)
            types = df[df["KEY"] == "Type"]
            meta_ids = set(types[types["VALUE"].isin(["Distribution", "NamespaceMap"])]["ID"])
            data_df = df[~df["ID"].isin(meta_ids)]
            results[eng] = data_df[["KEY", "VALUE"]].sort_values(["KEY", "VALUE"]).reset_index(drop=True).values.tolist()

        ref_name = engines[0]
        for eng in engines[1:]:
            assert results[eng] == results[ref_name], f"{eng} differs from {ref_name}"


# ── DuckDB load ─────────────────────────────────────────────────────────────

def test_duckdb_read_rdf():
    duckdb = pytest.importorskip("duckdb")
    import triplets
    data = duckdb.connect()
    rows = data.read_rdf([MINIMAL])
    assert rows == 17         # no xml_base row: the file declares none (the file location is never recorded)
    assert "Substation" in data.types_dict()


# ── RealGrid (large data, session scoped) ───────────────────────────────────

def test_realgrid_parse_and_meta(realgrid_data):
    assert len(realgrid_data) > 1_000_000
    assert "ACLineSegment" in set(realgrid_data[realgrid_data["KEY"] == "Type"]["VALUE"])
    assert "Distribution" in realgrid_data["VALUE"].values


def test_parse_batches_parallel_prefetch(tmp_path):
    """max_workers keeps batch order (in-order bounded prefetch) and row parity."""
    pytest.importorskip("pyarrow")
    import zipfile
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for i in range(6):
            bundle.write(MINIMAL, f"f{i}.xml")
    sequential = list(triplets.parser.parse_batches(archive))
    parallel = list(triplets.parser.parse_batches(archive, max_workers=3))
    assert len(parallel) == len(sequential) == 6
    assert [b.num_rows for b in parallel] == [b.num_rows for b in sequential]
