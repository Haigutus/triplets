"""Tests for the SHACL IR compiler (triplets.validation.shacl_ir)."""
import os
import importlib.util

import pytest

pytest.importorskip("rdflib")

import triplets

from pathlib import Path

from triplets.validation import compile as compile_shapes
from triplets.validation.shacl_ir import IR_COLUMNS, KNOWN_COMPONENTS, _COMPILE_CACHE

# One shape exercising most IR components: scalars, RDF lists, inverse path,
# logical operator, node-level closed, sparql constraint.
RICH_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:BreakerShape a sh:NodeShape ;
    sh:targetClass cim:Breaker ;
    sh:closed true ;
    sh:ignoredProperties ( cim:IdentifiedObject.mRID ) ;
    sh:property [
        sh:path cim:IdentifiedObject.name ;
        sh:name "name-required" ;
        sh:message "Breaker must have exactly one name" ;
        sh:severity sh:Violation ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:minLength 1 ; sh:maxLength 128 ;
        sh:pattern "^[^\\\\s].*" ;
        sh:datatype xsd:string ;
    ] ;
    sh:property [
        sh:path cim:Switch.normalOpen ;
        sh:datatype xsd:boolean ;
        sh:in ( true false ) ;
    ] ;
    sh:property [
        sh:path cim:Equipment.EquipmentContainer ;
        sh:class cim:VoltageLevel ;
        sh:nodeKind sh:IRI ;
    ] ;
    sh:property [
        sh:path [ sh:inversePath cim:Terminal.ConductingEquipment ] ;
        sh:minCount 1 ;
        sh:severity sh:Warning ;
    ] ;
    sh:property [
        sh:or ( [ sh:path cim:IdentifiedObject.description ; sh:minLength 1 ]
                [ sh:path cim:IdentifiedObject.name ; sh:minLength 1 ] ) ;
    ] ;
    sh:sparql [
        sh:message "custom sparql message" ;
        sh:prefixes <http://example.org/prefixes> ;
        sh:select "SELECT ?this WHERE { ?this ?p ?o }" ;
    ] .

<http://example.org/prefixes>
    sh:declare [ sh:prefix "cim" ; sh:namespace "http://iec.ch/TC57/CIM100#"^^xsd:anyURI ] .
