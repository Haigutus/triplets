"""The IRI contract case table — every flavor must match the scalar definition.

Scalar ``triplets.iri`` is the readable rule; this table is the contract. The
pandas and polars flavors are checked against it here; the cython parser and
the qlever C++ ingest are checked against it in the parse / export parity
harnesses.
"""
import math

import pandas
import pytest

import triplets
from triplets import iri
from triplets.iri import CIM_NS, RDF_TYPE, XSD_NS, SchemaTerms, iri_pandas

CIM16 = "http://iec.ch/TC57/2013/CIM-schema-cim16#"
NC = "https://cim4.eu/ns/nc#"
UUID = "0f7a1c4e-3b2d-4c5a-9e8f-1a2b3c4d5e6f"

TERMS = SchemaTerms(
    namespaces={"ACLineSegment.r": CIM16, "Breaker": CIM16, "Diagram.orientation": CIM16,
                "OrientationKind.negative": CIM16, "NcClass": NC},
    enum_keys=frozenset({"Diagram.orientation"}),
    datatypes={"ACLineSegment.r": XSD_NS + "float", "IdentifiedObject.mRID": None,
               "IdentifiedObject.name": None},
)

# (function name, input, expected) — unary, schema-free
CASES = [
    # local_id: exactly one prefix, longest first
    ("local_id", None, None),
    ("local_id", "", ""),
    ("local_id", "urn:uuid:" + UUID, UUID),
    ("local_id", "#_foo_bar", "foo_bar"),
    ("local_id", "_123", "123"),
    ("local_id", "urn:uuid:_x", "_x"),
    ("local_id", "abc", "abc"),
    ("local_id", "http://example.org/ns#g", "http://example.org/ns#g"),
    # local_value: ID rule, then http(s)…#frag; never '/'
    ("local_value", None, None),
    ("local_value", "urn:uuid:" + UUID, UUID),
    ("local_value", "#_" + UUID, UUID),
    ("local_value", "http://iec.ch/TC57/CIM100#Breaker", "Breaker"),
    ("local_value", NC + "Kind.value", "Kind.value"),
    ("local_value", "http://example.org/path/only", "http://example.org/path/only"),
    ("local_value", "12.5", "12.5"),
    ("local_value", "a#b", "a#b"),
    # local_key: rdf:type → Type; http(s)…#frag; '/'-only IRIs stay whole
    ("local_key", None, None),
    ("local_key", RDF_TYPE, "Type"),
    ("local_key", CIM_NS + "ACLineSegment.r", "ACLineSegment.r"),
    ("local_key", "http://purl.org/dc/terms/created", "http://purl.org/dc/terms/created"),
    ("local_key", "ACLineSegment.r", "ACLineSegment.r"),
    # local_term: vocabulary — '#' then '/'
    ("local_term", None, None),
    ("local_term", "http://www.w3.org/ns/shacl#minCount", "minCount"),
    ("local_term", "https://schema.org/domainIncludes", "domainIncludes"),
    ("local_term", "#Equipment", "Equipment"),
    ("local_term", "Breaker", "Breaker"),
    # is_iri
    ("is_iri", "http://a", True),
    ("is_iri", "https://a", True),
    ("is_iri", "urn:uuid:a", True),
    ("is_iri", "abc", False),
    ("is_iri", "urn", False),
    # expand_id
    ("expand_id", UUID, "urn:uuid:" + UUID),
    ("expand_id", "http://example.org/ns#g", "http://example.org/ns#g"),
    ("expand_id", "https://example.org/g", "https://example.org/g"),
]

# (function name, input, terms, expected)
NAME_CASES = [
    ("expand_name", "Breaker", TERMS, CIM16 + "Breaker"),
    ("expand_name", "Equipment", TERMS, CIM_NS + "Equipment"),      # abstract: no entry → default
    ("expand_name", "NcClass", TERMS, NC + "NcClass"),
    ("expand_name", "Breaker", None, CIM_NS + "Breaker"),
    ("expand_name", "http://x#Y", TERMS, "http://x#Y"),
    ("expand_key", "Type", TERMS, RDF_TYPE),
    ("expand_key", "ACLineSegment.r", TERMS, CIM16 + "ACLineSegment.r"),
    ("expand_key", "Unknown.x", TERMS, CIM_NS + "Unknown.x"),
    ("expand_key", "http://purl.org/dc/terms/created", TERMS, "http://purl.org/dc/terms/created"),
    ("expand_key", "ACLineSegment.r", None, CIM_NS + "ACLineSegment.r"),
]

