"""
Benchmark: qlever (libqlever C++) vs oxigraph (pyoxigraph) for SPARQL queries.

Converts CGMES RDF/XML test data to N-Quads, loads into both engines,
and runs the same SPARQL queries to compare performance.

Usage:
    python benchmark_sparql_engines.py
"""

import time
import tempfile
import subprocess
import os
import sys
import json
from pathlib import Path

import pandas as pd
import triplets

# ─── Data paths ───────────────────────────────────────────────────────────────

EQ_FILE = Path("test_data/TestConfigurations_packageCASv2.0/RealGrid/"
               "CGMES_v2.4.15_RealGridTestConfiguration_v2/"
               "CGMES_v2.4.15_RealGridTestConfiguration_EQ_V2.xml")

SHACL_DIR = Path("test_data/entsoe-profiles/CGMES/3.0.0/SHACL")

QLEVER_BIN = Path(os.environ.get("QLEVER_BIN",
    os.path.join(os.path.dirname(__file__), "vendor", "qlever", "build", "TestQleverNQ")))

CIM_NS = "http://iec.ch/TC57/CIM100#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

# ─── Test queries ─────────────────────────────────────────────────────────────

BENCHMARK_QUERIES = [
    {
        "name": "count_all_triples",
        "sparql": "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }",
    },
    {
        "name": "distinct_types",
        "sparql": "SELECT DISTINCT ?type WHERE { ?s a ?type } ORDER BY ?type",
    },
    {
        "name": "count_per_type",
        "sparql": "SELECT ?type (COUNT(?s) AS ?count) WHERE { ?s a ?type } GROUP BY ?type ORDER BY DESC(?count)",
    },
    {
        "name": "distinct_predicates",
        "sparql": "SELECT DISTINCT ?p WHERE { ?s ?p ?o } ORDER BY ?p",
    },
    {
        "name": "subjects_with_most_triples",
        "sparql": "SELECT ?s (COUNT(?p) AS ?count) WHERE { ?s ?p ?o } GROUP BY ?s ORDER BY DESC(?count) LIMIT 20",
    },
    {
        "name": "find_connectivity_nodes",
        "sparql": f"SELECT ?cn ?name WHERE {{ ?cn a <{CIM_NS}ConnectivityNode> . OPTIONAL {{ ?cn <{CIM_NS}IdentifiedObject.name> ?name }} }} LIMIT 100",
    },
    {
        "name": "join_terminal_to_equipment",
        "sparql": f"""SELECT ?terminal ?equipment ?eqType WHERE {{
            ?terminal a <{CIM_NS}Terminal> .
            ?terminal <{CIM_NS}Terminal.ConductingEquipment> ?equipment .
            ?equipment a ?eqType .
        }} LIMIT 100""",
    },
    {
        "name": "voltage_levels",
        "sparql": f"""SELECT ?vl ?name ?voltage WHERE {{
            ?vl a <{CIM_NS}VoltageLevel> .
            OPTIONAL {{ ?vl <{CIM_NS}IdentifiedObject.name> ?name }}
            OPTIONAL {{ ?vl <{CIM_NS}VoltageLevel.BaseVoltage> ?bv .
                        ?bv <{CIM_NS}BaseVoltage.nominalVoltage> ?voltage }}
        }}""",
    },
    {
        "name": "count_aclineSeg_by_baseVoltage",
        "sparql": f"""SELECT ?bvName (COUNT(?line) AS ?count) WHERE {{
            ?line a <{CIM_NS}ACLineSegment> .
            ?line <{CIM_NS}ConductingEquipment.BaseVoltage> ?bv .
            OPTIONAL {{ ?bv <{CIM_NS}IdentifiedObject.name> ?bvName }}
        }} GROUP BY ?bvName ORDER BY DESC(?count)""",
    },
    {
        "name": "complex_3hop_join",
        "sparql": f"""SELECT ?sub ?bay ?vl ?subName WHERE {{
            ?sub a <{CIM_NS}Substation> .
            ?bay <{CIM_NS}Bay.Substation> ?sub .
            ?bay <{CIM_NS}Bay.VoltageLevel> ?vl .
            OPTIONAL {{ ?sub <{CIM_NS}IdentifiedObject.name> ?subName }}
        }} LIMIT 50""",
    },
]


# ─── N-Quads conversion ──────────────────────────────────────────────────────

def _is_uri(value: str) -> bool:
    return (value.startswith("http://") or value.startswith("https://") or
            value.startswith("urn:") or value.startswith("#"))

def _is_uuid(value: str) -> bool:
    import re
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', value))