"""

CGMES_SHACL_DIR = Path(os.environ.get(
    "TRIPLETS_CGMES_SHACL",
    "test_data/CGMES-AP/CGMES/SHACL"))
# the ENTSO-E profiles split constraints: Simple carries datatype/nodeKind,
# Complex carries sparql/range/cardinality — real validation uses both
CGMES_EQ_SHACL_FILES = [
    CGMES_SHACL_DIR / "61970-600-2_Equipment-AP-Con-Simple-SHACL.ttl",
    CGMES_SHACL_DIR / "61970-301_Equipment-AP-Con-Complex-SHACL.ttl",
]


@pytest.fixture()
def shape_file(tmp_path):
    path = tmp_path / "rich.ttl"
    path.write_text(RICH_SHAPE)
    return str(path)


def test_ir_columns_and_components(shape_file):
    ir = compile_shapes(shape_file).ir
    assert list(ir.columns) == IR_COLUMNS
    assert set(ir["component"]) <= KNOWN_COMPONENTS
    assert (ir["target_on"] == "Breaker").all()

    by_component = ir.set_index("component")
    # closed params = compile-time allowed list: ignoredProperties + this shape's
    # DIRECT property paths (inverse paths and paths nested in sh:or don't count)
    assert set(by_component.loc["sh:closed", "params"]) == {
        "IdentifiedObject.mRID", "IdentifiedObject.name", "Switch.normalOpen",
        "Equipment.EquipmentContainer"}
    assert by_component.loc["sh:in", "params"] == ["true", "false"]
    assert by_component.loc["sh:class", "params"] == "VoltageLevel"
    assert sorted(by_component.loc["sh:datatype", "params"]) == ["xsd:boolean", "xsd:string"]
    sparql = by_component.loc["sh:sparql", "params"]
    assert sparql["select"].startswith("SELECT ?this")
    assert sparql["prefixes"] == "PREFIX cim: <http://iec.ch/TC57/CIM100#>\n"
    assert sparql["path"] is None                       # node-level constraint has no sh:path
    assert by_component.loc["sh:sparql", "message"] == "custom sparql message"


def test_ir_shape_metadata(shape_file):
    ir = compile_shapes(shape_file).ir
    name_rows = ir[ir["path"] == "IdentifiedObject.name"]
    assert (name_rows["name"] == "name-required").all()
    assert (name_rows["message"] == "Breaker must have exactly one name").all()
    # one row per component on the same property shape
    assert set(name_rows["component"]) == {"sh:minCount", "sh:maxCount", "sh:minLength",
                                           "sh:maxLength", "sh:pattern", "sh:datatype"}


def test_ir_inverse_path_and_severity(shape_file):
    ir = compile_shapes(shape_file).ir
    inverse = ir[ir["inverse"]]
    assert list(inverse["path"]) == ["Terminal.ConductingEquipment"]
    assert list(inverse["severity"]) == ["Warning"]
    assert (ir.loc[~ir["inverse"], "severity"] == "Violation").all()  # declared or default


def test_ir_logical_operator_nesting(shape_file):
    ir = compile_shapes(shape_file).ir
    or_params = ir.set_index("component").loc["sh:or", "params"]
    assert len(or_params) == 2                       # two alternatives
    first_alternative = or_params[0]
    assert first_alternative[0]["path"] == "IdentifiedObject.description"
    assert first_alternative[0]["component"] == "sh:minLength"


def test_compile_cache_hits_by_content(shape_file, tmp_path):
    compiled = compile_shapes(shape_file)
    assert compile_shapes(shape_file) is compiled          # same path → cache hit

    copy = tmp_path / "copy.ttl"                           # same content, other path
    copy.write_text(RICH_SHAPE)
    assert compile_shapes(str(copy)) is compiled

    assert compiled.hash in _COMPILE_CACHE
    assert compiled.plans == {}                            # engines fill this lazily


@pytest.mark.skipif(not all(f.exists() for f in CGMES_EQ_SHACL_FILES),
                    reason="external CGMES SHACL shapes not available")
def test_ir_real_cgmes_eq_shapes():
    """The real CGMES Equipment SHACL profiles compile to a non-trivial IR."""
    ir = compile_shapes([str(f) for f in CGMES_EQ_SHACL_FILES]).ir
    assert len(ir) > 500
    assert {"sh:minCount", "sh:datatype", "sh:nodeKind", "sh:sparql", "sh:minExclusive"} <= set(ir["component"])
    unknown = set(ir["component"]) - KNOWN_COMPONENTS
    assert not unknown, f"unexpected components in real shapes: {unknown}"
    # the Complex file's SPARQL target (PowerTransformer-twoWinding) compiles
    assert "SPARQL" in set(ir["target_by"])
    assert not [entry for entry in compile_shapes([str(f) for f in CGMES_EQ_SHACL_FILES]).stats["skipped_shapes"]
                if "sh:target not walked" in entry]


def test_target_node_and_objects_of_compile(tmp_path):
    """sh:targetNode → target_by="ID" (the ID as the data spells it), sh:targetObjectsOf
    → target_by="VALUE" (the KEY); one row per target."""
    path = tmp_path / "targets.ttl"
    path.write_text("""
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:Picked a sh:NodeShape ;
    sh:targetNode <urn:uuid:11111111-2222-3333-4444-555555555555>, cim:IDuniqueness ;
    sh:targetObjectsOf cim:Equipment.EquipmentContainer ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
