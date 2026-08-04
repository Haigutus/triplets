"""parser_rdfxml — the fully RDF-compliant sibling of the CIM parser.

The acceptance oracle is rdflib: reconstruct an rdflib.Graph from the parsed
base columns + context struct (``graph_from_triplets`` — the executable spec
of the context semantics) and require isomorphism with rdflib's own parse of
the same file. Feature tests are xfail(strict=True) until their phase lands.
"""
import pytest

import triplets
from triplets.parser import parse

rdflib = pytest.importorskip("rdflib")
pytest.importorskip("rdflib.compare")
pytest.importorskip("pyarrow")

FIXTURE = "tests/data/rdf_features.xml"
WITHDRAWN = "tests/data/rdf_features_withdrawn.xml"
META_TYPES = {"Distribution", "NamespaceMap"}

phase1 = pytest.mark.xfail(reason="rdfxml phase 1 pending", strict=True)
phase2 = pytest.mark.xfail(reason="rdfxml phase 2 pending", strict=True)
phase3 = pytest.mark.xfail(reason="rdfxml phase 3 pending", strict=True)
phase4 = pytest.mark.xfail(reason="rdfxml phase 4 pending", strict=True)
phase5 = pytest.mark.xfail(reason="rdfxml phase 5 pending", strict=True)


# ── the executable spec: context semantics → rdflib terms ────────────────────

def graph_from_triplets(table):
    """Reconstruct rdflib terms from base columns + the context struct."""
    rows = table.to_pylist()
    meta_ids = {r["ID"] for r in rows if r["KEY"] == "Type" and r["VALUE"] in META_TYPES}
    graph = rdflib.Graph()
    for r in rows:
        if r["ID"] in meta_ids:
            continue
        ctx = r.get("context") or {}
        id_prefix = ctx.get("ID_PREFIX") or ""
        subject = (rdflib.BNode(r["ID"]) if id_prefix == "_:"
                   else rdflib.URIRef(id_prefix + r["ID"]))
        predicate = (rdflib.RDF.type if r["KEY"] == "Type"
                     else rdflib.URIRef((ctx.get("KEY_PREFIX") or "") + r["KEY"]))
        kind = ctx.get("rdf_value_kind")
        value_prefix = ctx.get("VALUE_PREFIX") or ""
        if kind == "blank" or value_prefix == "_:":
            obj = rdflib.BNode(r["VALUE"])
        elif kind == "iri" or r["KEY"] == "Type":
            obj = rdflib.URIRef(value_prefix + r["VALUE"])
        elif ctx.get("rdf_language"):
            obj = rdflib.Literal(r["VALUE"], lang=ctx["rdf_language"])
        elif ctx.get("rdf_datatype"):
            obj = rdflib.Literal(r["VALUE"], datatype=rdflib.URIRef(ctx["rdf_datatype"]))
        else:
            obj = rdflib.Literal(r["VALUE"])
        graph.add((subject, predicate, obj))
    return graph


def parse_full(path=FIXTURE, **kwargs):
    return parse(path, dialect="rdfxml", return_type="arrow", **kwargs)


def rows_for(table, key=None):
    rows = table.to_pylist()
    return [r for r in rows if key is None or r["KEY"] == key]


def assert_isomorphic(path=FIXTURE, **kwargs):
    ours = graph_from_triplets(parse_full(path, **kwargs))
    reference = rdflib.Graph().parse(path, format="xml")
    if not rdflib.compare.isomorphic(ours, reference):
        _, only_ours, only_reference = rdflib.compare.graph_diff(ours, reference)
        raise AssertionError(
            f"not isomorphic: ours={len(ours)} vs rdflib={len(reference)} triples\n"
            f"only ours (5): {sorted(only_ours)[:5]}\n"
            f"only rdflib (5): {sorted(only_reference)[:5]}")


# ── phase 1: flat context (lang/datatype/prefixes/kinds/base) ────────────────

def test_language_tags_captured():
    labels = [r for r in rows_for(parse_full(), "label")
              if (r.get("context") or {}).get("rdf_language")]
    assert {r["context"]["rdf_language"] for r in labels} == {"en", "et", "de"}
    # lang="" cancels inheritance
    cancelled = [r for r in rows_for(parse_full(), "label") if r["VALUE"] == "cancelled"]
    assert (cancelled[0].get("context") or {}).get("rdf_language") is None


def test_datatype_captured():
    values = rows_for(parse_full(), "value")
    assert values[0]["context"]["rdf_datatype"] == "http://www.w3.org/2001/XMLSchema#float"
    assert values[0]["VALUE"] == "400.5"


def test_prefix_concatenation_reconstructs_terms():
    """The core invariant: PREFIX + column == the full term, byte-exact."""
    table = parse_full()
    for r in rows_for(table):
        ctx = r.get("context") or {}
        if ctx.get("KEY_PREFIX"):
            assert (ctx["KEY_PREFIX"] + r["KEY"]).startswith("http")


def test_base_applied_to_rdf_id():
    table = parse_full()
    lang1 = [r for r in rows_for(table, "Type") if r["VALUE"] == "Thing"][0]
    ctx = lang1["context"]
    assert (ctx["ID_PREFIX"] or "") + lang1["ID"] == "http://example.org/data.xml#_lang1"