def dataframe_to_nquads(df, output_path: str) -> int:
    """Convert triplets DataFrame [ID, KEY, VALUE, INSTANCE_ID] to N-Quads."""
    count = 0
    with open(output_path, 'w') as f:
        for _, row in df.iterrows():
            sid, key, val, gid = str(row.iloc[0]), str(row.iloc[1]), str(row.iloc[2]), str(row.iloc[3])

            # Subject
            subj = f"<urn:uuid:{sid}>" if not sid.startswith("http") else f"<{sid}>"

            # Predicate
            if key == "Type":
                pred = f"<{RDF_TYPE}>"
            elif key.startswith("http://"):
                pred = f"<{key}>"
            else:
                pred = f"<{CIM_NS}{key}>"

            # Object
            if key == "Type":
                obj = f"<{CIM_NS}{val}>" if not val.startswith("http") else f"<{val}>"
            elif _is_uri(val):
                obj = f"<{CIM_NS}{val[1:]}>" if val.startswith("#") else f"<{val}>"
            elif _is_uuid(val):
                obj = f"<urn:uuid:{val}>"
            else:
                escaped = val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                obj = f'"{escaped}"'

            # Graph
            graph = f"<urn:uuid:{gid}>"

            f.write(f"{subj} {pred} {obj} {graph} .\n")
            count += 1
    return count


# ─── Oxigraph benchmark ──────────────────────────────────────────────────────

def benchmark_oxigraph(df, queries):
    """Benchmark oxigraph with same data and queries."""
    from rdflib import Graph, Namespace, Literal, URIRef
    from rdflib.namespace import RDF, XSD
    from oxrdflib import OxigraphStore

    results = {"engine": "oxigraph", "queries": []}

    # Load data
    t0 = time.perf_counter()
    store = OxigraphStore()
    graph = Graph(store=store)

    for _, row in df.iterrows():
        sid, key, val = str(row.iloc[0]), str(row.iloc[1]), str(row.iloc[2])
        subj = URIRef(f"urn:uuid:{sid}") if not sid.startswith("http") else URIRef(sid)

        if key == "Type":
            obj = URIRef(f"{CIM_NS}{val}") if not val.startswith("http") else URIRef(val)
            graph.add((subj, RDF.type, obj))
        elif _is_uri(val):
            obj = URIRef(f"{CIM_NS}{val[1:]}") if val.startswith("#") else URIRef(val)
            pred = URIRef(f"{CIM_NS}{key}" if not key.startswith("http") else key)
            graph.add((subj, pred, obj))
        elif _is_uuid(val):
            pred = URIRef(f"{CIM_NS}{key}" if not key.startswith("http") else key)
            graph.add((subj, pred, URIRef(f"urn:uuid:{val}")))
        else:
            pred = URIRef(f"{CIM_NS}{key}" if not key.startswith("http") else key)
            graph.add((subj, pred, Literal(val)))

    results["load_time_ms"] = (time.perf_counter() - t0) * 1000
    results["triple_count"] = len(graph)

    # Run queries
    for q in queries:
        t0 = time.perf_counter()
        try:
            qres = list(graph.query(q["sparql"]))
            elapsed_ms = (time.perf_counter() - t0) * 1000
            results["queries"].append({
                "name": q["name"],
                "time_ms": round(elapsed_ms, 2),
                "result_count": len(qres),
            })
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            results["queries"].append({
                "name": q["name"],
                "time_ms": round(elapsed_ms, 2),
                "error": str(e)[:100],
            })

    results["total_query_time_ms"] = round(sum(q["time_ms"] for q in results["queries"]), 2)
    return results


# ─── qlever benchmark ────────────────────────────────────────────────────────

