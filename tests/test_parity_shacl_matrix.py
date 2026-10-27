"""SHACL parity matrix: every case on pyshacl (reference) and the pandas / polars /
duckdb engines, whole canonical rows compared (ID, KEY, VALUE, VIOLATION_TYPE,
SEVERITY, SOURCE_SHAPE, authored MESSAGE). One allowance: the vectorized engines'
triplets:lexicalForm Warnings. Known divergences are strict xfails per (case, engine)
in XFAIL; cases the engines deliberately skip are the SKIPPED group.

Adding a case: one CASES entry — (shape Turtle appended to PREFIX, triplet rows) or
(shape, rows, validate_shacl kwargs).
"""
import functools

import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

import pandas
import triplets

from triplets.export_schema import schemas

from pyshacl.errors import ConstraintLoadError, ShapeLoadError

from _parity import canon_violations

ENGINES = ["pandas", "polars", "duckdb"]
CIM = "http://iec.ch/TC57/CIM100#"
PREFIX = """@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
cim: sh:declare [ sh:prefix "cim" ; sh:namespace "http://iec.ch/TC57/CIM100#"^^xsd:anyURI ] ,
                [ sh:prefix "rdf" ; sh:namespace "http://www.w3.org/1999/02/22-rdf-syntax-ns#"^^xsd:anyURI ] .
"""


def attribute(xsd_type):
    return {"type": "Attribute", "xsd:type": xsd_type, "namespace": CIM}


def klass(name, *ancestors):
    return {"type": "Class", "namespace": CIM, "inheritance": [CIM + name, *(CIM + a for a in ancestors)]}


# one toy schema for every case: typed literals, associations, an enum, a class tree
SCHEMA = {"EQ": {
    "Breaker": klass("Breaker", "Switch", "Equipment"),
    "Bay": klass("Bay", "EquipmentContainer"),
    "Substation": klass("Substation", "EquipmentContainer"),
    "IdentifiedObject.name": attribute("xsd:string"),
    "IdentifiedObject.description": attribute("xsd:string"),
    "IdentifiedObject.aliasName": attribute("xsd:string"),
    "Conductor.length": attribute("xsd:float"),
    "Switch.ratedCurrent": attribute("xsd:float"),
    "TransformerEnd.endNumber": attribute("xsd:integer"),
    "Switch.normalOpen": attribute("xsd:boolean"),
    "Equipment.EquipmentContainer": {"type": "Association"},
    "Bay.VoltageLevel": {"type": "Association"},
    "Switch.kind": {"type": "Enumeration", "xsd:type": "xsd:anyURI", "namespace": CIM},
}}


def obj(object_id, type_name, *properties, instance="eq"):
    return [(object_id, "Type", type_name, instance)] + [(object_id, key, value, instance) for key, value in properties]


def breaker(object_id, *properties, instance="eq"):
    return obj(object_id, "Breaker", *properties, instance=instance)


NAME, DESC, ALIAS, CONT, LEN, CUR = ("IdentifiedObject.name", "IdentifiedObject.description", "IdentifiedObject.aliasName",
                                     "Equipment.EquipmentContainer", "Conductor.length", "Switch.ratedCurrent")

CONTAINERS = obj("bay1", "Bay", (NAME, "Bay 1")) + obj("sub1", "Substation")
# named / described / bare breakers: b1 → bay1, b2 → sub1, b3 without container
TRIO = (breaker("b1", (NAME, "B1"), (CONT, "bay1")) + breaker("b2", (DESC, "d"), (CONT, "sub1"))
        + breaker("b3") + CONTAINERS)
# breakers referencing each other (the #163 / #166 data)
RING = breaker("b1", (NAME, "n"), (CONT, "b2")) + breaker("b2", (CONT, "b1")) + breaker("b3", (DESC, "d"))

NAMED = "cim:Named a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."
DESCRIBED = "cim:Desc a sh:NodeShape ; sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ."
BOTH = """cim:Both a sh:NodeShape ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ;
    sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ."""
B = "cim:B a sh:NodeShape ; sh:targetClass cim:Breaker ;"
NAME_MIN = "sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ."
DESC_MIN = "sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ]"


def prop(body):
    return f"{B} sh:property [ {body} ] ."


def deactivated(shape):
    return shape.replace("sh:NodeShape ;", "sh:NodeShape ; sh:deactivated true ;", 1)


def sparql_target(select, body):
    return f'cim:B a sh:NodeShape ; sh:target [ a sh:SPARQLTarget ; sh:prefixes cim: ; sh:select """{select}""" ] ; {body}'


# the CGMES 3.0 export schema and UUID ids, as real data is validated
ENTSOE = {"rdf_map": schemas.ENTSOE_CGMES_3_0_0_552_ED1, "rdf_map_scope": "*"}
A1, A2, VL, SUB = (f"{n:08d}-0000-0000-0000-000000000000" for n in (1, 2, 71, 72))
SEGMENTS = (obj(A1, "ACLineSegment", (CONT, VL), (NAME, "L1")) + obj(A2, "ACLineSegment", (CONT, SUB))
            + obj(VL, "VoltageLevel") + obj(SUB, "Substation"))

HAS_CONTAINER = "SELECT DISTINCT ?this WHERE { ?this rdf:type cim:Breaker ; cim:Equipment.EquipmentContainer ?c . }"
SHARED = """cim:P a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; {constraint} .
    cim:B a sh:NodeShape ; sh:targetClass cim:Breaker ; sh:property cim:P .
    cim:S a sh:NodeShape ; sh:targetClass cim:{target} ; sh:property cim:P ."""

