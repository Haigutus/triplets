"""SPARQL store/engine comparison on the real Svedala IGM model.

Benchmarks rdflib with every usable store backend registered in this
environment (code unchanged — the store name is the only difference), plus
the triplets qlever and oxigraph engines, over four representative queries.
These are the measurements behind the engine table in docs/sparql.md.

Run:  uv run python examples/sparql_backend_benchmark.py
"""
import io
import time
from pathlib import Path

import pandas
import rdflib
from rdflib.plugin import plugins
from rdflib.store import Store

import triplets  # noqa: F401 — registers accessors

REPO = Path(__file__).resolve().parent.parent
FILES = sorted(str(p) for p in (REPO / "test_data/relicapgrid/Instance/Grid/IGM_Svedala").glob("*.xml"))
RUNS = 3

PREFIX = "PREFIX cim: <http://iec.ch/TC57/CIM100#>\n"
QUERIES = {
    "select+join": PREFIX + "SELECT ?name WHERE { ?s a cim:ACLineSegment ; cim:IdentifiedObject.name ?name . }",
    "group-by-type": "SELECT ?t (COUNT(?s) AS ?n) WHERE { ?s a ?t } GROUP BY ?t ORDER BY DESC(?n) LIMIT 5",
    "2-hop join": PREFIX + ("SELECT DISTINCT ?term ?eqname WHERE { ?term a cim:Terminal ; "
                            "cim:Terminal.ConductingEquipment ?eq . ?eq cim:IdentifiedObject.name ?eqname . }"),
    "ask": PREFIX + "ASK { ?s a cim:ACLineSegment }",
}


def best_of(callable_):
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        callable_()
        times.append(time.perf_counter() - start)
    return min(times) * 1000


def bench_rdflib_store(store_name, nquads):
    start = time.perf_counter()
    dataset = rdflib.Dataset(store=store_name, default_union=True)
    dataset.parse(io.BytesIO(nquads), format="nquads")
    load_ms = (time.perf_counter() - start) * 1000
    cells = [f"load {load_ms:7.0f} ms"]
    for name, query in QUERIES.items():
        ms = best_of(lambda q=query: list(dataset.query(q)))
        cells.append(f"{name} {ms:7.1f} ms")
    print(f"rdflib {store_name:12s} | " + " | ".join(cells))


def bench_engine(data, engine):
    from triplets import sparql
    try:
        sparql.get_engine(engine)
    except ImportError:
        print(f"{engine} engine not available — skipped")
        return
    start = time.perf_counter()
    sparql.query(data, QUERIES["ask"], engine=engine)
    print(f"{engine} first call (state build or cached load): {(time.perf_counter() - start) * 1000:.0f} ms")
    cells = []
    for name, query in QUERIES.items():
        ms = best_of(lambda q=query: sparql.query(data, q, engine=engine, data_unchanged=True))
        cells.append(f"{name} {ms:7.1f} ms")
    print(f"{engine} (data_unchanged) | " + " | ".join(cells))


def main():
    data = pandas.read_RDF(FILES)
    buffer = data.export_to_nquads(export_to_memory=True)
    nquads = buffer.read()
    print(f"{len(data)} rows, {len(nquads) / 1e6:.1f} MB N-Quads\n")

    usable = ("Memory", "Oxigraph")   # context-aware, local stores
    available = {p.name for p in plugins(kind=Store)}
    for store_name in usable:
        if store_name in available:
            bench_rdflib_store(store_name, nquads)
        else:
            print(f"rdflib {store_name:12s} | not installed")

    bench_engine(data, "oxigraph")
    bench_engine(data, "qlever")


if __name__ == "__main__":
    main()
