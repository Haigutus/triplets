"""Demonstration of the new SPARQL query engine (triplets.sparql).

Loads a real CGMES EQ instance and shows the four SPARQL query forms and the
result structure each one returns: SELECT -> DataFrame, ASK -> bool,
CONSTRUCT/DESCRIBE -> triplet DataFrame.
"""
from pathlib import Path

import pandas
import triplets
from triplets.export_schema import schemas

REPO = Path(__file__).resolve().parent.parent
EQ = str(REPO / "test_data/relicapgrid/Instance/Grid/IGM_Svedala/20220615T2230Z__Svedala_EQ_1.xml")

PREFIXES = """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX cim: <http://iec.ch/TC57/CIM100#>
"""


def header(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


data = pandas.read_RDF([EQ])
print(f"Loaded {len(data):,} triples from a CGMES EQ instance")

header("1. SELECT  ->  pandas DataFrame (columns = projected variables)")
q = PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name } LIMIT 5"
res = data.sparql.query(q)
print(f"type(result) = {type(res).__name__}   columns = {list(res.columns)}\n")
print(res.to_string(index=False))

header("2. SELECT with aggregation (COUNT)  ->  one-row DataFrame")
q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
res = data.sparql.query(q)
print(res.to_string(index=False))

header("3. ASK  ->  bool")
yes = data.sparql.query(PREFIXES + "ASK { ?s rdf:type cim:Substation }")
no = data.sparql.query(PREFIXES + "ASK { ?s rdf:type cim:NoSuchClass }")
print(f"ASK Substation exists?  {yes!r}   (type {type(yes).__name__})")
print(f"ASK NoSuchClass exists? {no!r}")

header("4. SELECT with rdf_map  ->  literals come back python-typed")
q = PREFIXES + "SELECT ?l WHERE { ?s cim:Conductor.length ?l } LIMIT 3"
untyped = data.sparql.query(q)
typed = data.sparql.query(q, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
print(f"no rdf_map : value={untyped['l'].iloc[0]!r}  python type={type(untyped['l'].iloc[0]).__name__}")
print(f"rdf_map    : value={typed['l'].iloc[0]!r}  python type={type(typed['l'].iloc[0]).__name__}")

header("5. CONSTRUCT  ->  triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID]")
q = PREFIXES + """
CONSTRUCT { ?s rdf:type cim:ACLineSegment }
WHERE     { ?s rdf:type cim:ACLineSegment }"""
res = data.sparql.query(q)
print(f"type(result) = {type(res).__name__}   columns = {list(res.columns)}   rows = {len(res)}\n")
print(res.head(5).to_string(index=False))
print("\nNote: urn:uuid: stripped from ID, rdf:type rendered as 'Type', "
      "INSTANCE_ID is None (constructed graph has no source instance).")

header("6. Scope a query to a single instance graph")
inst = data[(data["KEY"] == "Type") & (data["VALUE"] == "ACLineSegment")]["INSTANCE_ID"]
eq_instance = str(inst.astype(str).iloc[0])
q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
in_scope = int(data.sparql.query(q, scope=[eq_instance])["n"].iloc[0])
out_scope = int(data.sparql.query(q, scope=["00000000-0000-0000-0000-000000000000"])["n"].iloc[0])
print(f"ACLineSegments in EQ instance graph : {in_scope}")
print(f"ACLineSegments in an empty graph    : {out_scope}")

header("Done")
print("Engine: rdflib reference  |  API: df.sparql.query(sparql, rdf_map=, scope=)")
