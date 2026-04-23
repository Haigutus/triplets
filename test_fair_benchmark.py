"""Fair benchmark: both engines load from the same .nq file on disk."""
import time
from rdflib import ConjunctiveGraph
from oxrdflib import OxigraphStore

CIM = "http://iec.ch/TC57/CIM100#"
nq_path = "/tmp/cgmes_eq.nq"

# ─── Oxigraph: load from .nq file ────────────────────────────────────────────
print("OXIGRAPH: loading from .nq file...")
t0 = time.perf_counter()
store = OxigraphStore()
cg = ConjunctiveGraph(store=store)
cg.parse(nq_path, format="nquads")
load_ms = (time.perf_counter() - t0) * 1000
print(f"  Load time: {load_ms:.0f} ms")

# Count in named graphs (NQuads put data in named graphs, not default)
res = list(cg.query("SELECT (COUNT(*) AS ?c) WHERE { GRAPH ?g { ?s ?p ?o } }"))
print(f"  Triple count (named graphs): {res[0][0]}")

# Queries using GRAPH pattern to match qlever behavior
queries = [
    ("count_all", "SELECT (COUNT(*) AS ?c) WHERE { GRAPH ?g { ?s ?p ?o } }"),
    ("distinct_types", "SELECT DISTINCT ?t WHERE { GRAPH ?g { ?s a ?t } } ORDER BY ?t"),
    ("count_per_type", "SELECT ?t (COUNT(?s) AS ?c) WHERE { GRAPH ?g { ?s a ?t } } GROUP BY ?t ORDER BY DESC(?c)"),
    ("distinct_predicates", "SELECT DISTINCT ?p WHERE { GRAPH ?g { ?s ?p ?o } } ORDER BY ?p"),
    ("voltage_levels",
     "SELECT ?vl ?name WHERE { GRAPH ?g { "
     f"?vl a <{CIM}VoltageLevel> . "
     f"OPTIONAL {{ ?vl <{CIM}IdentifiedObject.name> ?name }} "
     "} }"),
]

print("\nOXIGRAPH query results:")
for name, sparql in queries:
    t0 = time.perf_counter()
    try:
        res = list(cg.query(sparql))
        ms = (time.perf_counter() - t0) * 1000
        print(f"  {name:30s} {ms:8.1f} ms  {len(res)} rows")
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  {name:30s} {ms:8.1f} ms  ERROR: {e}")

# ─── qlever: same queries (note: qlever sees NQuads in default+named) ────────
import subprocess, json, os, tempfile

qlever_bin = os.environ.get("QLEVER_BIN",
    os.path.join(os.path.dirname(__file__), "vendor", "qlever", "build", "TestQleverNQ"))

# qlever queries - qlever merges named graphs into default graph for querying
qlever_queries = [
    ("count_all", "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }"),
    ("distinct_types", "SELECT DISTINCT ?t WHERE { ?s a ?t } ORDER BY ?t"),
    ("count_per_type", "SELECT ?t (COUNT(?s) AS ?c) WHERE { ?s a ?t } GROUP BY ?t ORDER BY DESC(?c)"),
    ("distinct_predicates", "SELECT DISTINCT ?p WHERE { ?s ?p ?o } ORDER BY ?p"),
    ("voltage_levels",
     "SELECT ?vl ?name WHERE { "
     f"?vl a <{CIM}VoltageLevel> . "
     f"OPTIONAL {{ ?vl <{CIM}IdentifiedObject.name> ?name }} "
     "}"),
]

with tempfile.TemporaryDirectory(prefix="qlever_fair_") as tmpdir:
    index_base = os.path.join(tmpdir, "index")

    # Build index once
    t0 = time.perf_counter()
    proc = subprocess.run(
        [qlever_bin, nq_path, index_base, "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }"],
        capture_output=True, text=True, timeout=120
    )
    build_ms = (time.perf_counter() - t0) * 1000

    # Extract timings
    for line in proc.stdout.split("\n"):
        if "Index built in" in line:
            idx_build = line.split("in")[1].strip().split()[0]
        if "Index loaded in" in line:
            idx_load = line.split("in")[1].strip().split()[0]
        if "result-size-total" in line:
            try:
                j = json.loads(line.replace("Result:", "").strip())
                count = j["results"]["bindings"][0]["c"]["value"]
            except:
                count = "?"

    print(f"\nQLEVER: loading from same .nq file...")
    print(f"  Index build: {idx_build} ms")
    print(f"  Index load: {idx_load} ms")
    print(f"  Triple count: {count}")

    print("\nQLEVER query results:")
    for name, sparql in qlever_queries:
        proc = subprocess.run(
            [qlever_bin, nq_path, index_base, sparql],
            capture_output=True, text=True, timeout=60
        )
        # Parse
        query_ms = "?"
        result_count = "?"
        for line in proc.stdout.split("\n"):
            if line.strip().startswith("{"):
                try:
                    j = json.loads(line.strip())
                    query_ms = j.get("meta", {}).get("query-time-ms", "?")
                    result_count = len(j.get("results", {}).get("bindings", []))
                except:
                    pass
            if "Result:" in line:
                json_part = line.replace("Result:", "").strip()
                if json_part:
                    try:
                        j = json.loads(json_part)
                        query_ms = j.get("meta", {}).get("query-time-ms", "?")
                        result_count = len(j.get("results", {}).get("bindings", []))
                    except:
                        pass
            if "Result has size" in line:
                parts = line.split("size")[1].strip().split("x")
                result_count = int(parts[0].strip())

        print(f"  {name:30s} {str(query_ms):>8s} ms  {result_count} rows")
