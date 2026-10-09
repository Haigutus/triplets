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
from triplets.iri import CIM_NS, RDF_TYPE, TRIPLETS_NS, XSD_NS, iri_duckdb, iri_pandas

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
        "Float": {"type": "Primitive", "xsd:type": "xsd:float", "namespace": CIM16},   # not a KEY: no value type, no datatype
        "Legacy.untyped": {"xsd:type": "xsd:float", "namespace": CIM16},               # no entry type: an undefined KEY
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
                          "Terminal.ConductingEquipment": CIM16, "NcClass": NC, "Float": CIM16,
                          "Legacy.untyped": CIM16}


def test_key_types():
    assert iri.key_types(RDF_MAP)["Terminal.ConductingEquipment"] == "Association"
    assert iri.key_types(RDF_MAP)["Breaker"] == "Class"


def test_datatypes_attributes_only_string_is_none():
    """Annotation for Attribute literals: anyURI included (it is a literal datatype), string → None;
    Association / Enumeration entries carry xsd:anyURI too but are not literals."""
    assert DATATYPES == {"ACLineSegment.r": XSD_NS + "float", "IdentifiedObject.name": None,
                         "IdentifiedObject.mRID": None, "Model.modelingAuthoritySet": XSD_NS + "anyURI"}


def test_value_types_by_entry_type_only():
    assert VALUE_TYPES == {"ACLineSegment.r": "literal", "IdentifiedObject.name": "literal",
                           "IdentifiedObject.mRID": "literal", "Model.modelingAuthoritySet": "literal",
                           "Diagram.orientation": "enum", "Terminal.ConductingEquipment": "reference"}


def test_flat_maps_without_schema_are_empty():
    assert (iri.namespaces(None), iri.key_types(None), iri.datatypes(None), iri.value_types(None)) == ({}, {}, {}, {})


def test_flat_maps_from_shipped_schema_path():
    path = "triplets/export_schema/ENTSOE_CGMES_2.4.15_552_ED1.json"
    rdf_map = iri.load_rdf_map(path)
    assert iri.value_types(rdf_map)["Diagram.DiagramStyle"] == "reference"
    assert iri.value_types(rdf_map)["Diagram.orientation"] == "enum"
    assert iri.datatypes(rdf_map)["ACLineSegment.r"] == XSD_NS + "float"
    assert "Diagram.DiagramStyle" not in iri.datatypes(rdf_map)          # Association: never a datatype
    assert iri.namespaces(rdf_map)["OrientationKind.negative"] == CIM16
    assert "Equipment" not in iri.namespaces(rdf_map)          # abstract: no entry → undefined_namespace at use


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
    ("local_key", "urn:example:pred", "pred"),                       # URN: after its last ":"
    ("local_key", "http://purl.org/dc/terms/issued", "issued"),       # "/" namespace: the XML element local name
    ("local_key", "https://schema.org/name", "name"),
    ("local_key", "http://a/b#c", "c"),
    ("local_key", "_ACLineSegment.r", "_ACLineSegment.r"),   # a KEY is not an ID
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
    ("absolute_id", "just some text", "urn:uuid:just%20some%20text"),     # stays an IRI: encoded, never a literal
    ("absolute_id", "http://x/a b", "http://x/a%20b"),
    ("encode_iri", None, None),
    ("encode_iri", "a b\t<c>\"{d}|^`\\", "a%20b%09%3Cc%3E%22%7Bd%7D%7C%5E%60%5C"),
    ("encode_iri", "urn:uuid:" + UUID, "urn:uuid:" + UUID),
    ("encode_iri", "http://x/a%20b#c", "http://x/a%20b#c"),               # existing escapes pass untouched
    ("decode_iri", None, None),
    ("decode_iri", "urn:uuid:just%20some%20text", "urn:uuid:just some text"),
    ("decode_iri", "a%3Cb%3E%5C", "a<b>\\"),
    ("decode_iri", "a%41%2F", "a%41%2F"),                                 # only the escapes encode_iri writes
]