""")
    compiled = compile_shapes(str(path))
    assert set(zip(compiled.ir["target_by"], compiled.ir["target_on"])) == {
        ("ID", "11111111-2222-3333-4444-555555555555"), ("ID", "http://iec.ch/TC57/CIM100#IDuniqueness"),
        ("VALUE", "Equipment.EquipmentContainer")}
    assert compiled.stats["skipped_shapes"] == []


def test_not_walked_declarations_warn_at_compile(caplog, tmp_path):
    """Shapes reached only through declarations the IR does not walk (custom
    sh:target, sh:rule) must warn and land in skipped_shapes — the vectorized
    engines would otherwise silently under-validate."""
    import logging
    path = tmp_path / "invisible.ttl"
    path.write_text("""
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix ex: <http://example.org/> .

cim:Custom a sh:NodeShape ;
    sh:target ex:NotSparql ;
    sh:rule [ a sh:SPARQLRule ; sh:construct "CONSTRUCT { $this cim:x 1 } WHERE { $this a cim:Breaker }" ] ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
""")
    with caplog.at_level(logging.WARNING, logger="triplets.validation.shacl_ir"):
        compiled = compile_shapes(str(path))
    assert len(compiled.ir) == 0                           # invisible to the IR
    assert any("sh:target" in record.getMessage()
               for record in caplog.records if record.levelname == "WARNING")
    assert compiled.stats["skipped_shapes"] == ["http://iec.ch/TC57/CIM100#Custom: sh:rule not applied",
                                                "http://iec.ch/TC57/CIM100#Custom: sh:target not walked (no sh:select)"]


SPARQL_TARGET = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim: sh:declare [ sh:prefix "rdf" ; sh:namespace "http://www.w3.org/1999/02/22-rdf-syntax-ns#"^^xsd:anyURI ] ,
                [ sh:prefix "cim" ; sh:namespace "http://iec.ch/TC57/CIM100#"^^xsd:anyURI ] .
cim:Breakers a sh:NodeShape ;
    {targets}
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
"""
SELECT_BREAKERS = "SELECT ?this WHERE { ?this rdf:type cim:Breaker }"


def test_sparql_target_compiles(tmp_path):
    """sh:target with sh:select → target_by="SPARQL", target_on the executable
    query: the sh:prefixes declarations (sorted) + the sh:select text."""
    path = tmp_path / "sparql_target.ttl"
    path.write_text(SPARQL_TARGET.replace(
        "{targets}", f'sh:target [ a sh:SPARQLTarget ; sh:prefixes cim: ; sh:select "{SELECT_BREAKERS}" ] ;'))
    compiled = compile_shapes(str(path))
    assert list(zip(compiled.ir["target_by"], compiled.ir["target_on"])) == [
        ("SPARQL", "PREFIX cim: <http://iec.ch/TC57/CIM100#>\n"
                   "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n" + SELECT_BREAKERS)]
    assert compiled.stats["skipped_shapes"] == []


def test_sparql_target_next_to_target_class(tmp_path):
    """Rows per target; switching the NodeShape off drops both."""
    path = tmp_path / "two_targets.ttl"
    path.write_text(SPARQL_TARGET.replace(
        "{targets}", f'sh:targetClass cim:Breaker ; sh:target [ sh:prefixes cim: ; sh:select "{SELECT_BREAKERS}" ] ;'))
    compiled = compile_shapes(str(path))
    assert sorted(compiled.ir["target_by"]) == ["SPARQL", "Type"]
    assert len(compiled.active(["http://iec.ch/TC57/CIM100#Breakers"]).ir) == 0


