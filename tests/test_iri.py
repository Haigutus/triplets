"""The IRI contract — one case table per rule, every flavor held to the scalar definition.

Scalar ``triplets.iri`` is the readable rule. Each table below is the contract;
the pandas (object + arrow strings), polars and duckdb flavors are run over the
same rows through one adapter each. The cython parser is checked on a parse
fixture; the qlever C++ ingest in ``test_sparql_qlever_ingest.py``.
"""
import math

import pandas
import pytest

import triplets
from triplets import iri
from triplets.iri import CIM_NS, RDF_TYPE, XSD_NS, iri_duckdb, iri_pandas

CIM16 = "http://iec.ch/TC57/2013/CIM-schema-cim16#"
NC = "https://cim4.eu/ns/nc#"
UUID = "0f7a1c4e-3b2d-4c5a-9e8f-1a2b3c4d5e6f"

# Two profiles, one name declared in both (first wins), one non-dict section (skipped).
RDF_MAP = {
    "EQ": {
        "Breaker": {"type": "Class", "namespace": CIM16},
        "ACLineSegment.r": {"type": "Attribute", "xsd:type": "xsd:float", "namespace": CIM16},
        "IdentifiedObject.name": {"type": "Attribute", "xsd:type": "xsd:string", "namespace": CIM16},
        "IdentifiedObject.mRID": {"type": "Attribute", "xsd:type": "xsd:string", "namespace": CIM16},
        "Model.modelingAuthoritySet": {"type": "Attribute", "xsd:type": "xsd:anyURI", "namespace": CIM16},
        "Diagram.orientation": {"type": "Enumeration", "xsd:type": "xsd:anyURI", "namespace": CIM16},
        "OrientationKind.negative": {"type": "EnumerationValue", "namespace": CIM16},
        "Terminal.ConductingEquipment": {"type": "Association", "xsd:type": "xsd:anyURI", "namespace": CIM16},
        "NcClass": {"type": "Class", "namespace": NC},
    },
    "SSH": {"Breaker": {"type": "Class", "namespace": NC}},
    "ProfileNamespaceMap": {"cim": CIM16},
}
NAMESPACES = iri.namespaces(RDF_MAP)
VALUE_TYPES = iri.value_types(RDF_MAP)
DATATYPES = iri.datatypes(RDF_MAP)
MAPS = dict(namespaces=NAMESPACES, value_types=VALUE_TYPES, datatypes=DATATYPES)


# ── flat maps ──────────────────────────────────────────────────────────────────

def test_namespaces_first_profile_wins():
    assert NAMESPACES == {"Breaker": CIM16, "ACLineSegment.r": CIM16, "IdentifiedObject.name": CIM16,
                          "IdentifiedObject.mRID": CIM16, "Model.modelingAuthoritySet": CIM16,
                          "Diagram.orientation": CIM16, "OrientationKind.negative": CIM16,
                          "Terminal.ConductingEquipment": CIM16, "NcClass": NC}


def test_key_types():
    assert iri.key_types(RDF_MAP)["Terminal.ConductingEquipment"] == "Association"
    assert iri.key_types(RDF_MAP)["Breaker"] == "Class"


def test_datatypes_string_is_none_anyuri_absent():
    assert DATATYPES == {"ACLineSegment.r": XSD_NS + "float", "IdentifiedObject.name": None,
                         "IdentifiedObject.mRID": None}


def test_value_types():
    assert VALUE_TYPES == {"ACLineSegment.r": "literal", "IdentifiedObject.name": "literal",
                           "IdentifiedObject.mRID": "literal", "Diagram.orientation": "enum",
                           "Terminal.ConductingEquipment": "reference"}


def test_flat_maps_without_schema_are_empty():
    assert (iri.namespaces(None), iri.key_types(None), iri.datatypes(None), iri.value_types(None)) == ({}, {}, {}, {})


def test_flat_maps_from_shipped_schema_path():
    path = "triplets/export_schema/ENTSOE_CGMES_2.4.15_552_ED1.json"
    rdf_map = iri.load_rdf_map(path)
    assert iri.value_types(rdf_map)["Diagram.DiagramStyle"] == "reference"
    assert iri.value_types(rdf_map)["Diagram.orientation"] == "enum"
    assert iri.datatypes(rdf_map)["ACLineSegment.r"] == XSD_NS + "float"
    assert iri.namespaces(rdf_map)["OrientationKind.negative"] == CIM16
    assert "Equipment" not in iri.namespaces(rdf_map)          # abstract: no entry → CIM100 at use