SPLIT_CASES = [
    (None, (None, None)),
    ("http://iec.ch/TC57/CIM100#Breaker", ("http://iec.ch/TC57/CIM100#", "Breaker")),
    ("http://purl.org/dc/terms/issued", ("http://purl.org/dc/terms/", "issued")),
    ("https://example.org/vocab/Thing", ("https://example.org/vocab/", "Thing")),
    ("http://a#b#c", ("http://a#b#", "c")),
    ("http://a/b#c", ("http://a/b#", "c")),
    ("#Equipment", ("#", "Equipment")),
    ("urn:uuid:" + UUID, ("urn:uuid:", UUID)),
    ("urn:example:Thing", ("urn:example:", "Thing")),
    ("Breaker", ("", "Breaker")),
]


@pytest.mark.parametrize("text,expected", SPLIT_CASES)
def test_scalar_split_iri(text, expected):
    """The one split: lossless, namespace ends at its delimiter."""
    assert iri.split_iri(text) == expected
    if text is not None:
        assert "".join(expected) == text


@pytest.mark.parametrize("flavor", ["pandas-object", "pandas-arrow", "polars"])
def test_flavor_split_iri_matches_scalar(flavor):
    texts = [t for t, _ in SPLIT_CASES if t is not None]
    expected = [e for t, e in SPLIT_CASES if t is not None]
    if flavor == "polars":
        polars = pytest.importorskip("polars")
        from triplets.iri import iri_polars
        namespace, local = iri_polars.split_iri("x")
        out = polars.DataFrame({"x": texts}).select(namespace.alias("n"), local.alias("l"))
        got = list(zip(out["n"].to_list(), out["l"].to_list()))
    else:
        dtype = object if flavor == "pandas-object" else pandas.ArrowDtype(pytest.importorskip("pyarrow").string())
        namespace, local = iri_pandas.split_iri(pandas.Series(texts, dtype=dtype))
        got = list(zip(namespace.tolist(), local.tolist()))
    assert got == expected


def test_local_key_type_key():
    """rdf:type → the caller's type_key ("Type" is the CIM typed-node KEY)."""
    assert iri.local_key(RDF_TYPE) == iri.TYPE_KEY == "Type"
    assert iri.local_key(RDF_TYPE, type_key="type") == "type"


# (iri, kind, expected) — kind decides name vs node; None guesses without a schema
VALUE_KIND_CASES = [
    (None, None, None),
    ("http://iec.ch/TC57/CIM100#Breaker", "class", "Breaker"),
    ("https://example.org/vocab/Dataset", "class", "Dataset"),                 # "/" class: a name
    ("http://iec.ch/TC57/CIM100#SwitchKind.breaker", "enum", "SwitchKind.breaker"),
    ("http://ex.org/m#b", "reference", "http://ex.org/m#b"),                   # node: namespace not restorable
    ("urn:uuid:" + UUID, "reference", UUID),
    ("#_" + UUID, "reference", UUID),
    ("http://ex.org/m#b", None, "b"),                                          # guess: http + "#" → a name
    ("http://example.org/profile/EQ/3.0", None, "http://example.org/profile/EQ/3.0"),   # guess: "/" only → whole
    ("urn:example:thing", None, "urn:example:thing"),
    ("urn:uuid:" + UUID, None, UUID),
]


@pytest.mark.parametrize("text,kind,expected", VALUE_KIND_CASES)
def test_scalar_local_value_kind(text, kind, expected):
    assert iri.local_value(text, kind) == expected