def test_component_registries_agree():
    """The stringly-typed component keys live in several registries — they must
    describe the same universe: pandas is complete; polars/duckdb + the shared
    fallback set cover everything; pyshacl's report vocabulary maps onto it."""
    from triplets.validation import shacl_ir, shacl_pandas, shacl_report

    known = set(shacl_ir.KNOWN_COMPONENTS)
    assert set(shacl_pandas.CONSTRAINT_VALIDATORS) == known
    assert shacl_ir.FALLBACK_COMPONENTS <= known
    # pyshacl's report vocabulary covers the SHACL components, plus the ones only
    # pyshacl evaluates; triplets:range and triplets:missing are schema-validation-only
    # (never emitted by pyshacl)
    pyshacl_only = {"sh:qualifiedMinCount", "sh:qualifiedMaxCount", "sh:qualifiedValueShape",
                    "sh:uniqueLang", "sh:languageIn"}
    assert set(shacl_report._COMPONENT_MAP.values()) - pyshacl_only == known - {"triplets:range", "triplets:missing"}

    if importlib.util.find_spec("polars"):
        from triplets.validation import shacl_polars
        assert set(shacl_polars.PLAN_BUILDERS) | shacl_ir.FALLBACK_COMPONENTS == known
        assert set(shacl_polars.BATCH_BUILDERS) <= set(shacl_polars.PLAN_BUILDERS)
    if importlib.util.find_spec("duckdb"):
        from triplets.validation import shacl_duckdb
        assert set(shacl_duckdb.SQL_BUILDERS) | shacl_ir.FALLBACK_COMPONENTS == known


def test_logical_operator_cycle_dropped(caplog):
    """Mutually recursive sh:or shapes compile with a warning instead of
    RecursionError (sh:node already had this guard; sh:or/and/not gained it)."""
    import io
    shapes = io.StringIO("""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix ex: <http://example.org/> .
        ex:AShape a sh:NodeShape ; sh:targetClass ex:Thing ;
            sh:property ex:P1 .
        ex:P1 sh:path ex:name ; sh:or ( ex:P2 ) .
        ex:P2 sh:path ex:name ; sh:or ( ex:P1 ) .
    """)
    import rdflib
    graph = rdflib.Graph().parse(shapes, format="turtle")
    from triplets.validation.shacl_ir import parse_ir
    with caplog.at_level("WARNING"):
        ir = parse_ir(graph)
    assert any("cycle" in record.message for record in caplog.records)
    assert (ir["component"] == "sh:or").any()          # the outer constraint survives


# ── sh:deactivated: every shape compiles, active() filters ───────────────────

CIM = "http://iec.ch/TC57/CIM100#"