# ── local (schema-free), one row per rule ──────────────────────────────────────

LOCAL_CASES = [
    ("local_id", None, None),
    ("local_id", "urn:uuid:" + UUID, UUID),
    ("local_id", "#_" + UUID, UUID),
    ("local_id", "_" + UUID, UUID),
    ("local_id", "urn:uuid:_x", "_x"),                       # exactly one prefix
    ("local_id", "#__x", "_x"),
    ("local_id", "http://example.org/ns#g", "http://example.org/ns#g"),
    ("local_id", "plain", "plain"),
    ("local_value", None, None),
    ("local_value", "#_" + UUID, UUID),
    ("local_value", "urn:uuid:" + UUID, UUID),
    ("local_value", "http://iec.ch/TC57/CIM100#SwitchKind.breaker", "SwitchKind.breaker"),
    ("local_value", "https://cim4.eu/ns/nc#Kind.value", "Kind.value"),
    ("local_value", "http://a#b#c", "c"),                     # last '#'
    ("local_value", "http://example.org/path/only", "http://example.org/path/only"),   # never '/'
    ("local_value", "urn:example:thing", "urn:example:thing"),
    ("local_value", "Breaker", "Breaker"),
    ("local_key", None, None),
    ("local_key", RDF_TYPE, "Type"),
    ("local_key", "http://iec.ch/TC57/CIM100#ACLineSegment.r", "ACLineSegment.r"),
    ("local_key", "urn:example:pred", "urn:example:pred"),
    ("local_key", "_ACLineSegment.r", "_ACLineSegment.r"),   # a KEY is not an ID
    ("local_term", None, None),
    ("local_term", "http://www.w3.org/ns/shacl#minCount", "minCount"),
    ("local_term", "https://schema.org/domainIncludes", "domainIncludes"),
    ("local_term", "#Equipment", "Equipment"),
    ("local_term", "Breaker", "Breaker"),
    ("is_iri", None, False),
    ("is_iri", "http://a", True),
    ("is_iri", "https://a", True),
    ("is_iri", "urn:uuid:a", True),
    ("is_iri", "abc", False),
    ("is_iri", "urn", False),
    ("absolute_id", None, None),
    ("absolute_id", UUID, "urn:uuid:" + UUID),
    ("absolute_id", "_x", "urn:uuid:_x"),
    ("absolute_id", "http://example.org/ns#g", "http://example.org/ns#g"),
    ("absolute_id", "urn:example:g", "urn:example:g"),
]

# (function, input, namespaces?, expected)
NAME_CASES = [
    ("absolute_name", "Breaker", True, CIM16 + "Breaker"),
    ("absolute_name", "Breaker", False, CIM_NS + "Breaker"),
    ("absolute_name", "NcClass", True, NC + "NcClass"),
    ("absolute_name", "Equipment", True, CIM_NS + "Equipment"),             # absent → default
    ("absolute_name", "http://x#Y", True, "http://x#Y"),
    ("absolute_name", "urn:example:Y", True, "urn:example:Y"),
    ("absolute_name", None, True, None),
    ("absolute_key", "Type", True, RDF_TYPE),
    ("absolute_key", "ACLineSegment.r", True, CIM16 + "ACLineSegment.r"),
    ("absolute_key", "ACLineSegment.r", False, CIM_NS + "ACLineSegment.r"),
    ("absolute_key", "http://x#p", True, "http://x#p"),
]