@pytest.mark.parametrize("flavor", ["pandas-object", "pandas-arrow", "polars"])
def test_flavor_local_value_kind_matches_scalar(flavor):
    """Per-row kinds (the read_nquads(rdf_map=) path) — same answers as the scalar rule."""
    texts = [t for t, _, _ in VALUE_KIND_CASES]
    kinds = [k for _, k, _ in VALUE_KIND_CASES]
    expected = [e for _, _, e in VALUE_KIND_CASES]
    if flavor == "polars":
        polars = pytest.importorskip("polars")
        from triplets.iri import iri_polars
        frame = polars.DataFrame({"x": texts, "k": kinds}, schema={"x": polars.Utf8, "k": polars.Utf8})
        got = frame.select(iri_polars.local_value("x", polars.col("k")).alias("y"))["y"].to_list()
    else:
        dtype = object if flavor == "pandas-object" else pandas.ArrowDtype(pytest.importorskip("pyarrow").string())
        got = [_norm(v) for v in iri_pandas.local_value(pandas.Series(texts, dtype=dtype), pandas.Series(kinds, dtype=object)).tolist()]
    assert got == expected


# (function, input, with map?, expected) — undefined names take TRIPLETS_NS, with or without a map
NAME_CASES = [
    ("absolute_name", "Breaker", True, CIM16 + "Breaker"),
    ("absolute_name", "Breaker", False, TRIPLETS_NS + "Breaker"),           # no map: every name is undefined
    ("absolute_name", "NcClass", True, NC + "NcClass"),
    ("absolute_name", "Equipment", True, TRIPLETS_NS + "Equipment"),        # not a schema entry
    ("absolute_name", "http://x#Y", True, "http://x#Y"),
    ("absolute_name", "urn:example:Y", True, "urn:example:Y"),
    ("absolute_name", None, True, None),
    ("absolute_name", "Kind.a b", True, TRIPLETS_NS + "Kind.a%20b"),
    ("absolute_key", "Type", True, RDF_TYPE),
    ("absolute_key", "ACLineSegment.r", True, CIM16 + "ACLineSegment.r"),
    ("absolute_key", "ACLineSegment.r", False, TRIPLETS_NS + "ACLineSegment.r"),
    ("absolute_key", "Custom.foo", True, TRIPLETS_NS + "Custom.foo"),
    ("absolute_key", "http://x#p", True, "http://x#p"),
]

# (function, input, with map?, undefined_namespace, expected) — the caller's namespace wins
UNDEFINED_NS_CASES = [
    ("absolute_name", "Equipment", True, CIM_NS, CIM_NS + "Equipment"),
    ("absolute_name", "Breaker", False, CIM_NS, CIM_NS + "Breaker"),
    ("absolute_name", "Breaker", True, "http://acme#", CIM16 + "Breaker"),    # declared: the map wins
    ("absolute_key", "Custom.foo", True, "http://acme#", "http://acme#Custom.foo"),
]