def _graph(turtle):
    import rdflib
    return rdflib.Graph().parse(data=f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix cim: <{CIM}> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix md: <http://iec.ch/TC57/61970-552/ModelDescription/1#> .
        {turtle}""", format="turtle")


@pytest.mark.parametrize("flag, off", [
    ("true", True),
    ('"false"^^xsd:boolean', False),
    ('"false"', True),   # pyshacl tests bool(literal.value): a plain "false" deactivates too
    ('"abc"^^xsd:integer', False),   # ill-typed: no value
])
def test_deactivated_shapes_compile_and_filter(flag, off):
    from triplets.validation.shacl_ir import parse_ir
    graph = _graph(f"""cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:deactivated {flag} ;
        sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .""")
    assert len(parse_ir(graph)) == 1
    compiled = compile_shapes(graph)
    assert compiled.deactivated == ({CIM + "BreakerShape"} if off else frozenset())
    assert compiled.active().ir.empty is off
    assert compiled.active().stats["node_shapes"] == int(not off)
    assert (compiled.active() is compiled) is not off
    assert compiled.active([]).ir.equals(compiled.ir)   # [] runs every shape, the declared ones included
    assert (compiled.active([]) is compiled) is not off


def test_schema_compile_has_no_shapes_to_switch_off():
    from triplets.validation.schema_ir import compile_schema
    rdf_map = {"Breaker": {"type": "Class", "parameters": ["IdentifiedObject.name"]},
               "IdentifiedObject.name": {"type": "Attribute", "xsd:type": "xsd:string", "xsd:minOccours": "1"}}
    compiled = compile_schema(rdf_map)
    assert compiled.active() is compiled
    with pytest.raises(ValueError, match="schema compile"):
        compiled.active(["urn:x"])


NESTED = """cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:property cim:AliasShape, cim:ContainerShape .
    cim:AliasShape sh:path cim:IdentifiedObject.aliasName ; sh:or ( cim:M [ sh:datatype xsd:boolean ] ) .
    cim:M sh:datatype xsd:integer .
    cim:ContainerShape sh:path cim:Equipment.EquipmentContainer ; sh:node cim:T .
    cim:T a sh:NodeShape ; sh:property cim:TName .
    cim:TName sh:path cim:IdentifiedObject.name ; sh:minCount 1 ."""


def test_ir_rows_carry_their_shape_chain():
    """shapes: the shape ids whose switch-off removes the row, outermost first."""
    breaker, alias, container = (CIM + name for name in ("BreakerShape", "AliasShape", "ContainerShape"))
    by_component = compile_shapes(_graph(NESTED)).ir.set_index("component")
    assert by_component.loc["sh:or", "shapes"] == (breaker, alias)
    first, second = by_component.loc["sh:or", "params"]
    assert first[0]["shapes"] == (breaker, alias, CIM + "M")
    assert second[0]["shapes"][:2] == (breaker, alias) and len(second[0]["shapes"]) == 3
    assert by_component.loc["sh:node", "shapes"] == (breaker, container)
    assert by_component.loc["sh:node", "params"]["rows"][0]["shapes"] == (breaker, container, CIM + "T", CIM + "TName")


def test_active_prunes_nested_rows():
    """A switched-off member or sh:node target empties; the operator row stays."""
    compiled = compile_shapes(_graph(NESTED))
    active = compiled.active([CIM + "M", CIM + "T"])
    by_component = active.ir.set_index("component")
    first, second = by_component.loc["sh:or", "params"]
    assert first == [] and len(second) == 1
    assert by_component.loc["sh:node", "params"]["rows"] == []
    assert len(compiled.ir.set_index("component").loc["sh:or", "params"][0]) == 1   # the base keeps them
    assert active.active() is active                    # None keeps a filtered compile's own set
    assert "sh:or" not in set(compiled.active([CIM + "AliasShape"]).ir["component"])


def test_unknown_deactivated_shape_warns(caplog):
    compiled = compile_shapes(_graph(NESTED))
    with caplog.at_level("WARNING", logger="triplets.validation.shacl_ir"):
        active = compiled.active(["http://example.org/Typo", CIM + "M"])
    assert "http://example.org/Typo" in caplog.text and CIM + "M" not in caplog.text
    assert len(active.ir) == len(compiled.ir)


# ── per-file graph cache ──────────────────────────────────────────────────────

SHARED_TTL = """@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:SharedShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
"""
OTHER_TTL = """@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:OtherShape a sh:NodeShape ; sh:targetClass cim:Switch ;
    sh:property [ sh:path cim:Switch.normalOpen ; sh:maxCount 1 ] .
"""


def _shape_files(tmp_path):
    shared = tmp_path / "shared.ttl"
    shared.write_text(SHARED_TTL)
    other = tmp_path / "other.ttl"
    other.write_text(OTHER_TTL)
    return shared, other


def test_unions_share_per_file_parses(tmp_path, monkeypatch):
    """Two unions sharing a member file parse it once — the per-file graph
    cache is what makes many overlapping shape unions cheap."""
    import rdflib
    import triplets
    from triplets.validation import shacl_ir

    triplets.clear_caches()
    shared, other = _shape_files(tmp_path)
    parsed = []
    original = rdflib.Graph.parse

    def counting_parse(self, source=None, *args, **kwargs):
        parsed.append(str(source))
        return original(self, source, *args, **kwargs)

    monkeypatch.setattr(rdflib.Graph, "parse", counting_parse)
    shacl_ir.compile_shapes([str(shared)])
    shacl_ir.compile_shapes([str(shared), str(other)])   # shared.ttl must not re-parse
    assert parsed.count(str(shared)) == 1
    assert parsed.count(str(other)) == 1


def test_union_key_ignores_order_and_duplicates(tmp_path):
    import triplets
    from triplets.validation import shacl_ir

    triplets.clear_caches()
    shared, other = _shape_files(tmp_path)
    forward = shacl_ir.compile_shapes([str(shared), str(other)])
    backward = shacl_ir.compile_shapes([str(other), str(shared), str(shared)])
    assert forward is backward                            # same cache entry


def test_cached_graphs_survive_compiled_graph_mutation(tmp_path):
    """pyshacl (advanced=True) may add triples to compiled.graph — that must
    not leak into other unions sharing the cached per-file graphs."""
    import rdflib
    import triplets
    from triplets.validation import shacl_ir

    triplets.clear_caches()
    shared, other = _shape_files(tmp_path)
    first = shacl_ir.compile_shapes([str(shared)])
    first.graph.add((rdflib.URIRef("urn:x"), rdflib.URIRef("urn:y"), rdflib.URIRef("urn:z")))
    second = shacl_ir.compile_shapes([str(shared), str(other)])
    assert (rdflib.URIRef("urn:x"), rdflib.URIRef("urn:y"), rdflib.URIRef("urn:z")) \
        not in second.graph


def test_node_level_logical_operators_compile():
    """sh:or / sh:and / sh:not on the NodeShape itself judge its focus nodes."""
    compiled = compile_shapes(_graph("""
cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:or ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]
            [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) ;
    sh:not [ sh:path cim:Switch.state ; sh:hasValue "banned" ] ."""))
    rows = compiled.ir.set_index("component")
    assert set(rows.index) == {"sh:or", "sh:not"}
    assert rows["path"].isna().all()                                # focus-level
    assert [member[0]["path"] for member in rows.loc["sh:or", "params"]] == [
        "IdentifiedObject.name", "IdentifiedObject.description"]
    assert compiled.stats["skipped_shapes"] == []


def test_member_with_property_shapes_expands():
    """A NodeShape used as a logical member: its sh:property rows judge the same
    focus at node level, and the value nodes (a sh:node-style row) under a path."""
    compiled = compile_shapes(_graph("""