CASES = {
    # ── targets ──────────────────────────────────────────────────────────
    "target_class": (f"{B} {NAME_MIN}", TRIO),
    "target_class_two": (f"cim:B a sh:NodeShape ; sh:targetClass cim:Breaker, cim:Bay ; {NAME_MIN}", TRIO),
    "target_class_inherited": (f"cim:B a sh:NodeShape ; sh:targetClass cim:Switch ; {NAME_MIN}", TRIO),
    "target_class_inheritance_off": (f"cim:B a sh:NodeShape ; sh:targetClass cim:Switch ; {NAME_MIN}", TRIO,
                                     {"inheritance": False}),
    "target_subjects_of": (f"cim:B a sh:NodeShape ; sh:targetSubjectsOf cim:Equipment.EquipmentContainer ; {NAME_MIN}", TRIO),
    "target_objects_of": (f"cim:B a sh:NodeShape ; sh:targetObjectsOf cim:Equipment.EquipmentContainer ; {NAME_MIN}", TRIO),
    "target_objects_of_dangling": (f"cim:B a sh:NodeShape ; sh:targetObjectsOf cim:Equipment.EquipmentContainer ; {NAME_MIN}",
                                   TRIO + breaker("b4", (CONT, "dangling"))),
    "target_objects_of_literal": (f"cim:B a sh:NodeShape ; sh:targetObjectsOf cim:IdentifiedObject.name ; {NAME_MIN}", TRIO),
    "target_node_present": (f"cim:B a sh:NodeShape ; sh:targetNode <urn:uuid:b3> ; {NAME_MIN}", TRIO),
    "target_node_absent": (f"cim:B a sh:NodeShape ; sh:targetNode <urn:uuid:ghost> ; {NAME_MIN}", TRIO),
    # the ENTSO-E idiom: a class IRI in no data gives a dataset-wide sh:sparql its focus
    "target_node_virtual_iri_sparql": ('''cim:V a sh:NodeShape ; sh:targetNode cim:IDuniqueness ;
        sh:property [ sh:path rdf:type ; sh:sparql [ a sh:SPARQLConstraint ; sh:message "no name" ;
            sh:select """SELECT $this ?value WHERE { ?value a <http://iec.ch/TC57/CIM100#Breaker> .
                FILTER NOT EXISTS { ?value <http://iec.ch/TC57/CIM100#IdentifiedObject.name> ?n } }""" ] ] .''', TRIO),
    "target_sparql": (sparql_target(HAS_CONTAINER, NAME_MIN), TRIO),
    "target_sparql_plus_class": (sparql_target(HAS_CONTAINER, f"sh:targetClass cim:Breaker ; {NAME_MIN}"), TRIO),
    "target_sparql_node": (sparql_target(HAS_CONTAINER, "sh:node cim:Named .") + NAMED, TRIO),
    "target_sparql_or": (sparql_target(HAS_CONTAINER, "sh:or ( cim:Named cim:Desc ) .") + NAMED + DESCRIBED, TRIO),
    "target_sparql_sparql_constraint": (sparql_target(HAS_CONTAINER, """sh:property [ sh:path cim:IdentifiedObject.name ;
        sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value }' ] ] ."""), TRIO),
    "target_class_and_subjects_of": (f"{B} sh:targetSubjectsOf cim:Equipment.EquipmentContainer ; {NAME_MIN}", TRIO),
    # one property shape on a parent and a child node shape: one finding per Breaker
    "shared_property_shape": (SHARED.format(constraint="sh:minCount 1", target="Switch"), TRIO),
    # one property shape under two node shapes with the same target counts each value once
    "shared_property_shape_maxCount": (SHARED.format(constraint="sh:maxCount 1", target="Breaker"), breaker("b1", (NAME, "n"))),
    "shared_property_shape_minCount": (SHARED.format(constraint="sh:minCount 2", target="Breaker"), breaker("b1", (NAME, "n"))),
    "data_scope_target_node": (f"cim:B a sh:NodeShape ; sh:targetNode <urn:uuid:b1> ; {NAME_MIN}",
                               breaker("b1", (NAME, "n")) + [("b1", "Type", "Breaker", "other")], {"data_scope": ["other"]}),
    "data_scope_target_objects_of": (f"cim:B a sh:NodeShape ; sh:targetObjectsOf cim:Equipment.EquipmentContainer ; {NAME_MIN}",
                                     breaker("b1", (CONT, "bay1")) + breaker("b2", (CONT, "bay2"), instance="other"),
                                     {"data_scope": ["other"]}),
    "data_scope_empty": (f"{B} {NAME_MIN}", TRIO, {"data_scope": []}),
    # ── value components, property level ─────────────────────────────────
    "minCount": (prop("sh:path cim:IdentifiedObject.name ; sh:minCount 1"), TRIO),
    "maxCount": (prop("sh:path cim:IdentifiedObject.name ; sh:maxCount 1"),
                 breaker("b1", (NAME, "a"), (NAME, "b")) + breaker("b2", (NAME, "c"))),
    # "1" is the lexicalForm allowance
    "datatype_float": (prop("sh:path cim:Conductor.length ; sh:datatype xsd:float"),
                       breaker("b1", (LEN, "10.5")) + breaker("b2", (LEN, "abc")) + breaker("b3", (LEN, "1"))),
    "datatype_integer": (prop("sh:path cim:TransformerEnd.endNumber ; sh:datatype xsd:integer"),
                         breaker("b1", ("TransformerEnd.endNumber", "1")) + breaker("b2", ("TransformerEnd.endNumber", "1.5"))),
    # well-formed values only: rdflib loads an ill-formed boolean as false, a VALUE that is not in the data
    "datatype_boolean": (prop("sh:path cim:Switch.normalOpen ; sh:datatype xsd:boolean"),
                         breaker("b1", ("Switch.normalOpen", "true")) + breaker("b2", ("Switch.normalOpen", "false"))
                         + breaker("b3", ("Switch.normalOpen", "1"))),
    "class": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:class cim:Bay"), TRIO),
    "class_inherited": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:class cim:EquipmentContainer"),
                        TRIO + breaker("b4", (CONT, "b1"))),
    "minInclusive": (prop("sh:path cim:Conductor.length ; sh:minInclusive 0.0"),
                     breaker("b1", (LEN, "-5.0")) + breaker("b2", (LEN, "0.0")) + breaker("b3", (LEN, "3.0"))),
    "maxInclusive": (prop("sh:path cim:Conductor.length ; sh:maxInclusive 1.0"),
                     breaker("b1", (LEN, "-5.0")) + breaker("b2", (LEN, "1.0")) + breaker("b3", (LEN, "3.0"))),
    "minExclusive": (prop("sh:path cim:Conductor.length ; sh:minExclusive 0.0"),
                     breaker("b1", (LEN, "-5.0")) + breaker("b2", (LEN, "0.0")) + breaker("b3", (LEN, "3.0"))),
    "maxExclusive": (prop("sh:path cim:Conductor.length ; sh:maxExclusive 1.0"),
                     breaker("b1", (LEN, "-5.0")) + breaker("b2", (LEN, "1.0")) + breaker("b3", (LEN, "3.0"))),
    "range_non_numeric": (prop("sh:path cim:Conductor.length ; sh:minInclusive 0.0"),
                          breaker("b1", (LEN, "abc")) + breaker("b2", (LEN, "3.0"))),
    "pattern": (prop('sh:path cim:IdentifiedObject.name ; sh:pattern "^B"'),
                breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "xB")) + breaker("b3", (NAME, "x"))),
    "pattern_flags": (prop('sh:path cim:IdentifiedObject.name ; sh:pattern "^b" ; sh:flags "i"'),
                      breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "x"))),
    "minLength": (prop("sh:path cim:IdentifiedObject.name ; sh:minLength 2"),
                  breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "x"))),
    "maxLength": (prop("sh:path cim:IdentifiedObject.name ; sh:maxLength 2"),
                  breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "long"))),
    "nodeKind_iri": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:nodeKind sh:IRI"), TRIO),
    "nodeKind_literal": (prop("sh:path cim:IdentifiedObject.name ; sh:nodeKind sh:Literal"), TRIO),
    "nodeKind_literal_on_association": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:nodeKind sh:Literal"), TRIO),
    "hasValue_literal": (prop('sh:path cim:IdentifiedObject.name ; sh:hasValue "B1"'), TRIO),
    "hasValue_iri": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:hasValue <urn:uuid:bay1>"), TRIO),
    "in_literals": (prop('sh:path cim:IdentifiedObject.name ; sh:in ( "B1" "B2" )'),
                    breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "other"))),
    "in_enum": (prop("sh:path cim:Switch.kind ; sh:in ( cim:SwitchKind.open )"),
                breaker("b1", ("Switch.kind", "SwitchKind.open")) + breaker("b2", ("Switch.kind", "SwitchKind.closed"))),
    # a "/"-only IRI stays whole, member and VALUE
    "in_enum_slash_iri": (prop("sh:path cim:Switch.kind ; sh:in ( <http://example.org/kinds/a> )"),
                          breaker("b1", ("Switch.kind", "http://example.org/kinds/a"))
                          + breaker("b2", ("Switch.kind", "http://example.org/kinds/b"))),
    "equals": (prop("sh:path cim:IdentifiedObject.name ; sh:equals cim:IdentifiedObject.description"),
               breaker("b1", (NAME, "x"), (DESC, "x")) + breaker("b2", (NAME, "x"), (DESC, "y")) + breaker("b3", (NAME, "x"))),
    "disjoint": (prop("sh:path cim:IdentifiedObject.name ; sh:disjoint cim:IdentifiedObject.description"),
                 breaker("b1", (NAME, "x"), (DESC, "x")) + breaker("b2", (NAME, "x"), (DESC, "y"))),
    "lessThan": (prop("sh:path cim:Conductor.length ; sh:lessThan cim:Switch.ratedCurrent"),
                 breaker("b1", (LEN, "1.0"), (CUR, "2.0")) + breaker("b2", (LEN, "2.0"), (CUR, "2.0"))
                 + breaker("b3", (LEN, "3.0"), (CUR, "2.0"))),
    "lessThanOrEquals": (prop("sh:path cim:Conductor.length ; sh:lessThanOrEquals cim:Switch.ratedCurrent"),
                         breaker("b1", (LEN, "1.0"), (CUR, "2.0")) + breaker("b2", (LEN, "2.0"), (CUR, "2.0"))
                         + breaker("b3", (LEN, "3.0"), (CUR, "2.0"))),
    "closed": (f"{B} sh:closed true ; sh:ignoredProperties ( rdf:type ) ; sh:property [ sh:path cim:IdentifiedObject.name ] .", TRIO),
    "sparql_constraint": (prop("""sh:path cim:IdentifiedObject.name ;
        sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "B1") }' ]"""), TRIO),
    "sparql_constraint_message": (prop("""sh:path cim:IdentifiedObject.name ;
        sh:sparql [ sh:message "no B1" ;
                    sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "B1") }' ]"""), TRIO),
    "sparql_constraint_node_level": (f"{B} sh:sparql [ sh:prefixes cim: ; "
                                     "sh:select 'SELECT $this WHERE { $this cim:IdentifiedObject.name ?n }' ] .", TRIO),
    # ── paths ────────────────────────────────────────────────────────────
    "path_inverse": ("cim:C a sh:NodeShape ; sh:targetClass cim:Bay, cim:Substation ; "
                     "sh:property [ sh:path [ sh:inversePath cim:Equipment.EquipmentContainer ] ; sh:minCount 2 ] .", TRIO),
    "path_inverse_class": ("cim:C a sh:NodeShape ; sh:targetClass cim:Bay ; "
                           "sh:property [ sh:path [ sh:inversePath cim:Equipment.EquipmentContainer ] ; sh:class cim:Switch ] .",
                           TRIO + obj("x1", "Bay", (CONT, "bay1"))),
    "path_via_type": (prop("sh:path ( cim:Equipment.EquipmentContainer rdf:type ) ; sh:in ( cim:Bay )"), TRIO),
    "path_alternative_inverse": ("cim:C a sh:NodeShape ; sh:targetClass cim:Bay ; sh:property [ sh:path [ sh:alternativePath "
                                 "( cim:Bay.VoltageLevel [ sh:inversePath cim:Equipment.EquipmentContainer ] ) ] ; sh:minCount 2 ] .",
                                 TRIO + [("bay1", "Bay.VoltageLevel", "sub1", "eq")]),
    # ── logical operators, node level ────────────────────────────────────
    "node_or_paths": (f"{B} sh:or ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] "
                      "[ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) .", TRIO),
    "node_and_paths": (f"{B} sh:and ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] "
                       "[ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) .", TRIO),
    "node_xone_paths": (f"{B} sh:xone ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] "
                        "[ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] ) .",
                        TRIO + breaker("b4", (NAME, "n"), (DESC, "d"))),
    "node_not_path": (f"{B} sh:not [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .", TRIO),
    "node_or_shapes": (f"{B} sh:or ( cim:Named cim:Desc ) ." + NAMED + DESCRIBED, TRIO),
    "node_and_shape": (f"{B} sh:and ( cim:Both ) ." + BOTH, TRIO),
    "node_and_shapes": (f"{B} sh:and ( cim:Named cim:Desc ) ." + NAMED + DESCRIBED, TRIO),
    "node_xone_shapes": (f"{B} sh:xone ( cim:Named cim:Desc ) ." + NAMED + DESCRIBED, TRIO + breaker("b4", (NAME, "n"), (DESC, "d"))),
    "node_not_shape": (f"{B} sh:not cim:Named ." + NAMED, TRIO),
    "node_or_nested_and": (f"{B} sh:or ( [ sh:and ( cim:Named cim:Desc ) ] [ sh:path cim:Conductor.length ; sh:minCount 1 ] ) ."
                           + NAMED + DESCRIBED, TRIO + breaker("b4", (NAME, "n"), (DESC, "d")) + breaker("b5", (LEN, "1.0"))),
    "node_not_or": (f"{B} sh:not [ sh:or ( cim:Named cim:Desc ) ] ." + NAMED + DESCRIBED, TRIO),
    "node_or_with_node": (f"{B} sh:or ( [ sh:node cim:Named ] cim:Desc ) ." + NAMED + DESCRIBED, TRIO),
    "node_and_inverse_member": ("cim:C a sh:NodeShape ; sh:targetClass cim:Bay, cim:Substation ; "
                                "sh:and ( [ sh:path [ sh:inversePath cim:Equipment.EquipmentContainer ] ; sh:minCount 2 ] ) .", TRIO),
    # ── logical operators, property level, path-less members (value tests) ──
    "prop_or_values": (prop('sh:path cim:IdentifiedObject.name ; sh:or ( [ sh:pattern "^A" ] [ sh:pattern "Z$" ] )'),
                       breaker("b1", (NAME, "AZ"), (NAME, "xx"), (NAME, "Ax"))),
    "prop_or_each_value_passes": (prop('sh:path cim:IdentifiedObject.name ; sh:or ( [ sh:in ( "A" ) ] [ sh:pattern "^B" ] )'),
                                  breaker("b1", (NAME, "A"), (NAME, "Bx")) + breaker("b2", (NAME, "A"), (NAME, "C"))),
    "prop_and_values": (prop("sh:path cim:IdentifiedObject.name ; sh:and ( [ sh:minLength 2 ] [ sh:maxLength 3 ] )"),
                        breaker("b1", (NAME, "ab"), (NAME, "x"), (NAME, "long"))),
    "prop_xone_values": (prop('sh:path cim:IdentifiedObject.name ; sh:xone ( [ sh:pattern "^A" ] [ sh:pattern "Z$" ] )'),
                         breaker("b1", (NAME, "AZ"), (NAME, "xx"), (NAME, "Ax"))),
    "prop_not_value": (prop('sh:path cim:IdentifiedObject.name ; sh:not [ sh:in ( "B1" ) ]'),
                       breaker("b1", (NAME, "B1"), (NAME, "x"))),
    "prop_or_nested_and_values": (prop('sh:path cim:IdentifiedObject.name ; '
                                       'sh:or ( [ sh:and ( [ sh:minLength 2 ] [ sh:pattern "^A" ] ) ] [ sh:pattern "Z$" ] )'),
                                  breaker("b1", (NAME, "AZ"), (NAME, "xx"), (NAME, "Ax"), (NAME, "A"))),
    # ── property level, members with their own sh:path ───────────────────
    "prop_or_member_path": (prop("sh:path cim:Equipment.EquipmentContainer ; "
                                 "sh:or ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] )"), RING),
    "prop_and_member_path": (prop("sh:path cim:Equipment.EquipmentContainer ; "
                                  "sh:and ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] )"), RING),
    "prop_xone_member_path": (prop("sh:path cim:Equipment.EquipmentContainer ; "
                                   "sh:xone ( [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] "
                                   "[ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] )"), RING),
    "prop_not_member_path": (prop("sh:path cim:Equipment.EquipmentContainer ; "
                                  "sh:not [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]"), RING),
    # ── property level, NodeShape members with sh:property ───────────────
    "prop_or_node_shape": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:or ( cim:Named [ sh:hasValue <urn:uuid:sub1> ] )")
                           + NAMED, TRIO),
    "prop_and_node_shape": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:and ( cim:Named )") + NAMED, TRIO),
    "prop_xone_node_shapes": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:xone ( cim:Named cim:Desc )") + NAMED + DESCRIBED,
                              TRIO + [("bay1", DESC, "d", "eq")]),
    "prop_not_node_shape": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:not cim:Named") + NAMED, TRIO),
    # ── sh:node ──────────────────────────────────────────────────────────
    "prop_node": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Named") + NAMED, TRIO),
    "prop_node_two_values": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Named, cim:Desc") + NAMED + DESCRIBED, RING),
    "node_node": (f"{B} sh:node cim:Named ." + NAMED, TRIO),
    "node_node_two_values": (f"{B} sh:node cim:Named, cim:Desc ." + NAMED + DESCRIBED, TRIO),
    "node_node_chain": (f"{B} sh:node cim:Mid . cim:Mid a sh:NodeShape ; sh:node cim:Named ." + NAMED, TRIO),
    "node_node_two_findings": (f"{B} sh:node cim:Both ." + BOTH, TRIO),
    "node_node_plus_own_property": (f"{B} sh:node cim:Named ; {DESC_MIN} ." + NAMED, TRIO),
    "prop_node_chain": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Mid")
                        + "cim:Mid a sh:NodeShape ; sh:node cim:Named ." + NAMED, TRIO),
    "prop_node_referenced_or": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Either")
                                + "cim:Either a sh:NodeShape ; sh:or ( cim:Named cim:Desc ) ." + NAMED + DESCRIBED, TRIO),
    "prop_node_referenced_inverse": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Busy")
                                     + "cim:Busy a sh:NodeShape ; sh:property [ sh:path [ sh:inversePath "
                                       "cim:Equipment.EquipmentContainer ] ; sh:minCount 2 ] .", TRIO),
    "prop_node_in_or_member": (prop("sh:path cim:Equipment.EquipmentContainer ; "
                                    "sh:or ( [ sh:node cim:Named ] [ sh:hasValue <urn:uuid:sub1> ] )") + NAMED, TRIO),
    # one value node referenced twice: one row per referring focus
    "prop_node_value_node_two_refs": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Named") + NAMED,
                                      TRIO + breaker("b4", (CONT, "sub1"))),
    "prop_node_with_own_property": (prop("sh:path cim:Equipment.EquipmentContainer ; "
                                         "sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ]"), TRIO),
    # ── deactivation ─────────────────────────────────────────────────────
    "deactivated_node_shape": (deactivated(f"{B} {NAME_MIN}"), TRIO),
    "deactivated_property_shape": (f"{B} sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; sh:deactivated true ] ; "
                                   f"{DESC_MIN} .", TRIO),
    # the NC profiles' form: a standalone property shape switched off
    "deactivated_standalone_property_shape": ("cim:P a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; "
                                              f"sh:deactivated true . {B} sh:property cim:P ; {DESC_MIN} .", TRIO),
    "deactivated_node_target": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Named") + deactivated(NAMED), TRIO),
    "deactivated_node_target_node_level": (f"{B} sh:node cim:Named ." + deactivated(NAMED), TRIO),
    "deactivated_or_member": (f"{B} sh:or ( [ sh:deactivated true ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] cim:Desc ) ."
                              + DESCRIBED, TRIO),
    "deactivated_and_member": (f"{B} sh:and ( [ sh:deactivated true ; {NAME_MIN[:-2]} ] ) .", TRIO),
    "deactivated_xone_member": (f"{B} sh:xone ( [ sh:deactivated true ; {DESC_MIN} ] [ {NAME_MIN[:-2]} ] ) .", TRIO),
    # a switched-off negated shape conforms for every focus: sh:not flags them all
    "deactivated_not_member": (f"{B} sh:not cim:Named ." + deactivated(NAMED), TRIO),
    # the path of a deactivated property shape is still declared, so sh:closed allows it
    "deactivated_closed_path": (f"{B} sh:closed true ; sh:ignoredProperties ( rdf:type cim:Equipment.EquipmentContainer "
                                f"cim:IdentifiedObject.name ) ; {DESC_MIN} ; "
                                "sh:property [ sh:path cim:IdentifiedObject.aliasName ; sh:maxCount 0 ; sh:deactivated true ] .",
                                TRIO + breaker("b4", (ALIAS, "a"), (DESC, "d"))),
    "deactivated_parameter": (f"{B} sh:node cim:Named ; {DESC_MIN} ." + NAMED, TRIO, {"deactivated_shapes": [CIM + "Named"]}),
    "deactivated_parameter_reenables": (deactivated(f"{B} {NAME_MIN}"), TRIO, {"deactivated_shapes": []}),
    "deactivated_parameter_replaces_declared": (
        "cim:N a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 . "
        "cim:D a sh:PropertyShape ; sh:path cim:IdentifiedObject.description ; sh:minCount 1 ; sh:deactivated true . "
        f"{B} sh:property cim:N, cim:D .", TRIO, {"deactivated_shapes": [CIM + "N"]}),
    "deactivated_shared_property_shape": (SHARED.format(constraint="sh:minCount 1", target="Bay"), TRIO,
                                          {"deactivated_shapes": [CIM + "P"]}),
    "deactivated_node_shape_keeps_shared": (SHARED.format(constraint="sh:minCount 1", target="Breaker"), TRIO,
                                            {"deactivated_shapes": [CIM + "B"]}),
    "deactivated_declared_false": ('cim:X a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; '
                                   f'sh:deactivated "false"^^xsd:boolean . {B} sh:property cim:X ; {DESC_MIN} .', TRIO,
                                   {"deactivated_shapes": [CIM + "X"]}),
    # an ill-typed sh:deactivated literal has no value: the shape stays on
    "deactivated_ill_typed_flag": ('cim:X a sh:PropertyShape ; sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; '
                                   f'sh:deactivated "abc"^^xsd:integer . {B} sh:property cim:X .', TRIO),
    "deactivated_sparql_target": (sparql_target(HAS_CONTAINER, NAME_MIN), TRIO, {"deactivated_shapes": [CIM + "B"]}),
    # ── severity / message ───────────────────────────────────────────────
    "severity_warning_message": (prop('sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; sh:severity sh:Warning ; '
                                      'sh:message "needs a name"'), TRIO),
    # a node shape's severity and message do not reach its property shape's rows
    "severity_info_on_node_shape": (f'{B} sh:severity sh:Info ; sh:message "outer" ; {NAME_MIN}', TRIO),
    "message_on_node_level_node": (f'{B} sh:node cim:Named ; sh:message "must be Named" .' + NAMED, TRIO),
    "message_on_outer_vs_member": (prop('sh:path cim:IdentifiedObject.name ; sh:message "outer" ; sh:severity sh:Warning ; '
                                        'sh:or ( [ sh:pattern "^A" ; sh:message "inner" ; sh:severity sh:Info ] [ sh:pattern "Z$" ] )'),
                                   breaker("b1", (NAME, "xx"))),
    "message_on_node_target_property": (prop('sh:path cim:Equipment.EquipmentContainer ; sh:node cim:Named ; sh:message "outer"')
                                        + NAMED.replace("sh:minCount 1", 'sh:minCount 1 ; sh:message "inner" ; sh:severity sh:Warning'),
                                        TRIO),
    "message_two_values": (prop('sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; sh:message "beta", "alpha"'), TRIO),
    # ── a parameter given twice ──────────────────────────────────────────
    "twice_or_lists": (f"{B} sh:or ( cim:Named ), ( cim:Desc ) ." + NAMED + DESCRIBED, TRIO),
    "twice_and_lists": (f"{B} sh:and ( cim:Named ), ( cim:Desc ) ." + NAMED + DESCRIBED, TRIO),
    "twice_xone_lists": (f"{B} sh:xone ( cim:Named ), ( cim:Desc ) ." + NAMED + DESCRIBED, TRIO),
    "twice_not": (f"{B} sh:not cim:Named, cim:Desc ." + NAMED + DESCRIBED, TRIO),
    # pyshacl rejects these four shapes; once the engines raise at compile time too, both legs give
    # ("raises", …) and "rejected on both sides" is the parity claim: the XFAIL entries go then
    "twice_minCount": (prop("sh:path cim:IdentifiedObject.name ; sh:minCount 1, 2"), TRIO + breaker("b4", (NAME, "a"), (NAME, "b"))),
    "twice_pattern": (prop('sh:path cim:IdentifiedObject.name ; sh:pattern "^B", "1$"'),
                      breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "B2")) + breaker("b3", (NAME, "x1"))),
    "twice_datatype": (prop("sh:path cim:Conductor.length ; sh:datatype xsd:float, xsd:integer"), breaker("b1", (LEN, "10.5"))),
    "twice_class": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:class cim:Bay, cim:Substation"), TRIO),
    "twice_in": (prop('sh:path cim:IdentifiedObject.name ; sh:in ( "B1" ), ( "B2" )'),
                 breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "B2"))),
    "twice_hasValue": (prop('sh:path cim:IdentifiedObject.name ; sh:hasValue "B1", "B2"'),
                       breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "B2")) + breaker("b3", (NAME, "B1"), (NAME, "B2"))),
    "twice_sparql": (prop("""sh:path cim:IdentifiedObject.name ;
        sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "B1") }' ] ,
                  [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "B2") }' ]"""),
                     breaker("b1", (NAME, "B1")) + breaker("b2", (NAME, "B2"))),
    "twice_property_same_path": (f"{B} sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ; "
                                 "sh:property [ sh:path cim:IdentifiedObject.name ; sh:maxLength 1 ] .", TRIO),
    "twice_path": (prop("sh:path cim:IdentifiedObject.name, cim:IdentifiedObject.description ; sh:minCount 1"), TRIO),
    # ── the CGMES 3.0 export schema, UUID ids ────────────────────────────
    "entsoe_class": ("cim:L a sh:NodeShape ; sh:targetClass cim:ACLineSegment ; "
                     "sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:class cim:VoltageLevel ] .", SEGMENTS, ENTSOE),
    "entsoe_target_objects_of": (f"cim:C a sh:NodeShape ; sh:targetObjectsOf cim:Equipment.EquipmentContainer ; {NAME_MIN}",
                                 SEGMENTS, ENTSOE),
    "entsoe_path_via_type": ("cim:L a sh:NodeShape ; sh:targetClass cim:ACLineSegment ; "
                             "sh:property [ sh:path ( cim:Equipment.EquipmentContainer rdf:type ) ; sh:in ( cim:VoltageLevel ) ] .",
                             SEGMENTS, ENTSOE),
    # ── more components and forms ────────────────────────────────────────
    # a declared sh:deactivated switched back on, so pyshacl validates a copy of the shapes graph
    "deactivated_parameter_reenables_inverse_path": (
        "cim:C a sh:NodeShape ; sh:targetClass cim:Bay, cim:Substation ; "
        "sh:property [ sh:path [ sh:inversePath cim:Equipment.EquipmentContainer ] ; sh:minCount 2 ] . "
        + deactivated(f"cim:D a sh:NodeShape ; sh:targetClass cim:Bay ; {DESC_MIN} ."), TRIO, {"deactivated_shapes": []}),
    "maxCount_zero": (prop("sh:path cim:IdentifiedObject.aliasName ; sh:maxCount 0"), TRIO + breaker("b4", (ALIAS, "a"))),
    "class_on_literal": (prop("sh:path cim:IdentifiedObject.name ; sh:class cim:Bay"), TRIO),
    "nodeKind_blank_node": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:nodeKind sh:BlankNode"), TRIO),
    "in_numeric": (prop("sh:path cim:Conductor.length ; sh:in ( 1.0 2.0 )"), breaker("b1", (LEN, "1.0")) + breaker("b2", (LEN, "3.0"))),
    "lessThan_missing_comparand": (prop("sh:path cim:Conductor.length ; sh:lessThan cim:Switch.ratedCurrent"),
                                   breaker("b1", (LEN, "1.0")) + breaker("b2", (LEN, "3.0"), (CUR, "2.0"))),
    "minLength_on_iri": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:minLength 10"), TRIO),
    "closed_shared_property_shape": ("cim:P a sh:PropertyShape ; sh:path cim:IdentifiedObject.name . "
                                     f"{B} sh:closed true ; sh:ignoredProperties ( rdf:type ) ; sh:property cim:P .", TRIO),
    "sparql_inverse_path": ("cim:C a sh:NodeShape ; sh:targetClass cim:Bay, cim:Substation ; sh:property [ "
                            "sh:path [ sh:inversePath cim:Equipment.EquipmentContainer ] ; "
                            "sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value }' ] ] .", TRIO),
    "node_inside_not": (prop("sh:path cim:Equipment.EquipmentContainer ; sh:not [ sh:node cim:Named ]") + NAMED, TRIO),
    "deactivated_sparql_constraint": (prop("""sh:path cim:IdentifiedObject.name ; sh:sparql [ sh:deactivated true ;
        sh:select 'SELECT $this ?value WHERE { $this $PATH ?value }' ]"""), TRIO),
    "target_class_not_in_schema": (f"cim:B a sh:NodeShape ; sh:targetClass cim:Disconnector ; {NAME_MIN}",
                                   TRIO + obj("d1", "Disconnector")),
}