# (key, value, terms, expected kind, expected payload)
VALUE_CASES = [
    ("Type", "Breaker", TERMS, "iri", CIM16 + "Breaker"),
    ("Type", "Breaker", None, "iri", CIM_NS + "Breaker"),
    ("Type", "http://x#Y", TERMS, "iri", "http://x#Y"),
    ("Diagram.orientation", "OrientationKind.negative", TERMS, "iri", CIM16 + "OrientationKind.negative"),
    ("Diagram.orientation", "OrientationKind.negative", None, "literal", None),
    ("ACLineSegment.r", "1.5", TERMS, "literal", XSD_NS + "float"),
    ("IdentifiedObject.mRID", UUID, TERMS, "literal", None),           # schema beats UUID look
    ("IdentifiedObject.name", "Foo", TERMS, "literal", None),
    ("Terminal.ConductingEquipment", UUID, TERMS, "iri", "urn:uuid:" + UUID),
    ("Terminal.ConductingEquipment", UUID, None, "iri", "urn:uuid:" + UUID),
    ("Terminal.ConductingEquipment", UUID.upper(), TERMS, "literal", None),  # canonical lowercase only
    ("Model.DependentOn", "urn:uuid:" + UUID, TERMS, "iri", "urn:uuid:" + UUID),
    ("X.y", "https://example.org/thing", None, "iri", "https://example.org/thing"),
    ("X.y", "plain text", None, "literal", None),
    ("X.y", None, None, "literal", None),                             # null in → literal/null out, as the flavors do
]


def _norm(value):
    return None if value is None or value is pandas.NA or (isinstance(value, float) and math.isnan(value)) else value


# ── scalar ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,text,expected", CASES)
def test_scalar(name, text, expected):
    assert getattr(iri, name)(text) == expected


@pytest.mark.parametrize("name,text,terms,expected", NAME_CASES)
def test_scalar_expand_name(name, text, terms, expected):
    assert getattr(iri, name)(text, terms) == expected


@pytest.mark.parametrize("key,value,terms,kind,payload", VALUE_CASES)
def test_scalar_expand_value(key, value, terms, kind, payload):
    assert iri.expand_value(key, value, terms) == (kind, payload)


# ── pandas flavor vs scalar ────────────────────────────────────────────────────

def _grouped(cases):
    groups = {}
    for name, *rest in cases:
        groups.setdefault(name, []).append(rest)
    return groups


def _string_dtypes():
    dtypes = [object]
    try:
        import pyarrow
        dtypes.append(pandas.ArrowDtype(pyarrow.string()))   # qlever CONSTRUCT results arrive like this
    except ImportError:
        pass
    return dtypes


@pytest.mark.parametrize("dtype", _string_dtypes(), ids=str)
@pytest.mark.parametrize("name", sorted(_grouped(CASES)))
def test_pandas_matches_scalar(name, dtype):
    inputs = [text for text, _ in _grouped(CASES)[name] if not (name.startswith("expand") and text is None)]
    got = getattr(iri_pandas, name)(pandas.Series(inputs, dtype=dtype))
    assert [_norm(v) for v in got.tolist()] == [getattr(iri, name)(text) for text in inputs]


@pytest.mark.parametrize("name", sorted(_grouped(NAME_CASES)))
def test_pandas_expand_name_matches_scalar(name):
    for terms in (TERMS, None):
        inputs = [text for text, t, _ in _grouped(NAME_CASES)[name] if t is terms]
        got = getattr(iri_pandas, name)(pandas.Series(inputs, dtype=object), terms)
        assert got.tolist() == [getattr(iri, name)(text, terms) for text in inputs]


def test_pandas_expand_value_matches_scalar():
    for terms in (TERMS, None):
        rows = [(k, v) for k, v, t, _, _ in VALUE_CASES if t is terms]
        keys = pandas.Series([k for k, _ in rows], dtype=object)   # includes a None VALUE row
        values = pandas.Series([v for _, v in rows], dtype=object)
        kinds, payloads = iri_pandas.expand_value(keys, values, terms)
        expected = [iri.expand_value(k, v, terms) for k, v in rows]
        assert list(zip(kinds.tolist(), [_norm(p) for p in payloads.tolist()])) == expected


# ── polars flavor vs scalar ────────────────────────────────────────────────────

polars = pytest.importorskip("polars")
from triplets.iri import iri_polars  # noqa: E402


@pytest.mark.parametrize("name", sorted(_grouped(CASES)))
def test_polars_matches_scalar(name):
    inputs = [text for text, _ in _grouped(CASES)[name] if not (name.startswith("expand") and text is None)]
    frame = polars.DataFrame({"x": inputs}, schema={"x": polars.Utf8})
    got = frame.select(getattr(iri_polars, name)("x").alias("y"))["y"].to_list()
    assert got == [getattr(iri, name)(text) for text in inputs]


@pytest.mark.parametrize("name", sorted(_grouped(NAME_CASES)))
def test_polars_expand_name_matches_scalar(name):
    for terms in (TERMS, None):
        inputs = [text for text, t, _ in _grouped(NAME_CASES)[name] if t is terms]
        frame = polars.DataFrame({"x": inputs}, schema={"x": polars.Utf8})
        got = frame.select(getattr(iri_polars, name)("x", terms).alias("y"))["y"].to_list()
        assert got == [getattr(iri, name)(text, terms) for text in inputs]