cim:Named a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:not cim:Named ;
    sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:or ( cim:Named [ sh:hasValue "x" ] ) ] ."""))
    rows = compiled.ir.set_index("component")
    negated = rows.loc["sh:not", "params"]
    assert [(row["component"], row["path"]) for row in negated] == [("sh:minCount", "IdentifiedObject.name")]
    first, second = rows.loc["sh:or", "params"]
    assert [(row["component"], row["path"]) for row in first] == [("sh:node", "Equipment.EquipmentContainer")]
    assert first[0]["params"]["shape"] == "Named"
    assert [(row["component"], row["path"]) for row in first[0]["params"]["rows"]] == [("sh:minCount", "IdentifiedObject.name")]
    assert [row["component"] for row in second] == ["sh:hasValue"]


def test_unknown_components_are_rows_and_reported():
    """Terms no engine implements compile to rows with params None (top level and
    nested), so the run metadata can name them."""
    compiled = compile_shapes(_graph("""
cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.name ;
        sh:qualifiedValueShape [ sh:hasValue "n" ] ; sh:qualifiedMinCount 1 ] ;
    sh:property [ sh:path cim:IdentifiedObject.description ;
        sh:and ( [ sh:uniqueLang true ] ) ] ."""))
    rows = compiled.ir.set_index("component")
    assert rows.loc["sh:qualifiedValueShape", "params"] is None
    assert compiled.stats["unknown_components"] == ["sh:qualifiedMinCount", "sh:qualifiedValueShape"]
    assert "sh:and" not in rows.index                                # its member is not expressible: skipped, named
    assert [entry[entry.index("sh:and"):] for entry in compiled.stats["skipped_shapes"]] == [
        "sh:and member %s uses sh:uniqueLang — not evaluated" % entry_member(compiled)]


def entry_member(compiled):
    """The blank-node id of the one sh:and member in *compiled*'s graph."""
    import rdflib
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    head = next(compiled.graph.objects(None, SH["and"]))
    return str(compiled.graph.value(head, rdflib.RDF.first))