# the engines evaluate nothing of these shapes; the note is the run metadata's
# skipped_shapes / skipped_components entry naming it
SKIPPED = {
    "path_sequence_unsupported": (prop("sh:path ( cim:Equipment.EquipmentContainer cim:IdentifiedObject.name ) ; sh:minCount 1"),
                                  TRIO, "unsupported sh:path form"),
    "node_level_in": (f"{B} sh:in ( <urn:uuid:b1> ) .", TRIO, "node-level sh:in not evaluated"),
    "node_level_hasValue": (f"{B} sh:hasValue <urn:uuid:b1> .", TRIO, "node-level sh:hasValue not evaluated"),
    "qualified": (prop('sh:path cim:IdentifiedObject.name ; sh:qualifiedValueShape [ sh:pattern "^B" ] ; sh:qualifiedMinCount 1'),
                  TRIO, "sh:qualifiedMinCount"),
    # pyshacl rejects a bare-IRI sh:target (ShapeLoadError)
    "target_without_select": (f"cim:B a sh:NodeShape ; sh:target cim:SomeTarget ; {NAME_MIN}", TRIO, "sh:target not walked"),
    "or_over_unknown": (f"{B} sh:or ( [ sh:path cim:IdentifiedObject.name ; sh:uniqueLang true ] cim:Desc ) ." + DESCRIBED,
                        TRIO, "uses sh:uniqueLang"),
    # sh:BlankNodeOrIRI and sh:IRIOrLiteral are only debug-logged: no metadata entry names them
    "nodeKind_blank_or_iri": (prop("sh:path cim:IdentifiedObject.name ; sh:nodeKind sh:BlankNodeOrIRI"), TRIO, None),
    "nodeKind_iri_or_literal": (prop("sh:path cim:IdentifiedObject.name ; sh:nodeKind sh:IRIOrLiteral"), TRIO, None),
}

