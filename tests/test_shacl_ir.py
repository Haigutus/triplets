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
    "/home/kvilgo/GIT/application-profiles-library/CGMES/CurrentRelease/SHACL"))
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
    assert (ir["target_class"] == "Breaker").all()

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


def test_invisible_targets_warn_at_compile(caplog, tmp_path):
    """Shapes reached only through targets the IR does not walk must warn —
    the vectorized engines would otherwise silently under-validate."""
    import logging
    path = tmp_path / "invisible.ttl"
    path.write_text("""
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:PickedNodeShape a sh:NodeShape ;
    sh:targetNode <urn:uuid:11111111-2222-3333-4444-555555555555> ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
""")
    with caplog.at_level(logging.WARNING, logger="triplets.validation.shacl_ir"):
        compiled = compile_shapes(str(path))
    assert len(compiled.ir) == 0                           # invisible to the IR
    assert any("sh:targetNode" in record.getMessage()
               for record in caplog.records if record.levelname == "WARNING")


def test_component_registries_agree():
    """The stringly-typed component keys live in several registries — they must
    describe the same universe: pandas is complete; polars/duckdb + the shared
    fallback set cover everything; pyshacl's report vocabulary maps onto it."""
    from triplets.validation import shacl_ir, shacl_pandas, shacl_report

    known = set(shacl_ir.KNOWN_COMPONENTS)
    assert set(shacl_pandas.CONSTRAINT_VALIDATORS) == known
    assert shacl_ir.FALLBACK_COMPONENTS <= known
    assert set(shacl_report._COMPONENT_MAP.values()) == known

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
