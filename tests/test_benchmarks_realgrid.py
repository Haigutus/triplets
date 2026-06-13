"""Performance benchmarks for parsing RealGrid and tools operations (pandas, polars, duckdb).

Run with:
  python -m pytest tests/test_benchmarks_realgrid.py --benchmark-only -v

  python -m pytest tests/test_benchmarks_realgrid.py --benchmark-only \
    --benchmark-json=tests/performance_results/parsers_performance.json -q -k "parse"
"""
import pytest
import pandas
import polars

from triplets.parser import parse

try:
    import pyarrow  # noqa: F401
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

try:
    from triplets.parser import cython_pugixml_arrow  # noqa: F401
    HAS_CYTHON_PUGIXML_ARROW = True
except Exception:
    HAS_CYTHON_PUGIXML_ARROW = False

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

REALGRID_ZIP = "test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"
TYPE_FOR_VIEW = "ACLineSegment"

_PARSE_ENGINES = ["python_lxml_pandas"]
if HAS_PYARROW:
    _PARSE_ENGINES.append("python_lxml_arrow")
if HAS_CYTHON_PUGIXML_ARROW:
    _PARSE_ENGINES.append("cython_pugixml_arrow")


# ── Parse benchmarks ────────────────────────────────────────────────────────

@pytest.mark.benchmark(group="parse-realgrid")
@pytest.mark.parametrize("engine", _PARSE_ENGINES)
def test_parse_realgrid_to_pandas(benchmark, engine):
    benchmark.extra_info.update({"engine": engine, "return": "pandas"})
    df = benchmark(lambda: parse(REALGRID_ZIP, engine=engine, return_type="pandas"))
    assert len(df) > 1_000_000


@pytest.mark.benchmark(group="parse-realgrid")
@pytest.mark.parametrize("engine", _PARSE_ENGINES)
def test_parse_realgrid_to_polars(benchmark, engine):
    benchmark.extra_info.update({"engine": engine, "return": "polars"})
    pdf = benchmark(lambda: parse(REALGRID_ZIP, engine=engine, return_type="polars"))
    assert len(pdf) > 1_000_000


@pytest.mark.benchmark(group="parse-realgrid")
@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_parse_realgrid_to_duckdb(benchmark):
    """Parse RealGrid and load into DuckDB via Arrow (zero-copy)."""
    import triplets
    benchmark.extra_info.update({"engine": "duckdb", "return": "duckdb"})
    def _run():
        data = duckdb.connect()
        data.read_rdf([REALGRID_ZIP])
        return data
    data = benchmark(_run)
    assert data.execute("SELECT COUNT(*) FROM triplets").fetchone()[0] > 1_000_000


# ── type_tableview benchmarks ───────────────────────────────────────────────

@pytest.mark.benchmark(group="type-tableview")
def test_type_tableview_pandas(benchmark):
    df = parse(REALGRID_ZIP, engine=_PARSE_ENGINES[-1], return_type="pandas")
    benchmark.extra_info.update({"engine": "pandas"})
    view = benchmark(lambda: df.type_tableview(TYPE_FOR_VIEW, string_to_number=False))
    assert view is not None and len(view) > 0


@pytest.mark.benchmark(group="type-tableview")
def test_type_tableview_polars(benchmark):
    from triplets.tools import polars_engine
    pdf = parse(REALGRID_ZIP, engine=_PARSE_ENGINES[-1], return_type="polars")
    benchmark.extra_info.update({"engine": "polars"})
    view = benchmark(lambda: polars_engine.type_tableview(pdf, TYPE_FOR_VIEW, string_to_number=False))
    assert view is not None and len(view) > 0


@pytest.mark.benchmark(group="type-tableview")
@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_type_tableview_duckdb(benchmark):
    import triplets
    data = duckdb.connect()
    data.read_rdf([REALGRID_ZIP])
    benchmark.extra_info.update({"engine": "duckdb"})
    view = benchmark(lambda: data.type_tableview(TYPE_FOR_VIEW).df())
    assert view is not None and len(view) > 0


# ── filter_triplets_by_type benchmarks ──────────────────────────────────────────────

@pytest.mark.benchmark(group="filter-by-type")
def test_filter_by_type_pandas(benchmark):
    import triplets
    df = parse(REALGRID_ZIP, engine=_PARSE_ENGINES[-1], return_type="pandas")
    benchmark.extra_info.update({"engine": "pandas"})
    result = benchmark(lambda: triplets.tools.filter_triplets_by_type(df, TYPE_FOR_VIEW, engine="pandas"))
    assert len(result) > 0


@pytest.mark.benchmark(group="filter-by-type")
def test_filter_by_type_polars(benchmark):
    import triplets
    pdf = parse(REALGRID_ZIP, engine=_PARSE_ENGINES[-1], return_type="polars")
    benchmark.extra_info.update({"engine": "polars"})
    result = benchmark(lambda: triplets.tools.filter_triplets_by_type(pdf, TYPE_FOR_VIEW, engine="polars"))
    assert len(result) > 0


@pytest.mark.benchmark(group="filter-by-type")
@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_filter_by_type_duckdb(benchmark):
    import triplets
    data = duckdb.connect()
    data.read_rdf([REALGRID_ZIP])
    benchmark.extra_info.update({"engine": "duckdb"})
    result = benchmark(lambda: data.filter_triplets_by_type(TYPE_FOR_VIEW).df())
    assert len(result) > 0


# ── types_dict benchmarks ──────────────────────────────────────────────────