ISSUE = "https://github.com/Haigutus/triplets/issues/"


def known(reason, *cases):
    """One divergence, on every engine."""
    return {(case, engine): reason for case in cases for engine in ENGINES}


XFAIL = {
    **known(ISSUE + "163 second sh:node value dropped", "prop_node_two_values", "node_node_two_values"),
    **known(ISSUE + "164 node-level sh:and: one row per nested finding",
            "node_and_paths", "node_and_shape", "node_and_shapes", "node_and_inverse_member"),
    **known(ISSUE + "164 path-level sh:and: SOURCE_SHAPE is the member's", "prop_and_values", "prop_and_node_shape"),
    **known(ISSUE + "166 member with its own sh:path checked on the focus",
            "prop_or_member_path", "prop_and_member_path", "prop_xone_member_path", "prop_not_member_path"),
    **known(ISSUE + "163 N1 a parameter given twice: one value read",
            "twice_or_lists", "twice_xone_lists", "twice_not", "twice_pattern", "twice_class", "twice_hasValue"),
    **known(ISSUE + "163 N1 a parameter given twice: evaluated where pyshacl rejects the shape",
            "twice_minCount", "twice_datatype", "twice_in", "twice_path"),
    **known(ISSUE + "163 N1 a parameter given twice: one value read, and #164", "twice_and_lists"),
    **known("N2 sh:flags not read, issue pending", "pattern_flags"),
    **known("N3 sh:alternativePath reduced to its inverse member, issue pending", "path_alternative_inverse"),
    **known("N4 sh:property on a property shape reported as sh:node, issue pending", "prop_node_with_own_property"),
    **known("N7 sh:nodeKind sh:BlankNode not evaluated, draft pending", "nodeKind_blank_node"),
    **known("N8 sh:in compares lexical forms, not typed literals, draft pending", "in_numeric"),
    **known("N9 sh:minLength on an IRI measures the local id, draft pending", "minLength_on_iri"),
    **known("N10 sh:sparql $PATH left unbound on a blank-node path, draft pending", "sparql_inverse_path"),
    **known("N11 sh:deactivated on a sh:sparql constraint ignored, draft pending", "deactivated_sparql_constraint"),
    **known("N12 two sh:message values: the engines report the first declared, pyshacl's reader the first "
            "alphabetically, draft pending", "message_two_values"),
}