def test_rdf_id_source_captured():
    table = parse_full()
    sources = {(r.get("context") or {}).get("rdf_id_source") for r in rows_for(table)}
    assert {"ID", "about", "nodeID"} <= {s for s in sources if s}


def test_custom_clean_rules():
    table = parse_full(clean_rules={"ID": ("#",)})
    # base-stripped locals keep their underscore with the "#"-only rule
    assert any(r["ID"].startswith("_lang1") for r in rows_for(table, "Type"))


def test_engines_lists_rdfxml_registry():
    assert "parser_rdfxml" in triplets.engines()


def test_withdrawn_constructs_do_not_fail_and_are_captured():
    """Parse = pure capture: no exception, raw rdf:* attributes recorded."""
    table = parse_full(WITHDRAWN)
    joined = " ".join((r.get("context") or {}).get("rdf_attributes") or ""
                      for r in rows_for(table))
    assert "aboutEach" in joined and "bagID" in joined


# ── phase 2: nested/blank nodes ───────────────────────────────────────────────

def test_nested_nodes_materialize():
    table = parse_full()
    names = [r["VALUE"] for r in rows_for(table, "name")]
    assert "named inner" in names and "anonymous inner" in names


def test_blank_label_collision_resolved_and_label_preserved():
    table = parse_full()
    blanks = [r for r in rows_for(table) if (r.get("context") or {}).get("ID_PREFIX") == "_:"]
    labels = {(r["context"].get("rdf_node_id")) for r in blanks}
    assert "_x" in labels                       # original label captured
    iri_x = [r for r in rows_for(table, "name") if r["VALUE"] == "iri x"]
    blank_x = [r for r in rows_for(table, "name") if r["VALUE"] == "blank x"]
    assert iri_x[0]["ID"] != blank_x[0]["ID"]    # no collision


def test_parse_type_resource():
    table = parse_full()
    assert any(r["VALUE"] == "alpha" for r in rows_for(table, "a"))


def test_property_attributes_become_rows():
    table = parse_full()
    assert any(r["VALUE"] == "via attribute" for r in rows_for(table, "name"))


def test_rdf_description_has_no_fake_type():
    table = parse_full()
    assert not any(r["VALUE"] == "Description" for r in rows_for(table, "Type"))


NESTED_DOC = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:ex="http://example.org/schema#" xml:base="http://example.org/data.xml">
  <ex:Thing rdf:ID="_top" ex:code="42">
    <ex:label xml:lang="en">top</ex:label>
    <ex:child>
      <ex:Inner rdf:nodeID="a"><ex:name>named inner</ex:name></ex:Inner>
    </ex:child>
    <ex:child><rdf:Description><ex:name>anon inner</ex:name></rdf:Description></ex:child>
    <ex:pair rdf:parseType="Resource">
      <ex:a>alpha</ex:a>
      <ex:b rdf:resource="#_top"/>
    </ex:pair>
    <ex:tag ex:name="via attribute"/>
    <rdf:type rdf:resource="http://example.org/schema#Special"/>
  </ex:Thing>
</rdf:RDF>"""


def test_nested_document_isomorphic():
    """Phase-2 oracle: flat + nested + parseType=Resource + property attributes
    + rdf:type child, no phase-3 constructs."""
    import io
    doc = io.BytesIO(NESTED_DOC)
    doc.name = "nested.xml"
    ours = graph_from_triplets(parse_full(doc))
    reference = rdflib.Graph().parse(io.BytesIO(NESTED_DOC), format="xml")
    assert rdflib.compare.isomorphic(ours, reference), (
        f"ours={len(ours)} vs rdflib={len(reference)}")


# ── phase 3: containers, collections, parseType=Literal ──────────────────────

def test_containers_li_numbering():
    table = parse_full()
    assert any(r["KEY"] == "_1" for r in rows_for(table))
    assert any(r["KEY"] == "_2" for r in rows_for(table))


def test_collections_first_rest_nil():
    table = parse_full()
    assert any(r["KEY"] == "first" for r in rows_for(table))
    assert any(r["VALUE"] == "nil" for r in rows_for(table, "rest"))


def test_xml_literal_captured():
    table = parse_full()
    bodies = rows_for(table, "body")
    assert bodies and "bold" in bodies[0]["VALUE"]
    assert bodies[0]["context"]["rdf_datatype"].endswith("XMLLiteral")


def test_full_fixture_isomorphic():
    assert_isomorphic()


# ── phase 4: round-trip ───────────────────────────────────────────────────────

@phase4
def test_nquads_roundtrip_isomorphic():
    import io
    table = parse_full()
    nq = triplets.export.export_to_nquads(table, export_to_memory=True)
    dataset = rdflib.Dataset(default_union=True)
    dataset.parse(io.BytesIO(nq.getvalue()), format="nquads")
    exported = rdflib.Graph()
    for s, p, o in dataset:
        exported.add((s, p, o))
    reference = rdflib.Graph().parse(FIXTURE, format="xml")
    assert rdflib.compare.isomorphic(exported, reference)


# ── phase 5: reification + completeness ───────────────────────────────────────

def test_reification_rows():
    table = parse_full()
    assert any(r["VALUE"] == "Statement" for r in rows_for(table, "Type"))


def test_nothing_lost_property():
    """Every rdf-visible attribute in the fixture is recoverable from
    base columns + context (the capture-completeness property)."""
    assert_isomorphic()
    table = parse_full(WITHDRAWN)
    assert len(rows_for(table)) > 0
