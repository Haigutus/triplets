"""Arrow-ingest tests for the embedded qlever engine.

The index is built straight from Arrow columns through an injected parser
(_qlever_arrow_parser.cpp). The parity target is the N-Quads text path: the
same data indexed via export_to_nquads + build_index must answer the full
quad dump identically to build_index_from_arrow (same engine, same decode —
a pure ingest differential). A torture frame exercises every term-mapping
rule; flavor tests run the same data through polars and duckdb encodings
(large_utf8, dictionary, chunk offsets).
"""
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("triplets.sparql._qlever", reason="qlever extension not built (setup_qlever.py)")

import pandas
import triplets
from triplets.sparql import _qlever
from triplets.export.nquads_utils import CIM_NS

from _parity import SVEDALA_DIR

UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
INSTANCE_1 = "99999999-0000-1111-2222-333333333333"
INSTANCE_2 = "http://example.com/instances/profile2"

# Inline export schema exercising every schema-driven rule.
RDF_MAP = {"Profile": {
    "Test.float":    {"xsd:type": "xsd:float"},
    "Test.integer":  {"xsd:type": "xsd:integer"},
    "Test.boolean":  {"xsd:type": "xsd:boolean"},
    "Test.dateTime": {"xsd:type": "xsd:dateTime"},
    "Test.string":   {"xsd:type": "xsd:string"},
    "Test.anyURI":   {"xsd:type": "xsd:anyURI"},        # excluded → IRI handling
    "Test.enum":     {"type": "Enumeration"},
    "Test.other":    {"namespace": "http://example.com/ns#"},
}}


def torture_frame():
    """One row per term-mapping rule (see _qlever_arrow_parser.cpp)."""
    rows = [
        # S1/G1: UUID and URI subjects/graphs; P1/O1/O2: Type objects
        (UUID_A, "Type", "ACLineSegment", INSTANCE_1),                      # bare class → CIM_NS
        ("http://example.com/thing", "Type", "http://example.com/Class", INSTANCE_2),
        (UUID_B, "Type", f"{CIM_NS}Terminal", INSTANCE_1),                  # http class as-is
        ("urn:example:id1", "Type", "urn:example:Class", INSTANCE_1),       # urn passthrough
        # P2: full-URI KEY
        (UUID_A, "http://example.com/ns#pred", "plain", INSTANCE_1),
        # P3 + schema namespace
        (UUID_A, "Test.other", "otherval", INSTANCE_1),
        # O3: URI VALUE under unmapped KEY
        (UUID_A, "SomeRef", "https://example.com/target", INSTANCE_1),
        # O4: enum
        (UUID_A, "Test.enum", "UnitSymbol.A", INSTANCE_1),
        # O5: typed literals (incl. one that LOOKS like a UUID — schema beats heuristic)
        (UUID_A, "Test.float", "1.5", INSTANCE_1),
        (UUID_A, "Test.integer", "42", INSTANCE_1),
        (UUID_A, "Test.boolean", "true", INSTANCE_1),
        (UUID_A, "Test.dateTime", "2020-01-02T03:04:05", INSTANCE_1),
        (UUID_A, "Test.string", UUID_B, INSTANCE_1),
        # O6: anyURI excluded from datatypes → UUID/IRI handling applies
        (UUID_A, "Test.anyURI", UUID_B, INSTANCE_1),
        # O7: UUID reference under unmapped KEY (lowercase only)
        (UUID_A, "RefKey", UUID_B, INSTANCE_1),
        (UUID_A, "NotRef", UUID_B.upper(), INSTANCE_1),                     # stays literal
        # O8/E1: plain literals with characters the text path escapes (or broke on)
        (UUID_A, "IdentifiedObject.name", 'q"uo\\te\nnew', INSTANCE_1),
        (UUID_A, "Desc", "tab\there", INSTANCE_1),
        (UUID_A, "Unicode", "õäöü€", INSTANCE_2),
        (UUID_A, "Empty", "", INSTANCE_1),
        # N1: null VALUE row must be dropped
        (UUID_A, "Dropped", None, INSTANCE_1),
    ]
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


DUMP = "SELECT ?g ?s ?p ?o WHERE { GRAPH ?g { ?s ?p ?o } }"


def quad_dump(index):
    frame = index.select_arrow(DUMP).to_pandas()
    return frame.sort_values(["g", "s", "p", "o"]).reset_index(drop=True)


def build_via_nquads(data, rdf_map, directory):
    buffer = data.export_to_nquads(rdf_map=rdf_map, export_to_memory=True)
    source = Path(directory, "data.nq")
    source.write_bytes(buffer.read())
    basename = str(Path(directory, "nq", "index"))
    Path(directory, "nq").mkdir()
    _qlever.build_index(str(source), basename)
    return _qlever.QleverIndex(basename)


def build_via_arrow(data, rdf_map, directory):
    from triplets.sparql.sparql_qlever import _build_index
    basename = str(Path(directory, "arrow", "index"))
    Path(directory, "arrow").mkdir()
    _build_index(data.export_to_arrow(), rdf_map, basename)
    return _qlever.QleverIndex(basename)