@functools.cache
def compiled(name):
    """One CompiledShapes per case for all four runs: blank-node labels (SOURCE_SHAPE
    of anonymous shapes) are new on every parse of the Turtle."""
    import rdflib
    shape = {**CASES, **SKIPPED}[name][0]
    return triplets.validation.compile(rdflib.Graph().parse(data=PREFIX + shape, format="turtle"))


def frame(rows):
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def run(name, engine, **extra):
    """The case's canonical rows on *engine*; a shape rejected at load is ("raises", <exception class>)."""
    _, rows, *rest = CASES[name]
    kwargs = {"rdf_map": SCHEMA, "rdf_map_scope": "*", **(rest[0] if rest else {}), **extra}
    try:
        return canon_violations(triplets.validation.validate_shacl(frame(rows), compiled(name), engine=engine, **kwargs))
    except (ValueError, ConstraintLoadError, ShapeLoadError) as error:
        return ("raises", type(error).__name__)


@functools.cache
def reference(name):
    """pyshacl, pure: lexical=False (the supplement would repeat the pandas datatype rows)."""
    return run(name, "pyshacl", lexical=False)


def matrix():
    return [pytest.param(case, engine, id=f"{case}-{engine}",
                         marks=[pytest.mark.xfail(strict=True, reason=XFAIL[case, engine])] if (case, engine) in XFAIL else [])
            for case in CASES for engine in ENGINES]


@pytest.mark.parametrize("case, engine", matrix())
def test_matrix(case, engine):
    if engine != "pandas":
        pytest.importorskip(engine)
    assert run(case, engine) == reference(case)


def test_xfail_entries_name_cases():
    assert {case for case, _ in XFAIL} <= set(CASES)


def test_pyshacl_names_the_qualified_components():
    _, rows, _ = SKIPPED["qualified"]
    violations = triplets.validation.validate_shacl(frame(rows), compiled("qualified"), engine="pyshacl", rdf_map=SCHEMA,
                                                    rdf_map_scope="*", lexical=False)
    assert set(violations["VIOLATION_TYPE"]) == {"sh:qualifiedMinCount"}


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("case", SKIPPED)
def test_skipped(case, engine):
    """The engine reports nothing for the shape and names it in the run metadata."""
    if engine != "pandas":
        pytest.importorskip(engine)
    _, rows, note = SKIPPED[case]
    violations = triplets.validation.validate_shacl(frame(rows), compiled(case), engine=engine, rdf_map=SCHEMA, rdf_map_scope="*")
    assert violations.empty
    meta = violations.attrs["validation"]
    if note:
        assert any(note in entry for entry in meta["skipped_shapes"] + meta["skipped_components"])