def test_polars_expand_value_matches_scalar():
    for terms in (TERMS, None):
        rows = [(k, v) for k, v, t, _, _ in VALUE_CASES if t is terms]
        frame = polars.DataFrame({"KEY": [k for k, _ in rows], "VALUE": [v for _, v in rows]},
                                 schema={"KEY": polars.Utf8, "VALUE": polars.Utf8})
        kind, payload = iri_polars.expand_value("KEY", "VALUE", terms)
        got = frame.select(kind.alias("kind"), payload.alias("payload"))
        assert list(zip(got["kind"].to_list(), got["payload"].to_list())) == \
            [iri.expand_value(k, v, terms) for k, v in rows]


# ── SchemaTerms from a shipped schema ──────────────────────────────────────────

SCHEMA = "triplets/export_schema/ENTSOE_CGMES_2.4.15_552_ED1.json"


def test_schema_terms_from_shipped_schema():
    terms = SchemaTerms.from_rdf_map(SCHEMA)
    assert "Diagram.orientation" in terms.enum_keys
    assert "Diagram.orientation" not in terms.datatypes            # xsd:anyURI → reference handling
    assert terms.datatypes["ACLineSegment.r"] == XSD_NS + "float"
    assert terms.datatypes["IdentifiedObject.name"] is None       # xsd:string: literal, no annotation
    assert terms.namespace("OrientationKind.negative") == CIM16
    assert terms.namespace("ACLineSegment") == CIM16
    assert terms.namespace("Equipment") == CIM_NS                 # abstract, no entry
    assert terms.key_kind("Diagram.orientation") == "iri"
    assert terms.key_kind("ACLineSegment.r") == "literal"
    assert terms.key_kind("Terminal.ConductingEquipment") is None


def test_schema_terms_cached_by_content():
    triplets.clear_caches()
    first = SchemaTerms.from_rdf_map(SCHEMA)
    assert SchemaTerms.from_rdf_map(SCHEMA) is first
    assert SchemaTerms.from_rdf_map(first) is first
    assert SchemaTerms.from_rdf_map(None) is iri.EMPTY_TERMS
    triplets.clear_caches()
    assert SchemaTerms.from_rdf_map(SCHEMA) is not first


# ── native mirrors: the cython parser applies local_id / local_value in C++ ───

PARSE_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:cim="http://iec.ch/TC57/CIM100#" xmlns:nc="https://cim4.eu/ns/nc#">
  <cim:Breaker rdf:about="urn:uuid:_x">
    <cim:Equipment.EquipmentContainer rdf:resource="#_{uuid}"/>
    <cim:Switch.open>false</cim:Switch.open>
    <cim:Breaker.kind rdf:resource="http://iec.ch/TC57/CIM100#SwitchKind.breaker"/>
    <cim:Breaker.ref rdf:resource="https://cim4.eu/ns/nc#Kind.value"/>
    <cim:Breaker.uri rdf:resource="http://example.org/path/only"/>
  </cim:Breaker>
  <nc:Thing rdf:ID="_abc">
    <nc:Thing.ref rdf:resource="urn:uuid:{uuid}"/>
  </nc:Thing>
</rdf:RDF>
""".format(uuid=UUID)


def _parse_engine_or_skip(engine):
    try:
        triplets.parser.get_engine(engine)
    except Exception as error:      # extension not built in this environment
        pytest.skip(f"{engine} not available: {error}")


@pytest.mark.parametrize("engine", ["python_lxml_pandas", "python_lxml_arrow", "cython_pugixml_arrow"])
def test_parse_engines_apply_local_id_and_local_value(engine, tmp_path):
    """Every parse engine shortens IDs / reference VALUEs exactly like the scalar rule."""
    _parse_engine_or_skip(engine)
    path = tmp_path / "iri_cases.xml"
    path.write_text(PARSE_FIXTURE)
    frame = triplets.parse(str(path), engine=engine, return_type="pandas")
    frame = frame[~frame["KEY"].isin(["label"]) & ~frame["VALUE"].isin(["Distribution", "NamespaceMap"])]
    rows = {(row.KEY, row.VALUE) for row in frame.itertuples() if row.KEY != "Type"} | \
           {("Type", row.VALUE) for row in frame[frame["KEY"] == "Type"].itertuples()}
    ids = set(frame["ID"])
    assert {iri.local_id("urn:uuid:_x"), iri.local_id("_abc")} <= ids
    assert ("Equipment.EquipmentContainer", iri.local_value("#_" + UUID)) in rows
    assert ("Thing.ref", iri.local_value("urn:uuid:" + UUID)) in rows
    assert ("Breaker.kind", iri.local_value("http://iec.ch/TC57/CIM100#SwitchKind.breaker")) in rows
    assert ("Breaker.ref", iri.local_value("https://cim4.eu/ns/nc#Kind.value")) in rows
    assert ("Breaker.uri", iri.local_value("http://example.org/path/only")) in rows
    assert ("Switch.open", "false") in rows
