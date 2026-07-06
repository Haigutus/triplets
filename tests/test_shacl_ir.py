"""Tests for the SHACL IR compiler (triplets.validation.shacl_ir)."""
import os

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
    assert by_component.loc["sh:datatype", "params"].tolist() == ["xsd:string", "xsd:boolean"]
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