def test_logical_member_with_unknown_component_drops_the_operator(caplog):
    """sh:or ( [ sh:not <shape with sh:qualifiedValueShape> ] [...] ): judged on
    the member's expressible part, sh:not [] would fire on every focus node —
    the operator is skipped and named instead."""
    with caplog.at_level("WARNING", logger="triplets.validation.shacl_ir"):
        compiled = compile_shapes(_graph("""
cim:Q a sh:NodeShape ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:qualifiedValueShape [ sh:hasValue "n" ] ; sh:qualifiedMinCount 1 ] .
cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:or ( [ sh:not cim:Q ] [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:maxCount 1 ] ."""))
    assert list(compiled.ir["component"]) == ["sh:maxCount"]        # the sibling still runs
    outer, inner = sorted(compiled.stats["skipped_shapes"])       # the member is a blank node
    assert inner.endswith("sh:not member http://iec.ch/TC57/CIM100#Q uses sh:qualifiedMinCount, "
                          "sh:qualifiedValueShape — not evaluated")
    assert outer.startswith("http://iec.ch/TC57/CIM100#N: sh:or member ")
    assert outer.endswith(" holds a part that was not evaluated")
    assert any("not evaluated" in record.getMessage() for record in caplog.records)


def test_logical_member_without_shape_is_skipped():
    """A bare IRI in the member list (no shape triples) cannot be judged."""
    compiled = compile_shapes(_graph("""
cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ;
    sh:or ( md:FullModel md:DifferenceModel ) ."""))
    assert list(compiled.ir["component"]) == ["sh:minCount"]
    assert compiled.stats["skipped_shapes"] == [
        "http://iec.ch/TC57/CIM100#N: sh:or member http://iec.ch/TC57/61970-552/ModelDescription/1#DifferenceModel has no shape — not evaluated",
        "http://iec.ch/TC57/CIM100#N: sh:or member http://iec.ch/TC57/61970-552/ModelDescription/1#FullModel has no shape — not evaluated"]


def test_sh_node_expands_closed_and_logical_operators():
    """A shape referenced through sh:node is walked like a targeted one: its
    sh:closed and node-level operators judge the referenced value nodes."""
    compiled = compile_shapes(_graph("""
cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:C ] .
cim:C a sh:NodeShape ; sh:closed true ;
    sh:property [ sh:path cim:IdentifiedObject.name ] ;
    sh:not [ sh:path cim:IdentifiedObject.name ; sh:hasValue "banned" ] ."""))
    nested = compiled.ir.iloc[0]["params"]["rows"]
    assert {row["component"] for row in nested} == {"sh:closed", "sh:not"}
    assert next(row["params"] for row in nested if row["component"] == "sh:closed") == ["IdentifiedObject.name"]


def test_node_level_value_constraints_are_skipped():
    """sh:in / sh:class on a node shape judge the focus node itself: no row, an
    entry in skipped_shapes, and an operator over such a member is dropped."""
    compiled = compile_shapes(_graph("""
cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:in ( cim:x ) ;
    sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:T ] ;
    sh:or ( [ sh:class cim:Breaker ] [ sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ] ) .
cim:T a sh:NodeShape ; sh:class cim:VoltageLevel ."""))
    assert [(row["component"], row["params"]["rows"]) for row in compiled.ir.to_dict("records")] == [("sh:node", [])]
    skipped = compiled.stats["skipped_shapes"]
    assert CIM + "N: node-level sh:in not evaluated" in skipped
    assert CIM + "T: node-level sh:class not evaluated" in skipped
    assert any(entry.startswith(CIM + "N: sh:or member ") and "holds a part that was not evaluated" in entry for entry in skipped)