@pytest.mark.benchmark(group="types-dict")
def test_types_dict_pandas(benchmark):
    import triplets
    df = parse(REALGRID_ZIP, engine=_PARSE_ENGINES[-1], return_type="pandas")
    benchmark.extra_info.update({"engine": "pandas"})
    result = benchmark(lambda: triplets.tools.types_dict(df, engine="pandas"))
    assert len(result) > 0


@pytest.mark.benchmark(group="types-dict")
def test_types_dict_polars(benchmark):
    import triplets
    pdf = parse(REALGRID_ZIP, engine=_PARSE_ENGINES[-1], return_type="polars")
    benchmark.extra_info.update({"engine": "polars"})
    result = benchmark(lambda: triplets.tools.types_dict(pdf, engine="polars"))
    assert len(result) > 0


@pytest.mark.benchmark(group="types-dict")
@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")
def test_types_dict_duckdb(benchmark):
    import triplets
    data = duckdb.connect()
    data.read_rdf([REALGRID_ZIP])
    benchmark.extra_info.update({"engine": "duckdb"})
    result = benchmark(lambda: data.types_dict())
    assert len(result) > 0


# ── rdflib load: which serialization feeds rdflib fastest? ──────────────────
# Exports RealGrid as untyped CIM XML, typed CIM XML (rdf:datatype), and
# N-Quads (552 ED2 schema), then parses each with rdflib. Single round —
# each load takes ~20s on 1.08M triples.

@pytest.fixture(scope="module")
def realgrid_serializations(tmp_path_factory):
    rdflib = pytest.importorskip("rdflib")  # noqa: F841
    import os
    import triplets  # noqa: F401  (registers exports)
    from triplets.export_schema import schemas

    data = parse(REALGRID_ZIP, return_type="pandas")
    base = tmp_path_factory.mktemp("rdflib_load")
    paths = {}
    for kind, kwargs in (
        ("untyped", {}),
        ("typed", {"datatypes": True}),
        # strict: no non-namespaced meta-object elements — required by pyoxigraph's parser
        ("untyped-strict", {"export_undefined": False}),
        ("typed-strict", {"datatypes": True, "export_undefined": False}),
    ):
        paths[kind] = []
        files = data.export_to_cimxml(
            rdf_map=schemas.ENTSOE_CGMES_2_4_15_552_ED2,
            export_type="xml_per_instance", export_to_memory=True, **kwargs)
        for f in files:
            target = base / kind / os.path.basename(f.name)
            target.parent.mkdir(exist_ok=True)
            f.seek(0)
            target.write_bytes(f.read())
            paths[kind].append(str(target))
    nq = base / "realgrid.nq"
    data.export_to_nquads(str(nq), rdf_map=schemas.ENTSOE_CGMES_2_4_15_552_ED2)
    paths["nquads"] = [str(nq)]
    return paths


@pytest.mark.benchmark(group="rdflib-load")
@pytest.mark.parametrize("kind", ["untyped", "typed", "nquads"])
def test_rdflib_load(benchmark, realgrid_serializations, kind):
    import rdflib

    def load():
        if kind == "nquads":
            graph = rdflib.ConjunctiveGraph()
            graph.parse(realgrid_serializations[kind][0], format="nquads")
        else:
            graph = rdflib.Graph()
            for path in realgrid_serializations[kind]:
                graph.parse(path, format="xml")
        return graph

    benchmark.extra_info.update({"serialization": kind})
    graph = benchmark.pedantic(load, rounds=1, iterations=1)
    assert len(graph) > 1_000_000


@pytest.mark.benchmark(group="rdflib-load")
@pytest.mark.parametrize("kind", ["untyped", "typed", "nquads"])
def test_oxrdflib_load(benchmark, realgrid_serializations, kind):
    """rdflib API with the oxigraph (Rust) storage backend — parsing still
    happens in rdflib's Python parsers, so gains are modest (~10-15%)."""
    pytest.importorskip("oxrdflib")
    import rdflib

    def load():
        if kind == "nquads":
            graph = rdflib.Dataset(store="Oxigraph")
            graph.parse(realgrid_serializations[kind][0], format="nquads")
        else:
            graph = rdflib.Graph(store="Oxigraph")
            for path in realgrid_serializations[kind]:
                graph.parse(path, format="xml")
        return graph

    benchmark.extra_info.update({"serialization": kind, "store": "oxigraph"})
    graph = benchmark.pedantic(load, rounds=1, iterations=1)
    assert len(graph) > 1_000_000


@pytest.mark.benchmark(group="rdflib-load")
@pytest.mark.parametrize("kind", ["untyped-strict", "typed-strict", "nquads"])
def test_pyoxigraph_bulk_load(benchmark, realgrid_serializations, kind):
    """Pure pyoxigraph bulk_load — Rust parser end to end, ~10x faster than
    any rdflib-API load. XML works only with export_undefined=False:
    pyoxigraph's strict parser rejects the non-namespaced meta-object
    elements the lax export contains (rdflib tolerates them)."""
    pyoxigraph = pytest.importorskip("pyoxigraph")

    def load():
        store = pyoxigraph.Store()
        if kind == "nquads":
            store.bulk_load(path=realgrid_serializations[kind][0], format=pyoxigraph.RdfFormat.N_QUADS)
        else:
            for path in realgrid_serializations[kind]:
                store.bulk_load(path=path, format=pyoxigraph.RdfFormat.RDF_XML)
        return store

    benchmark.extra_info.update({"serialization": kind, "store": "pyoxigraph"})
    store = benchmark.pedantic(load, rounds=1, iterations=1)
    assert len(store) > 1_000_000