# (key, value, maps?, expected kind, expected payload)
VALUE_CASES = [
    ("Type", "Breaker", True, "iri", CIM16 + "Breaker"),
    ("Type", "Breaker", False, "iri", CIM_NS + "Breaker"),
    ("Type", "http://x#Y", True, "iri", "http://x#Y"),
    ("X.y", "https://example.org/thing", False, "iri", "https://example.org/thing"),   # IRI passes through
    ("Diagram.orientation", "OrientationKind.negative", True, "iri", CIM16 + "OrientationKind.negative"),
    ("Diagram.orientation", "OrientationKind.negative", False, "literal", None),
    ("Terminal.ConductingEquipment", UUID, True, "iri", "urn:uuid:" + UUID),           # reference by schema…
    ("Terminal.ConductingEquipment", UUID.upper(), True, "iri", "urn:uuid:" + UUID.upper()),
    ("Terminal.ConductingEquipment", "_abc", True, "iri", "urn:uuid:_abc"),
    ("Terminal.ConductingEquipment", "urn:uuid:" + UUID, True, "iri", "urn:uuid:" + UUID),
    ("Terminal.ConductingEquipment", UUID.upper(), False, "literal", None),           # …UUID look without one
    ("Terminal.ConductingEquipment", UUID, False, "iri", "urn:uuid:" + UUID),
    ("ACLineSegment.r", "1.5", True, "literal", XSD_NS + "float"),
    ("ACLineSegment.r", "1.5", False, "literal", None),
    ("IdentifiedObject.mRID", UUID, True, "literal", None),                           # schema beats UUID look
    ("IdentifiedObject.name", "Foo", True, "literal", None),
    ("Model.modelingAuthoritySet", "http://tso.example", True, "iri", "http://tso.example"),
    ("X.y", "plain text", False, "literal", None),
    ("X.y", None, True, "literal", None),
]

# (key, maps?, sh:nodeKind, expected)
NODE_KIND_CASES = [
    ("Diagram.orientation", "IRI", "iri"),
    ("Diagram.orientation", "Literal", "iri"),
    ("Terminal.ConductingEquipment", "Literal", "iri"),       # every reference violates Literal
    ("Terminal.ConductingEquipment", "IRI", None),            # against IRI the value form still decides
    ("ACLineSegment.r", "IRI", "literal"),
    ("IdentifiedObject.name", "Literal", "literal"),
    ("Unknown.key", "IRI", None),
]


# ── flavors: one adapter each, all held to the scalar rule ─────────────────────

def _norm(value):
    return None if value is None or value is pandas.NA or (isinstance(value, float) and math.isnan(value)) else value


def _pandas(dtype):
    def apply(name, inputs, **maps):
        series = pandas.Series(inputs, dtype=dtype)
        return [_norm(v) for v in getattr(iri_pandas, name)(series, **maps).tolist()]

    def apply_value(keys, values, **maps):
        kind, payload = iri_pandas.absolute_value(pandas.Series(keys, dtype=dtype), pandas.Series(values, dtype=dtype), **maps)
        return list(zip(kind.tolist(), [_norm(v) for v in payload.tolist()]))
    return apply, apply_value


def _polars():
    polars = pytest.importorskip("polars")
    from triplets.iri import iri_polars

    def apply(name, inputs, **maps):
        frame = polars.DataFrame({"x": inputs}, schema={"x": polars.Utf8})
        return frame.select(getattr(iri_polars, name)("x", **maps).alias("y"))["y"].to_list()

    def apply_value(keys, values, **maps):
        frame = polars.DataFrame({"KEY": keys, "VALUE": values}, schema={"KEY": polars.Utf8, "VALUE": polars.Utf8})
        kind, payload = iri_polars.absolute_value("KEY", "VALUE", **maps)
        out = frame.select(kind.alias("k"), payload.alias("p"))
        return list(zip(out["k"].to_list(), out["p"].to_list()))
    return apply, apply_value


def _duckdb():
    duckdb = pytest.importorskip("duckdb")

    def apply(name, inputs, **maps):
        frame = pandas.DataFrame({"x": pandas.Series(inputs, dtype=object)})   # noqa: F841 — duckdb sees it by name
        return [row[0] for row in duckdb.sql(f"SELECT {getattr(iri_duckdb, name)('x')} AS y FROM frame").fetchall()]
    return apply, None


FLAVORS = {
    "pandas-object": lambda: _pandas(object),
    "pandas-arrow": lambda: _pandas(pandas.ArrowDtype(pytest.importorskip("pyarrow").string())),
    "polars": _polars,
    "duckdb": _duckdb,
}