# (key, value, maps?, expected kind, expected payload) — the schema entry type decides, never the text shape
VALUE_CASES = [
    ("Type", "Breaker", True, "iri", CIM16 + "Breaker"),
    ("Type", "Breaker", False, "iri", TRIPLETS_NS + "Breaker"),
    ("Type", "Equipment", True, "iri", TRIPLETS_NS + "Equipment"),                    # class not in the schema
    ("Type", "http://x#Y", True, "iri", "http://x#Y"),
    ("Diagram.orientation", "OrientationKind.negative", True, "iri", CIM16 + "OrientationKind.negative"),
    ("Diagram.orientation", "https://cim4.eu/ns/nc#Kind.x", True, "iri", "https://cim4.eu/ns/nc#Kind.x"),   # absolute enum value
    ("Diagram.orientation", "Nope.value", True, "iri", TRIPLETS_NS + "Nope.value"),   # enum value not in the schema
    ("Terminal.ConductingEquipment", UUID, True, "iri", "urn:uuid:" + UUID),           # reference by schema…
    ("Terminal.ConductingEquipment", UUID.upper(), True, "iri", "urn:uuid:" + UUID.upper()),
    ("Terminal.ConductingEquipment", "_abc", True, "iri", "urn:uuid:_abc"),
    ("Terminal.ConductingEquipment", "urn:uuid:" + UUID, True, "iri", "urn:uuid:" + UUID),
    ("Terminal.ConductingEquipment", "http://example.org/thing", True, "iri", "http://example.org/thing"),
    ("Terminal.ConductingEquipment", "just some text", True, "iri", "urn:uuid:just%20some%20text"),
    ("Diagram.orientation", "Nope.a b", True, "iri", TRIPLETS_NS + "Nope.a%20b"),
    ("Model.modelingAuthoritySet", "http://tso.example/a b", True, "literal", XSD_NS + "anyURI"),   # anyURI text: a literal, verbatim
    ("ACLineSegment.r", "1.5", True, "literal", XSD_NS + "float"),
    ("IdentifiedObject.mRID", UUID, True, "literal", None),                           # attribute: UUID look is irrelevant
    ("IdentifiedObject.name", "https://cim4.eu/ns/nc#Thing", True, "literal", None),  # attribute: IRI look is irrelevant
    ("Model.modelingAuthoritySet", "http://tso.example", True, "literal", XSD_NS + "anyURI"),
    ("Model.modelingAuthoritySet", "not a uri", True, "literal", XSD_NS + "anyURI"),
    ("Legacy.untyped", "1.5", True, "literal", None),                                 # no entry type → undefined KEY
    # undefined KEY (or no schema at all): the text shape decides
    ("X.y", "https://example.org/thing", True, "iri", "https://example.org/thing"),
    ("X.y", UUID, True, "iri", "urn:uuid:" + UUID),
    ("X.y", UUID.upper(), True, "literal", None),                                     # canonical lowercase only
    ("X.y", "plain text", True, "literal", None),
    ("Diagram.orientation", "OrientationKind.negative", False, "literal", None),
    ("Terminal.ConductingEquipment", UUID, False, "iri", "urn:uuid:" + UUID),
    ("Terminal.ConductingEquipment", UUID.upper(), False, "literal", None),
    ("ACLineSegment.r", "1.5", False, "literal", None),
    ("X.y", None, True, "literal", None),
    ("Type", None, True, "literal", None),                                            # a null VALUE is no term
    ("Diagram.orientation", None, True, "literal", None),
    ("Terminal.ConductingEquipment", None, True, "literal", None),
    ("ACLineSegment.r", None, True, "literal", None),
]

# (key, value, defined?) with the map — the rows export_undefined=False keeps
DEFINED_CASES = [
    ("Type", "Breaker", True),
    ("Type", "Equipment", False),
    ("ACLineSegment.r", "1.5", True),
    ("Diagram.orientation", "OrientationKind.negative", True),
    ("Diagram.orientation", "https://cim4.eu/ns/nc#Kind.x", True),                   # absolute enum value passes
    ("Diagram.orientation", "Nope.value", False),
    ("Terminal.ConductingEquipment", "anything", True),
    ("Legacy.untyped", "1.5", False),
    ("X.y", "1.5", False),
]