def benchmark_qlever(nq_path, queries):
    """Benchmark qlever: build index, load, run queries."""
    results = {"engine": "qlever", "queries": []}

    with tempfile.TemporaryDirectory(prefix="qlever_bench_") as tmpdir:
        index_base = os.path.join(tmpdir, "index")

        # Build and load index (measured by C++ binary)
        # First run just the count query to measure index build + load
        proc = subprocess.run(
            [str(QLEVER_BIN), nq_path, index_base,
             "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }"],
            capture_output=True, text=True, timeout=300
        )
        if proc.returncode != 0:
            results["error"] = proc.stderr[-500:]
            return results

        # Parse timings from output
        for line in proc.stdout.split("\n"):
            if "Index built in" in line:
                results["index_build_ms"] = int(line.split("in")[1].strip().split()[0])
            elif "Index loaded in" in line:
                results["index_load_ms"] = int(line.split("in")[1].strip().split()[0])

        # Parse triple count from JSON result
        for line in proc.stdout.split("\n"):
            if '"count"' in line and '"value"' in line:
                try:
                    j = json.loads(line)
                    results["triple_count"] = int(j["results"]["bindings"][0]["count"]["value"])
                except:
                    pass

        # Now run each query individually (index rebuild each time — not ideal
        # but gives us per-query timing)
        for q in queries:
            proc = subprocess.run(
                [str(QLEVER_BIN), nq_path, index_base, q["sparql"]],
                capture_output=True, text=True, timeout=60
            )
            if proc.returncode != 0:
                results["queries"].append({
                    "name": q["name"],
                    "error": proc.stderr[-200:],
                })
                continue

            # Parse query time from JSON result metadata
            query_ms = None
            result_count = None
            for line in proc.stdout.split("\n"):
                if line.startswith("Query completed in"):
                    query_ms = int(line.split("in")[1].strip().split()[0])
                if line.startswith("{") or line.startswith("Result:"):
                    try:
                        json_str = line.replace("Result:", "").strip()
                        if json_str:
                            j = json.loads(json_str)
                            if "meta" in j:
                                query_ms = j["meta"].get("query-time-ms", query_ms)
                            if "results" in j:
                                result_count = len(j["results"].get("bindings", []))
                    except:
                        pass

            results["queries"].append({
                "name": q["name"],
                "time_ms": query_ms or 0,
                "result_count": result_count,
            })

    results["total_query_time_ms"] = round(sum(q.get("time_ms", 0) for q in results["queries"]), 2)
    return results


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("SPARQL Engine Benchmark: qlever (C++) vs oxigraph (Rust/Python)")
    print("=" * 70)

    # Check prerequisites
    if not EQ_FILE.exists():
        print(f"ERROR: Test file not found: {EQ_FILE}")
        sys.exit(1)
    if not QLEVER_BIN.exists():
        print(f"ERROR: qlever binary not found: {QLEVER_BIN}")
        sys.exit(1)

    # Load data
    print(f"\nLoading: {EQ_FILE.name}")
    df = triplets.rdf_parser.load_all_to_dataframe([str(EQ_FILE)])
    print(f"  {len(df)} triplets")

    # Convert to N-Quads for qlever
    nq_path = "/tmp/cgmes_eq.nq"
    print(f"\nConverting to N-Quads...")
    t0 = time.perf_counter()
    count = dataframe_to_nquads(df, nq_path)
    conv_ms = (time.perf_counter() - t0) * 1000
    nq_size = os.path.getsize(nq_path)
    print(f"  {count} quads, {nq_size/1024/1024:.1f} MB, conversion: {conv_ms:.0f} ms")

    queries = BENCHMARK_QUERIES
    print(f"\nRunning {len(queries)} benchmark queries...")

    # Oxigraph
    print("\n" + "-" * 70)
    print("OXIGRAPH (pyoxigraph via oxrdflib)")
    print("-" * 70)
    try:
        ox = benchmark_oxigraph(df, queries)
        print(f"  Load time:        {ox['load_time_ms']:.0f} ms")
        print(f"  Triple count:     {ox['triple_count']}")
        print(f"  Total query time: {ox['total_query_time_ms']:.1f} ms")
        print()
        for q in ox["queries"]:
            status = f"{q['result_count']} rows" if "result_count" in q else f"ERROR: {q.get('error','?')}"
            print(f"  {q['name']:40s} {q['time_ms']:8.1f} ms  {status}")
    except ImportError:
        print("  SKIPPED: oxrdflib not installed")
        ox = None

    # qlever
    print("\n" + "-" * 70)
    print("QLEVER (libqlever C++)")
    print("-" * 70)
    ql = benchmark_qlever(nq_path, queries)
    if "error" in ql:
        print(f"  ERROR: {ql['error']}")
    else:
        print(f"  Index build:      {ql.get('index_build_ms', '?')} ms")
        print(f"  Index load:       {ql.get('index_load_ms', '?')} ms")
        print(f"  Triple count:     {ql.get('triple_count', '?')}")
        print(f"  Total query time: {ql['total_query_time_ms']:.1f} ms")
        print()
        for q in ql["queries"]:
            if "error" in q:
                print(f"  {q['name']:40s}  ERROR: {q['error'][:60]}")
            else:
                status = f"{q.get('result_count', '?')} rows" if q.get('result_count') is not None else ""
                print(f"  {q['name']:40s} {q.get('time_ms', 0):8.1f} ms  {status}")

    # Summary
    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    if ox:
        print(f"  {'':40s} {'Oxigraph':>10s} {'qlever':>10s} {'Speedup':>10s}")
        print(f"  {'Data loading':40s} {ox['load_time_ms']:>9.0f}ms {ql.get('index_build_ms', 0):>9d}ms")
        if ox["queries"] and ql["queries"]:
            for oq, qq in zip(ox["queries"], ql["queries"]):
                if "error" not in oq and "error" not in qq and qq.get("time_ms", 0) > 0:
                    speedup = oq["time_ms"] / qq["time_ms"] if qq["time_ms"] > 0 else float('inf')
                    print(f"  {oq['name']:40s} {oq['time_ms']:>9.1f}ms {qq['time_ms']:>9.1f}ms {speedup:>9.1f}x")
                elif "error" in oq:
                    print(f"  {oq['name']:40s} {'ERROR':>10s}")
                elif "error" in qq:
                    print(f"  {oq['name']:40s} {oq['time_ms']:>9.1f}ms {'ERROR':>10s}")


if __name__ == "__main__":
    main()