def _rows(cases, name):
    return [case[1:] for case in cases if case[0] == name]


def _has(flavor, name):
    module = {"duckdb": iri_duckdb}.get(flavor)
    return hasattr(module, name) if module else True


@pytest.mark.parametrize("name,text,expected", LOCAL_CASES)
def test_scalar_local(name, text, expected):
    assert getattr(iri, name)(text) == expected


@pytest.mark.parametrize("flavor", FLAVORS)
@pytest.mark.parametrize("name", sorted({case[0] for case in LOCAL_CASES}))
def test_flavor_local_matches_scalar(flavor, name):
    if not _has(flavor, name):
        pytest.skip(f"{flavor} has no {name}")
    apply, _ = FLAVORS[flavor]()
    rows = _rows(LOCAL_CASES, name)
    assert apply(name, [text for text, _ in rows]) == [expected for _, expected in rows]


@pytest.mark.parametrize("name,text,with_namespaces,expected", NAME_CASES)
def test_scalar_absolute_name(name, text, with_namespaces, expected):
    assert getattr(iri, name)(text, NAMESPACES if with_namespaces else None) == expected


@pytest.mark.parametrize("flavor", ["pandas-object", "pandas-arrow", "polars"])
@pytest.mark.parametrize("name", ["absolute_name", "absolute_key"])
@pytest.mark.parametrize("with_namespaces", [True, False], ids=["schema", "no-schema"])
def test_flavor_absolute_name_matches_scalar(flavor, name, with_namespaces):
    apply, _ = FLAVORS[flavor]()
    rows = [(t, e) for t, w, e in _rows(NAME_CASES, name) if w == with_namespaces]
    namespaces = NAMESPACES if with_namespaces else None
    assert apply(name, [t for t, _ in rows], namespaces=namespaces) == [e for _, e in rows]


@pytest.mark.parametrize("key,value,with_maps,kind,payload", VALUE_CASES)
def test_scalar_absolute_value(key, value, with_maps, kind, payload):
    assert iri.absolute_value(key, value, **(MAPS if with_maps else {})) == (kind, payload)


@pytest.mark.parametrize("flavor", ["pandas-object", "pandas-arrow", "polars"])
@pytest.mark.parametrize("with_maps", [True, False], ids=["schema", "no-schema"])
def test_flavor_absolute_value_matches_scalar(flavor, with_maps):
    _, apply_value = FLAVORS[flavor]()
    rows = [(k, v, kind, p) for k, v, w, kind, p in VALUE_CASES if w == with_maps]
    got = apply_value([k for k, *_ in rows], [v for _, v, *_ in rows], **(MAPS if with_maps else {}))
    assert got == [(kind, p) for _, _, kind, p in rows]


@pytest.mark.parametrize("key,expected_kind,result", NODE_KIND_CASES)
def test_node_kind(key, expected_kind, result):
    assert iri.node_kind(key, VALUE_TYPES, expected_kind) == result


# ── native mirror: the cython parser applies local_id / local_value in C++ ─────

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


@pytest.mark.parametrize("engine", ["python_lxml_pandas", "python_lxml_arrow", "cython_pugixml_arrow"])
def test_parse_engines_apply_local_id_and_local_value(engine, tmp_path):
    try:
        triplets.parser.get_engine(engine)
    except Exception as error:      # extension not built in this environment
        pytest.skip(f"{engine} not available: {error}")
    path = tmp_path / "iri_cases.xml"
    path.write_text(PARSE_FIXTURE)
    frame = triplets.parse(str(path), engine=engine, return_type="pandas")
    rows = set(zip(frame["KEY"], frame["VALUE"]))
    assert {iri.local_id("urn:uuid:_x"), iri.local_id("_abc")} <= set(frame["ID"])
    assert {("Equipment.EquipmentContainer", iri.local_value("#_" + UUID)),
            ("Thing.ref", iri.local_value("urn:uuid:" + UUID)),
            ("Breaker.kind", iri.local_value("http://iec.ch/TC57/CIM100#SwitchKind.breaker")),
            ("Breaker.ref", iri.local_value("https://cim4.eu/ns/nc#Kind.value")),
            ("Breaker.uri", iri.local_value("http://example.org/path/only"))} <= rows
