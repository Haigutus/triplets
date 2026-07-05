"""Demonstration of SHACL validation (triplets.validation).

Loads a real CGMES EQ instance, validates it against an inline SHACL shape,
and shows how schema typing, instance scoping, compile-once shapes and the
lexical-form datatype check change the results.
"""
from pathlib import Path

import pandas
import triplets
from triplets.export_schema import schemas

REPO = Path(__file__).resolve().parent.parent
EQ = str(REPO / "test_data/relicapgrid/Instance/Grid/IGM_Svedala/20220615T2230Z__Svedala_EQ_1.xml")

# A SHACL NodeShape targeting every ACLineSegment:
#  - it must have a name              (sh:minCount)
#  - its length must be an xsd:float  (sh:datatype)
SHAPE = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
                  sh:message "ACLineSegment must have a name" ] ;
    sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ;
                  sh:message "Conductor.length must be xsd:float" ] .
"""


def header(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# Write the shape next to the script so the engine auto-detects turtle by extension.
shape_path = str(Path(__file__).resolve().parent / "shacl_validation_shape.ttl")
Path(shape_path).write_text(SHAPE)

header("1. Load a real CGMES EQ instance as a triplet DataFrame")
data = pandas.read_RDF([EQ])
print(f"Loaded {len(data):,} triples")
n_lines = (data["VALUE"] == "ACLineSegment").sum()
print(f"ACLineSegment instances in the data: {n_lines}")

header("2. Validate WITHOUT a schema — VALUEs are untyped strings")
print("'cim:Conductor.length' is a plain string, so sh:datatype xsd:float fails.\n")
violations = data.shacl.validate(shape_path, lexical=False)
print(f"Violations found: {len(violations)}")
print(violations[["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"]].head(10).to_string(index=False))

header("3. Validate WITH a schema (rdf_map) — VALUEs get proper xsd types")
print("Now Conductor.length is exported as xsd:float, so the datatype check passes.\n")
violations_typed = data.shacl.validate(shape_path, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
                                       lexical=False)
print(f"Violations found: {len(violations_typed)}")
if violations_typed.empty:
    print("Data conforms to the shape.")

header("4. Scope validation to a single instance graph")
instance = str(data["INSTANCE_ID"].astype(str).iloc[0])
print(f"Scoping to INSTANCE_ID = {instance}")
scoped = data.shacl.validate(shape_path, scope=[instance], lexical=False)
print(f"In-scope violations:     {len(scoped)}")
out = data.shacl.validate(shape_path, scope=["00000000-0000-0000-0000-000000000000"], lexical=False)
print(f"Out-of-scope violations: {len(out)}  (no ACLineSegments target this graph)")

header("5. The lexical-form check — the deliberate deviation from pyshacl")
print('rdflib reads "1"^^xsd:float as simply valid; the raw lexical form is judged here:\n')
mixed = pandas.DataFrame([
    ("a1", "Type", "ACLineSegment", "eq"), ("a1", "IdentifiedObject.name", "L1", "eq"),
    ("a1", "Conductor.length", "10.5", "eq"),   # canonical float — fine
    ("a2", "Type", "ACLineSegment", "eq"), ("a2", "IdentifiedObject.name", "L2", "eq"),
    ("a2", "Conductor.length", "1", "eq"),      # integer form  — Warning (triplets:lexicalForm)
    ("a3", "Type", "ACLineSegment", "eq"), ("a3", "IdentifiedObject.name", "L3", "eq"),
    ("a3", "Conductor.length", "abc", "eq"),    # not a float   — Violation (sh:datatype)
], columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
findings = triplets.validation.validate(mixed, shape_path, engine="pandas")
print(findings[["ID", "VALUE", "VIOLATION_TYPE", "SEVERITY", "MESSAGE"]].to_string(index=False))

header("6. Compile once, validate many")
compiled = triplets.validation.compile(shape_path)
print(f"CompiledShapes: {len(compiled.ir)} IR rows, hash {compiled.hash[:12]}…")
print("IR (constraint table):")
print(compiled.ir[["target_class", "path", "component", "params", "severity"]].to_string(index=False))
data.shacl.validate(compiled, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)   # no re-parse
print("\nSecond validate() against the same shapes reused the compiled IR (cache hit).")

header("Done")
print("Engines: pyshacl (reference) + pandas (lexical seed)  |  API: df.shacl.validate(shapes, rdf_map=, scope=)")