# (key, node kind the schema fixes) — the exporters' rule; None = decide by value form
NODE_KIND_CASES = [
    ("Diagram.orientation", "iri"),
    ("Terminal.ConductingEquipment", "iri"),       # whatever the text: exported as an IRI
    ("ACLineSegment.r", "literal"),
    ("IdentifiedObject.name", "literal"),
    ("Model.modelingAuthoritySet", "literal"),     # xsd:anyURI attribute: a literal
    ("Unknown.key", None),
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


@pytest.mark.parametrize("name,text,with_namespaces,undefined_namespace,expected", UNDEFINED_NS_CASES)
def test_scalar_undefined_namespace(name, text, with_namespaces, undefined_namespace, expected):
    assert getattr(iri, name)(text, NAMESPACES if with_namespaces else None, undefined_namespace) == expected


@pytest.mark.parametrize("flavor", ["pandas-object", "pandas-arrow", "polars"])
def test_flavor_undefined_namespace_matches_scalar(flavor):
    apply, _ = FLAVORS[flavor]()
    for name, text, with_namespaces, undefined_namespace, expected in UNDEFINED_NS_CASES:
        namespaces = NAMESPACES if with_namespaces else None
        assert apply(name, [text], namespaces=namespaces, undefined_namespace=undefined_namespace) == [expected]


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


@pytest.mark.parametrize("key,result", NODE_KIND_CASES)
def test_node_kind(key, result):
    assert iri.node_kind(key, VALUE_TYPES) == result


@pytest.mark.parametrize("key,value,expected", DEFINED_CASES)
def test_scalar_defined(key, value, expected):
    assert iri.defined(key, value, NAMESPACES, VALUE_TYPES) is expected
    assert iri.defined(key, value) is False                                # no schema: nothing is defined


@pytest.mark.parametrize("flavor", ["pandas-object", "pandas-arrow", "polars"])
def test_flavor_defined_matches_scalar(flavor):
    keys, values = [k for k, _, _ in DEFINED_CASES], [v for _, v, _ in DEFINED_CASES]
    expected = [e for _, _, e in DEFINED_CASES]
    if flavor == "polars":
        polars = pytest.importorskip("polars")
        from triplets.iri import iri_polars
        frame = polars.DataFrame({"KEY": keys, "VALUE": values})
        assert frame.select(iri_polars.defined("KEY", "VALUE", NAMESPACES, VALUE_TYPES).alias("d"))["d"].to_list() == expected
    else:
        dtype = object if flavor == "pandas-object" else pandas.ArrowDtype(pytest.importorskip("pyarrow").string())
        got = iri_pandas.defined(pandas.Series(keys, dtype=dtype), pandas.Series(values, dtype=dtype), NAMESPACES, VALUE_TYPES)
        assert got.tolist() == expected


# ── RDF/XML reference resolution (local_resources=False) ─────────────────────

BASE = "http://iec.ch/TC57/CIM100"
RESOLVE_CASES = [
    (None, BASE, None),
    ("#ACLineSegment", BASE, BASE + "#ACLineSegment"),
    ("#_x", BASE + "#", BASE + "#_x"),                                     # base fragment replaced
    ("", BASE, BASE),
    ("http://entsoe.eu/CIM/SchemaExtension/3/1#X", BASE, "http://entsoe.eu/CIM/SchemaExtension/3/1#X"),
    ("urn:uuid:" + UUID, BASE, "urn:uuid:" + UUID),
    ("other#Y", "http://example.org/ns/doc", "http://example.org/ns/other#Y"),   # relative path join
    ("#X", None, "#X"),                                                    # no base: unchanged
]


@pytest.mark.parametrize("reference,base,expected", RESOLVE_CASES)
def test_resolve_iri(reference, base, expected):
    assert iri.resolve_iri(reference, base) == expected


# ── native mirror: the cython parser applies local_id / local_value in C++ ─────

PARSE_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:cim="http://iec.ch/TC57/CIM100#" xmlns:nc="https://cim4.eu/ns/nc#"
         xmlns:dcterms="http://purl.org/dc/terms/" xmlns:voc="https://example.org/vocab/">
  <cim:Breaker rdf:about="urn:uuid:_x">
    <cim:Equipment.EquipmentContainer rdf:resource="#_{uuid}"/>
    <cim:Switch.open>false</cim:Switch.open>
    <cim:Breaker.kind rdf:resource="http://iec.ch/TC57/CIM100#SwitchKind.breaker"/>
    <cim:Breaker.ref rdf:resource="https://cim4.eu/ns/nc#Kind.value"/>
    <cim:Breaker.uri rdf:resource="http://example.org/path/only"/>
    <cim:Breaker.frag rdf:resource="http://example.org/m#c/d"/>
    <note>free text</note>
  </cim:Breaker>
  <nc:Thing rdf:ID="_abc">
    <nc:Thing.ref rdf:resource="urn:uuid:{uuid}"/>
    <dcterms:issued>2024-01-01T00:00:00Z</dcterms:issued>
  </nc:Thing>
  <voc:Dataset rdf:about="urn:uuid:_d">
    <dcterms:conformsTo rdf:resource="http://example.org/profile/EQ/3.0"/>
  </voc:Dataset>
</rdf:RDF>
""".format(uuid=UUID)


PARSE_ENGINES = ["python_lxml_pandas", "python_lxml_arrow", "cython_pugixml_arrow", "rdf_parser.load_RDF_to_list"]
# the same document with the RDF namespace bound to another prefix: engines resolve it by namespace
PARSE_FIXTURES = {"rdf": PARSE_FIXTURE,
                  "r": PARSE_FIXTURE.replace("xmlns:rdf=", "xmlns:r=").replace("rdf:", "r:")}


def _parse(engine, text, tmp_path):
    path = tmp_path / "iri_cases.xml"
    path.write_text(text)
    if engine == "rdf_parser.load_RDF_to_list":
        from triplets import rdf_parser
        return pandas.DataFrame(rdf_parser.load_RDF_to_list(str(path)), columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    try:
        triplets.parser.get_engine(engine)
    except Exception as error:      # extension not built in this environment
        pytest.skip(f"{engine} not available: {error}")
    return triplets.parse(str(path), engine=engine, return_type="pandas")


def _expected_rows(text):
    """The object rows by the triplets.iri rules alone, from lxml's resolved QNames: ID via
    local_id, KEY via local_key, Type VALUE via local_value(kind="class"), a resource via
    local_value (the schema-free guess)."""
    from lxml import etree
    rdf = lambda name: f"{{{iri.RDF_NS}}}{name}"  # noqa: E731
    element_iri = lambda element: (etree.QName(element).namespace or "") + etree.QName(element).localname  # noqa: E731
    rows = set()
    for rdf_object in etree.fromstring(text.encode()):
        object_id = iri.local_id(rdf_object.get(rdf("ID")) or rdf_object.get(rdf("about")) or "")
        rows.add((object_id, iri.TYPE_KEY, iri.local_value(element_iri(rdf_object), "class")))
        for child in rdf_object:
            resource = child.get(rdf("resource"))
            rows.add((object_id, iri.local_key(element_iri(child)),
                      child.text if resource is None else iri.local_value(resource)))
    return rows


@pytest.mark.parametrize("prefix", PARSE_FIXTURES)
@pytest.mark.parametrize("engine", PARSE_ENGINES)
def test_parse_engines_follow_iri_rules(engine, prefix, tmp_path):
    """Every XML parser splits element QNames natively (no IRI string exists) and reads the
    RDF attributes by namespace; its object rows must equal the triplets.iri rules exactly —
    the rules read_nquads applies — for "#" and "/" namespaces, a tag without a namespace,
    and any RDF prefix."""
    text = PARSE_FIXTURES[prefix]
    frame = _parse(engine, text, tmp_path)
    meta = set(frame.loc[(frame["KEY"] == "Type") & frame["VALUE"].isin(["Distribution", "NamespaceMap"]), "ID"])
    rows = set(frame.loc[~frame["ID"].isin(meta), ["ID", "KEY", "VALUE"]].itertuples(index=False, name=None))
    expected = _expected_rows(text)
    assert {("_x", "Type", "Breaker"), ("_x", "note", "free text"), ("abc", "issued", "2024-01-01T00:00:00Z"),
            ("_d", "Type", "Dataset"), ("_x", "Breaker.kind", "SwitchKind.breaker")} <= expected
    assert rows == expected


@pytest.mark.parametrize("engine", ["python_lxml_pandas", "python_lxml_arrow"])
def test_parse_absolute_resources_resolve_against_xml_base(engine, tmp_path):
    """local_resources=False is the absolute form: ID and references resolved against
    xml:base at parse time (rdf:ID="X" → base#X), so nothing downstream needs the base."""
    from lxml import etree
    text = PARSE_FIXTURE.replace('<rdf:RDF ', '<rdf:RDF xml:base="http://example.org/base" ', 1)
    path = tmp_path / "iri_cases.xml"
    path.write_text(text)
    frame = triplets.parse(str(path), engine=engine, return_type="pandas", local_resources=False)
    rdf = lambda name: f"{{{iri.RDF_NS}}}{name}"  # noqa: E731
    base = "http://example.org/base"
    expected = set()
    for rdf_object in etree.fromstring(text.encode()):
        rdf_id = rdf_object.get(rdf("ID"))
        object_id = iri.resolve_iri("#" + rdf_id if rdf_id else rdf_object.get(rdf("about")), base)
        for child in rdf_object:
            resource = child.get(rdf("resource"))
            expected.add((object_id, iri.local_key(etree.QName(child).namespace + etree.QName(child).localname
                                                   if etree.QName(child).namespace else etree.QName(child).localname),
                          child.text if resource is None else iri.resolve_iri(resource, base)))
    rows = set(frame[["ID", "KEY", "VALUE"]].itertuples(index=False, name=None))
    assert expected <= rows
    assert {("http://example.org/base#_abc", "Thing.ref", "urn:uuid:" + UUID),
            ("urn:uuid:_x", "Equipment.EquipmentContainer", "http://example.org/base#_" + UUID)} <= expected
    assert not any(value.startswith("#") for value in frame["VALUE"]) and not any(i.startswith("#") for i in frame["ID"])


@pytest.mark.parametrize("engine", ["python_lxml_pandas", "python_lxml_arrow"])
def test_parse_absolute_without_xml_base_uses_default_base(engine, tmp_path):
    """No declared absolute xml:base: the file location is never a base — default_base
    (http://triplets# unless the caller passes one) is."""
    path = tmp_path / "iri_cases.xml"
    path.write_text(PARSE_FIXTURE)
    frame = triplets.parse(str(path), engine=engine, return_type="pandas", local_resources=False)
    assert "http://triplets#_abc" in set(frame["ID"])
    meta = set(frame.loc[(frame["KEY"] == "Type") & frame["VALUE"].isin(["Distribution", "NamespaceMap"]), "ID"])
    objects = frame[~frame["ID"].isin(meta)]
    assert not any(str(tmp_path) in text for text in objects["ID"].tolist() + objects["VALUE"].tolist())
    custom = triplets.parse(str(path), engine=engine, return_type="pandas", local_resources=False,
                            default_base="http://example.org/base")
    assert "http://example.org/base#_abc" in set(custom["ID"])


@pytest.mark.parametrize("engine", PARSE_ENGINES)
def test_xml_base_row_holds_only_a_declared_absolute_base(engine, tmp_path):
    """The NamespaceMap xml_base row is metadata the schema generator copies (ProfileXMLBase):
    a declared absolute xml:base only — never the file location, on every engine."""
    def base_rows(text):
        frame = _parse(engine, text, tmp_path)
        return frame.loc[frame["KEY"] == "xml_base", "VALUE"].tolist()
    assert base_rows(PARSE_FIXTURE) == []
    declared = PARSE_FIXTURE.replace("<rdf:RDF ", '<rdf:RDF xml:base="http://example.org/base" ', 1)
    assert base_rows(declared) == ["http://example.org/base"]
    relative = PARSE_FIXTURE.replace("<rdf:RDF ", '<rdf:RDF xml:base="relative/dir" ', 1)
    assert base_rows(relative) == []