def test_skipped_shapes_follow_the_switch_off():
    """skipped_shapes entries carry their lineage: switching off the node shape
    above an inexpressible path, or the shape holding a dropped operator, takes
    the entry along — and only that entry."""
    compiled = compile_shapes(_graph("""
cim:A a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:Deep .
cim:B a sh:NodeShape ; sh:targetClass cim:Disconnector ; sh:property cim:Deep .
cim:Deep sh:path ( cim:a cim:b cim:c ) ; sh:minCount 1 .
cim:C a sh:NodeShape ; sh:targetClass cim:Switch ; sh:or ( md:FullModel ) ."""))
    deep = CIM + "Deep: unsupported sh:path form"
    bare = CIM + "C: sh:or member http://iec.ch/TC57/61970-552/ModelDescription/1#FullModel has no shape — not evaluated"
    assert compiled.stats["skipped_shapes"] == [bare, deep]
    assert compiled.active([CIM + "A"]).stats["skipped_shapes"] == [bare, deep]   # still reached through B
    assert compiled.active([CIM + "A", CIM + "B"]).stats["skipped_shapes"] == [bare]
    assert compiled.active([CIM + "C"]).stats["skipped_shapes"] == [deep]


def test_xone_compiles_like_or():
    compiled = compile_shapes(_graph("""cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
        sh:xone ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] cim:M ) .
        cim:M sh:path cim:IdentifiedObject.description ; sh:minCount 1 ."""))
    (row,) = compiled.ir.to_dict("records")
    assert (row["component"], row["path"]) == ("sh:xone", None)
    assert [[(member["component"], member["path"]) for member in alternative] for alternative in row["params"]] == [
        [("sh:minCount", "IdentifiedObject.name")], [("sh:minCount", "IdentifiedObject.description")]]
    assert compiled.active([CIM + "M"]).ir["params"][0][1] == []     # a switched-off member empties
    assert compiled.stats["skipped_shapes"] == []



def test_every_parameter_value_compiles_to_a_row():
    """Each value of a repeatable parameter is a constraint of its own (#163)."""
    compiled = compile_shapes(_graph("""
cim:Named a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
cim:Desc a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .
cim:N a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:node cim:Named, cim:Desc ;
    sh:or ( cim:Named ), ( cim:Desc ) ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:pattern "^B", "1$" ] ."""))
    rows = compiled.ir
    assert sorted(params["shape"] for params in rows.loc[rows["component"] == "sh:node", "params"]) == ["Desc", "Named"]
    assert sorted(params[0][0]["path"] for params in rows.loc[rows["component"] == "sh:or", "params"]) == [
        "IdentifiedObject.description", "IdentifiedObject.name"]
    assert sorted(rows.loc[rows["component"] == "sh:pattern", "params"]) == ["1$", "^B"]

CGMES_DY_COMPLEX = CGMES_SHACL_DIR / "61970-302_Dynamics-AP-Con-Complex-SHACL.ttl"


@pytest.mark.skipif(not CGMES_DY_COMPLEX.exists(), reason="CGMES-AP submodule not checked out")
def test_dynamics_shapes_compile_xone():
    """CGMES-AP Dynamics: the two sh:xone shapes compile, nothing unknown."""
    compiled = compile_shapes(str(CGMES_DY_COMPLEX))
    xone = compiled.ir[compiled.ir["component"] == "sh:xone"]
    assert xone["shape_id"].nunique() == 2
    assert len(xone) == 35                                            # one row per sh:targetClass
    assert xone["path"].isna().all()
    assert compiled.stats["unknown_components"] == []
    assert not any("xone" in entry for entry in compiled.stats["skipped_shapes"])