@pytest.mark.parametrize("rdf_map", [None, RDF_MAP], ids=["schema-less", "typed"])
def test_arrow_ingest_matches_nquads_ingest(rdf_map):
    """The full quad dump is identical whichever way the index was fed.

    Torture rows with characters beyond the text path's escape set (tab) are
    excluded here — the text path emits broken N-Quads for them (the arrow
    path handles them correctly; see test_characters_beyond_the_escape_set).
    """
    data = torture_frame()
    data = data[data["KEY"] != "Desc"].reset_index(drop=True)
    with tempfile.TemporaryDirectory() as directory:
        via_nquads = quad_dump(build_via_nquads(data, rdf_map, directory))
        via_arrow = quad_dump(build_via_arrow(data, rdf_map, directory))
    pandas.testing.assert_frame_equal(via_arrow, via_nquads)
    assert len(via_arrow) == len(data) - 1        # the null-VALUE row is dropped


def test_characters_beyond_the_escape_set():
    """Literals with \\t or \\r broke the text path (its escape set is only
    backslash, quote, newline) — the arrow path ingests them losslessly."""
    data = pandas.DataFrame({
        "ID": [UUID_A], "KEY": ["Desc"], "VALUE": ["tab\there\rreturn"],
        "INSTANCE_ID": [INSTANCE_1]})
    with tempfile.TemporaryDirectory() as directory:
        index = build_via_arrow(data, None, directory)
        result = index.select_arrow("SELECT ?o WHERE { ?s ?p ?o }").to_pandas()
    assert result["o"].iloc[0] == "tab\there\rreturn"


def test_null_in_required_column_raises():
    """Null VALUE rows are dropped (exporter parity); null anywhere else has
    no defined export — fail loud with the row index."""
    data = pandas.DataFrame({
        "ID": [UUID_A, None], "KEY": ["a", "b"], "VALUE": ["1", "2"],
        "INSTANCE_ID": [INSTANCE_1, INSTANCE_1]})
    with tempfile.TemporaryDirectory() as directory:
        with pytest.raises(RuntimeError, match="null ID/KEY/INSTANCE_ID at row 1"):
            build_via_arrow(data, None, directory)


def test_flavors_reach_the_same_graph():
    """The same data through pandas (utf8 / arrow-backed), polars
    (large_utf8, categorical→dictionary) and duckdb (its native arrow
    encoding) produces identical query results — exercises every column
    encoding the C++ accessors support."""
    q = "SELECT (COUNT(?s) AS ?n) WHERE { ?s ?p ?o }"
    data = torture_frame()
    reference = triplets.sparql.query(data, q, engine="qlever")["n"].iloc[0]

    polars = pytest.importorskip("polars")
    as_polars = polars.from_pandas(data).with_columns(
        polars.col("KEY").cast(polars.Categorical),
        polars.col("INSTANCE_ID").cast(polars.Categorical))
    assert triplets.sparql.query(as_polars, q, engine="qlever")["n"][0] == reference

    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect()
    connection.register("src", data)
    connection.execute("CREATE TABLE triplets AS SELECT * FROM src")
    connection.unregister("src")
    assert triplets.sparql.query(connection, q, engine="qlever")["n"].iloc[0] == reference


def test_chunked_batches():
    """Multi-chunk tables reach the builder as sliced batches (nonzero array
    offsets) — the accessors must be offset-aware."""
    import pyarrow
    data = torture_frame().dropna(subset=["VALUE"]).reset_index(drop=True)
    table = pyarrow.Table.from_pandas(data, preserve_index=False)
    chunked = pyarrow.concat_tables([table.slice(0, 7), table.slice(7)])
    with tempfile.TemporaryDirectory() as directory:
        from triplets.sparql.sparql_qlever import _build_index
        basename = str(Path(directory, "index"))
        _build_index(chunked, None, basename)
        count = _qlever.QleverIndex(basename).select_arrow(
            "SELECT (COUNT(?s) AS ?n) WHERE { ?s ?p ?o }").to_pandas()
    assert int(count["n"].iloc[0]) == len(data)


def test_export_to_arrow():
    """The columnar interchange: fixed column order, dictionary/categorical
    passthrough, clear failure on non-triplet input."""
    import pyarrow
    data = torture_frame()
    table = data.export_to_arrow()
    assert table.column_names == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    assert table.num_rows == len(data)

    polars = pytest.importorskip("polars")
    as_polars = polars.from_pandas(data).with_columns(polars.col("KEY").cast(polars.Categorical))
    table = as_polars.export_to_arrow()
    assert pyarrow.types.is_dictionary(table.schema.field("KEY").type)

    with pytest.raises(ValueError, match="missing columns"):
        pandas.DataFrame({"ID": ["x"]}).export_to_arrow()


def test_svedala_parity_smoke():
    """Realistic CGMES frame (dictionary<int32,string> KEY/INSTANCE_ID from
    read_RDF) — arrow ingest answers identically to the nquads text path."""
    if not SVEDALA_DIR.exists():
        pytest.skip("Svedala test data not available (needs git submodule)")
    data = pandas.read_RDF([str(p) for p in sorted(SVEDALA_DIR.glob("*.xml"))])
    q = "SELECT (COUNT(?s) AS ?n) WHERE { ?s ?p ?o }"
    with tempfile.TemporaryDirectory() as directory:
        via_nquads = quad_dump(build_via_nquads(data, None, directory))
        via_arrow = quad_dump(build_via_arrow(data, None, directory))
    pandas.testing.assert_frame_equal(via_arrow, via_nquads)
