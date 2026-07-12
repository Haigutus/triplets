"""Tests for read_nquads — the inverse of export_to_nquads (triplets.parser.nquads)."""
import io

import pandas
import pytest

import triplets
from triplets.export import export_to_nquads
from triplets.parser.nquads import read_nquads

CIM = "http://iec.ch/TC57/CIM100#"

FRAME = pandas.DataFrame(
    [
        ("11111111-2222-3333-4444-555555555555", "Type", "ACLineSegment", "g1"),
        ("11111111-2222-3333-4444-555555555555", "IdentifiedObject.name", "Line 1", "g1"),
        ("11111111-2222-3333-4444-555555555555", "Conductor.length", "12.5", "g1"),
        # a UUID-shaped VALUE exports as a reference IRI and must round-trip
        ("11111111-2222-3333-4444-555555555555", "Equipment.EquipmentContainer",
         "99999999-8888-7777-6666-555555555555", "g1"),
        # escaping round-trip: quote, backslash, newline
        ("aaaaaaaa-2222-3333-4444-555555555555", "IdentifiedObject.description",
         'say "hi" \\ twice\nline two', "g2"),
    ],
    columns=["ID", "KEY", "VALUE", "INSTANCE_ID"],
)


def roundtrip(frame, **kwargs):
    buffer = export_to_nquads(frame, export_to_memory=True, **kwargs)
    buffer.seek(0)
    return read_nquads(buffer)


def canon(frame):
    return frame.astype("object").sort_values(["ID", "KEY", "VALUE"]).reset_index(drop=True)


def test_roundtrip_recovers_triplets():
    result = roundtrip(FRAME)
    pandas.testing.assert_frame_equal(canon(result), canon(FRAME))


def test_roundtrip_with_rdf_map_drops_datatype_to_lexical():
    """Datatype annotations from the export schema drop back to lexical form
    (the triplets convention: everything is a string)."""
    rdf_map = {"Profile": {
        "Conductor.length": {"xsd:type": "xsd:float"},
        "IdentifiedObject.name": {"xsd:type": "xsd:string"},
    }}
    result = roundtrip(FRAME, rdf_map=rdf_map)
    pandas.testing.assert_frame_equal(canon(result), canon(FRAME))


def test_source_bytes_str_filelike_and_path(tmp_path):
    quad = f'<urn:uuid:a> <{CIM}IdentifiedObject.name> "n" <urn:uuid:g> .\n'
    path = tmp_path / "data.nq"
    path.write_text(quad)

    for source in (quad, quad.encode(), io.BytesIO(quad.encode()), str(path), path):
        result = read_nquads(source)
        assert result.iloc[0].tolist() == ["a", "IdentifiedObject.name", "n", "g"]


def test_ntriples_without_graph_gets_null_instance_id():
    result = read_nquads(f'<urn:uuid:a> <{CIM}IdentifiedObject.name> "n" .')
    assert result["INSTANCE_ID"].isna().all()
    assert result.iloc[0]["VALUE"] == "n"


def test_language_tag_and_datatype_suffix_dropped():
    content = (f'<urn:uuid:a> <{CIM}IdentifiedObject.name> "namn"@sv <urn:uuid:g> .\n'
               f'<urn:uuid:a> <{CIM}Conductor.length> '
               f'"2.5"^^<http://www.w3.org/2001/XMLSchema#float> <urn:uuid:g> .')
    result = read_nquads(content)
    assert result["VALUE"].tolist() == ["namn", "2.5"]


def test_unicode_escapes_decoded():
    result = read_nquads('<urn:uuid:a> <urn:p> "caf\\u00E9 \\U0001F600" .')
    assert result.iloc[0]["VALUE"] == "café 😀"


def test_rdf_type_becomes_type_key():
    result = read_nquads("<urn:uuid:a> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
                         f"<{CIM}Terminal> <urn:uuid:g> .")
    assert result.iloc[0].tolist() == ["a", "Type", "Terminal", "g"]


def test_bnodes_and_full_iris_pass_through():
    result = read_nquads('_:b0 <http://example.com/p> <http://example.com/o> .')
    assert result.iloc[0].tolist()[:3] == ["b0", "http://example.com/p", "http://example.com/o"]


def test_blank_lines_and_comments_skipped():
    content = ('\n# a comment\n'
               '<urn:uuid:a> <urn:p> "n" <urn:uuid:g> .\n\n')
    assert len(read_nquads(content)) == 1


def test_malformed_line_raises():
    with pytest.raises(ValueError, match="not N-Quads"):
        read_nquads('<urn:uuid:a> <urn:p> .')


def test_return_types():
    quad = '<urn:uuid:a> <urn:p> "n" <urn:uuid:g> .'
    polars = pytest.importorskip("polars")
    assert isinstance(read_nquads(quad, return_type="polars"), polars.DataFrame)
    pyarrow = pytest.importorskip("pyarrow")
    assert isinstance(read_nquads(quad, return_type="arrow"), pyarrow.Table)
    assert isinstance(pandas.read_nquads(quad), pandas.DataFrame)      # registered reader
    assert isinstance(polars.read_nquads(quad), polars.DataFrame)


def test_top_level_export():
    assert triplets.read_nquads is read_nquads
