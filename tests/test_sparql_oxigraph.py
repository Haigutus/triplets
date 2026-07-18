"""Parity tests for the pyoxigraph SPARQL engine (vs the rdflib reference).

Skip entirely when pyoxigraph is not installed — the registry then auto-falls
back to rdflib and nothing else changes.
"""
import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyoxigraph")

import pandas
import triplets

from _parity import SVEDALA_DIR, SVEDALA_FILES, SKIP_REASON

PREFIXES = ("PREFIX cim: <http://iec.ch/TC57/CIM100#> "
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> ")


@pytest.fixture(scope="module")
def svedala():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


@pytest.fixture
def no_qlever(monkeypatch):
    """Engine registry as seen by a pip-only install (no compiled extension)."""
    monkeypatch.setattr(triplets.sparql._REGISTRY, "auto",
                        [name for name in triplets.sparql._REGISTRY.auto if name != "qlever"])


def test_auto_order(no_qlever):
    """Without the qlever extension, auto upgrades from rdflib to oxigraph."""
    assert triplets.sparql.get_engine("auto")[0] == "oxigraph"


def test_select_parity(svedala):
    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    reference = int(triplets.sparql.query(svedala, q, engine="rdflib")["n"].iloc[0])
    fast = int(triplets.sparql.query(svedala, q, engine="oxigraph")["n"].iloc[0])
    assert fast == reference > 0


def test_select_columns_and_rows(svedala):
    result = triplets.sparql.query(
        svedala, PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name } LIMIT 5",
        engine="oxigraph")
    assert list(result.columns) == ["s", "name"]
    assert len(result) == 5


def test_ask(svedala):
    assert triplets.sparql.query(svedala, PREFIXES + "ASK { ?s rdf:type cim:Substation }",
                                 engine="oxigraph") is True
    assert triplets.sparql.query(svedala, PREFIXES + "ASK { ?s rdf:type cim:NoSuchClass }",
                                 engine="oxigraph") is False


def test_values_are_lexical_strings(svedala):
    """All SELECT values are strings (triplets are all-string; consumers
    cast) — typed literals keep their lexical form, no dtype inference.
    The SPARQL-CSV serializer is lexical forms by definition."""
    from triplets.export_schema import schemas
    result = triplets.sparql.query(
        svedala, PREFIXES + "SELECT ?l WHERE { ?s cim:Conductor.length ?l } LIMIT 1",
        rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, engine="oxigraph")
    value = result["l"].iloc[0]
    assert isinstance(value, str)
    assert float(value) > 0


def test_return_type_flavors(svedala):
    """return_type: "auto" matches the input flavor; explicit "polars" /
    "arrow" / "pandas" honored. The polars path decodes the SPARQL-CSV
    directly (no pandas hop)."""
    polars = pytest.importorskip("polars")
    import pyarrow
    q = PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name } LIMIT 5"
    assert isinstance(triplets.sparql.query(svedala, q, engine="oxigraph"), pandas.DataFrame)
    assert isinstance(triplets.sparql.query(svedala, q, return_type="polars", engine="oxigraph"),
                      polars.DataFrame)
    assert isinstance(triplets.sparql.query(svedala, q, return_type="arrow", engine="oxigraph"),
                      pyarrow.Table)
    auto = triplets.sparql.query(polars.from_pandas(svedala), q, engine="oxigraph")
    assert isinstance(auto, polars.DataFrame)
    assert auto.height == 5


def test_arrow_input(svedala):
    """A bare pyarrow table (no registered methods) is accepted as data —
    converted at the engine boundary, same answers as the pandas input."""
    import pyarrow
    table = pyarrow.Table.from_pandas(svedala, preserve_index=False)
    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    via_arrow = int(triplets.sparql.query(table, q, engine="oxigraph")["n"].iloc[0])
    via_pandas = int(triplets.sparql.query(svedala, q, engine="oxigraph")["n"].iloc[0])
    assert via_arrow == via_pandas > 0


def test_polars_select_parity(svedala):
    """The direct polars CSV decode returns the same values as the pandas path."""
    polars = pytest.importorskip("polars")
    q = PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name }"
    via_pandas = triplets.sparql.query(svedala, q, engine="oxigraph")
    via_polars = triplets.sparql.query(svedala, q, return_type="polars", engine="oxigraph")
    assert sorted(map(tuple, via_pandas.values.tolist())) == sorted(map(tuple, via_polars.rows()))


def test_unbound_values_are_null(svedala):
    """An OPTIONAL variable with no binding is null. Caveat pinned here: the
    SPARQL-CSV empty field cannot distinguish unbound from an empty-string
    literal — both decode to null (the W3C CSV-results tradeoff)."""
    q = PREFIXES + ("SELECT ?s ?missing WHERE { ?s rdf:type cim:ACLineSegment "
                    "OPTIONAL { ?s cim:NoSuch.key ?missing } } LIMIT 3")
    result = triplets.sparql.query(svedala, q, return_type="arrow", engine="oxigraph")
    assert result["missing"].null_count == 3


def test_construct_returns_triplets(svedala):
    result = triplets.sparql.query(
        svedala,
        PREFIXES + "CONSTRUCT { ?s rdf:type cim:ACLineSegment } WHERE { ?s rdf:type cim:ACLineSegment }",
        engine="oxigraph")
    assert list(result.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    assert (result["KEY"] == "Type").all()


def test_construct_parity_with_rdflib(svedala):
    """The N-Quads round-trip CONSTRUCT conversion (serialize Rust-side,
    decode via read_nquads) produces the same triplet frame as the rdflib
    engine: uuid stripped, CIM shortened, literal values in lexical form."""
    q = PREFIXES + ("CONSTRUCT { ?s cim:IdentifiedObject.name ?n } "
                    "WHERE { ?s rdf:type cim:ACLineSegment . ?s cim:IdentifiedObject.name ?n }")
    order = ["ID", "KEY", "VALUE"]
    oxigraph = triplets.sparql.query(svedala, q, engine="oxigraph").sort_values(order).reset_index(drop=True)
    rdflib = triplets.sparql.query(svedala, q, engine="rdflib").sort_values(order).reset_index(drop=True)
    pandas.testing.assert_frame_equal(oxigraph, rdflib, check_dtype=False)


def test_scope_parity(svedala):
    instances = svedala[(svedala["KEY"] == "Type") & (svedala["VALUE"] == "ACLineSegment")]["INSTANCE_ID"]
    eq_instance = str(instances.astype(str).iloc[0])
    other = next(i for i in set(svedala["INSTANCE_ID"].astype(str).unique()) if i != eq_instance)

    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    in_scope = int(triplets.sparql.query(svedala, q, scope=[eq_instance], engine="oxigraph")["n"].iloc[0])
    out_scope = int(triplets.sparql.query(svedala, q, scope=[other], engine="oxigraph")["n"].iloc[0])
    reference = int(triplets.sparql.query(svedala, q, scope=[eq_instance], engine="rdflib")["n"].iloc[0])
    assert in_scope == reference > 0
    assert out_scope == 0


def test_scope_shares_store(svedala):
    """Scope is SPARQL-protocol default graphs at query time, not a data
    operation — scoped and unscoped queries resolve to the same cached
    store, and the query text is never modified."""
    from triplets.sparql import sparql_oxigraph
    q = PREFIXES + "ASK { ?s rdf:type cim:Substation }"
    triplets.sparql.query(svedala, q, engine="oxigraph")
    cached = len(sparql_oxigraph._STORES)
    instance = str(svedala["INSTANCE_ID"].astype(str).iloc[0])
    triplets.sparql.query(svedala, q, scope=[instance], engine="oxigraph")
    assert len(sparql_oxigraph._STORES) == cached


def test_scope_overrides_own_from(svedala):
    """Per the SPARQL protocol, an externally supplied dataset (scope) takes
    precedence over FROM inside the query — the query's own FROM cannot
    broaden a scoped query."""
    instances = svedala[(svedala["KEY"] == "Type") & (svedala["VALUE"] == "ACLineSegment")]["INSTANCE_ID"]
    eq_instance = str(instances.astype(str).iloc[0])
    other = next(i for i in set(svedala["INSTANCE_ID"].astype(str).unique()) if i != eq_instance)

    q = PREFIXES + (f"SELECT (COUNT(?s) AS ?n) FROM <urn:uuid:{eq_instance}> "
                    "WHERE { ?s rdf:type cim:ACLineSegment }")
    unscoped = int(triplets.sparql.query(svedala, q, engine="oxigraph")["n"].iloc[0])
    scoped = int(triplets.sparql.query(svedala, q, scope=[other], engine="oxigraph")["n"].iloc[0])
    assert unscoped > 0        # the query's own FROM sees the EQ instance
    assert scoped == 0         # scope replaces it entirely


def test_store_cache_reused(svedala):
    from triplets.sparql import sparql_oxigraph
    q = PREFIXES + "ASK { ?s rdf:type cim:Substation }"
    triplets.sparql.query(svedala, q, engine="oxigraph")
    cached = len(sparql_oxigraph._STORES)
    triplets.sparql.query(svedala, q, engine="oxigraph")   # same data → same store object
    assert len(sparql_oxigraph._STORES) == cached


def test_store_shared_across_row_order(svedala):
    """content_hash is row-order-invariant → shuffled input of the same flavor
    resolves to the same cached store."""
    from triplets.sparql import sparql_oxigraph
    q = PREFIXES + "ASK { ?s rdf:type cim:Substation }"
    triplets.sparql.query(svedala, q, engine="oxigraph")
    cached = len(sparql_oxigraph._STORES)
    shuffled = svedala.sample(frac=1, random_state=3).reset_index(drop=True)
    triplets.sparql.query(shuffled, q, engine="oxigraph")
    assert len(sparql_oxigraph._STORES) == cached


def test_invalid_query_error_carries_query_text(svedala):
    """No query fixing: a rejected query raises with oxigraph's message + the
    query. (The ENTSO-E bare-HAVING shape that qlever rejects is *accepted*
    by oxigraph — implicit grouping — so the probe here is a syntax error.)"""
    bad = PREFIXES + "SELECT ?s WHERE { ?s rdf:type cim:Substation"
    with pytest.raises(ValueError, match="(?s)oxigraph rejected the query.*Substation"):
        triplets.sparql.query(svedala, bad, engine="oxigraph")


# ── union / duplicate semantics (the reason the default graph is projected) ──

DUPLICATED = pandas.DataFrame(
    [
        ("11111111-2222-3333-4444-555555555555", "Type", "ACLineSegment", "g1"),
        ("11111111-2222-3333-4444-555555555555", "IdentifiedObject.name", "Line 1", "g1"),
        ("11111111-2222-3333-4444-555555555555", "IdentifiedObject.name", "Line 1", "g2"),
        ("22222222-2222-3333-4444-555555555555", "Type", "Substation", "g2"),
    ],
    columns=["ID", "KEY", "VALUE", "INSTANCE_ID"],
)


def test_union_deduplicates():
    """A triple present in several named graphs is one solution in an
    unscoped query — the load-time default-graph projection gives rdflib's
    default_union set semantics (oxigraph's own union keeps one solution
    per graph)."""
    q = PREFIXES + "SELECT ?name WHERE { ?s cim:IdentifiedObject.name ?name }"
    result = triplets.sparql.query(DUPLICATED, q, engine="oxigraph")
    reference = triplets.sparql.query(DUPLICATED, q, engine="rdflib")
    assert len(result) == len(reference) == 1


def test_multi_instance_scope_duplicates_documented():
    """Pinned caveat: a multi-instance scope is a SPARQL-protocol dataset
    union — a triple present in several scoped instances yields one solution
    per instance (DISTINCT dedupes). Single-instance scope is exact."""
    q = PREFIXES + "SELECT ?name WHERE { ?s cim:IdentifiedObject.name ?name }"
    multi = triplets.sparql.query(DUPLICATED, q, scope=["g1", "g2"], engine="oxigraph")
    assert len(multi) == 2                                   # the documented residual
    distinct = triplets.sparql.query(
        DUPLICATED, PREFIXES + "SELECT DISTINCT ?name WHERE { ?s cim:IdentifiedObject.name ?name }",
        scope=["g1", "g2"], engine="oxigraph")
    assert len(distinct) == 1
    single = triplets.sparql.query(DUPLICATED, q, scope=["g1"], engine="oxigraph")
    assert len(single) == 1


# ── sh:sparql delegation (the SHACL engines ride the auto SPARQL engine) ────

def shape_graph(select):
    rdflib = pytest.importorskip("rdflib")
    graph = rdflib.Graph()
    graph.parse(data=f"""
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [
        sh:path cim:IdentifiedObject.name ;
        sh:sparql [ sh:select '{select}' ] ;
    ] .
""", format="turtle")
    return graph


def test_shacl_sparql_delegation_via_oxigraph(no_qlever, svedala):
    """sh:sparql constraints in the vectorized SHACL engines ride on oxigraph
    automatically when it is the auto engine (clean data → 0 violations,
    but the query executed)."""
    pytest.importorskip("pyshacl")
    assert triplets.sparql.get_engine("auto")[0] == "oxigraph"
    graph = shape_graph('SELECT $this ?value WHERE { $this $PATH ?value . '
                        'FILTER (str(?value) = "no-such-name") }')
    violations = triplets.validation.validate(svedala, graph, engine="pandas")
    assert len(violations.loc[violations["VIOLATION_TYPE"] == "sh:sparql"]) == 0


def test_shacl_sparql_positive_violations_parity(no_qlever, svedala):
    """A rule that *finds* rows: the oxigraph CSV path produces the same
    violation IDs as the rdflib reference path (urn:uuid: stripping included)."""
    pytest.importorskip("pyshacl")
    graph = shape_graph("SELECT $this ?value WHERE { $this $PATH ?value }")

    via_oxigraph = triplets.validation.validate(svedala, graph, engine="pandas")
    import triplets.sparql as sparql_registry
    saved = sparql_registry._REGISTRY.auto
    sparql_registry._REGISTRY.auto = ["rdflib"]
    try:
        via_rdflib = triplets.validation.validate(svedala, graph, engine="pandas")
    finally:
        sparql_registry._REGISTRY.auto = saved

    key = ["ID", "VIOLATION_TYPE", "VALUE"]
    left = via_oxigraph[via_oxigraph["VIOLATION_TYPE"] == "sh:sparql"].sort_values(key)
    right = via_rdflib[via_rdflib["VIOLATION_TYPE"] == "sh:sparql"].sort_values(key)
    assert len(left) > 0
    assert left[key].reset_index(drop=True).equals(right[key].reset_index(drop=True))


SPARQL_HEAVY_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:IdentifiedObject.name ;
                  sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "no-such-name") }' ] ] ;
    sh:property [ sh:path cim:Conductor.length ;
                  sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (<http://www.w3.org/2001/XMLSchema#float>(str(?value)) < 0) }' ] ] .

cim:TerminalShape a sh:NodeShape ;
    sh:targetClass cim:Terminal ;
    sh:property [ sh:path cim:Terminal.ConductingEquipment ;
                  sh:sparql [ sh:select 'SELECT $this WHERE { $this $PATH ?eq . FILTER NOT EXISTS { ?eq <http://iec.ch/TC57/CIM100#IdentifiedObject.name> ?n } }' ] ] .
"""


@pytest.mark.performance
@pytest.mark.benchmark(group="shacl-sparql-backend")
@pytest.mark.parametrize("sparql_engine", ["rdflib", "oxigraph", "qlever"])
def test_benchmark_shacl_sparql_backend(benchmark, svedala, monkeypatch, sparql_engine):
    """The same sh:sparql-heavy shape validated with each SPARQL backend
    forced as the auto engine — the numbers behind the sh:sparql section in
    docs/validation.md."""
    pytest.importorskip("pyshacl")
    if sparql_engine == "qlever":
        pytest.importorskip("triplets.sparql._qlever", reason="qlever extension not built")
    monkeypatch.setattr(triplets.sparql._REGISTRY, "auto",
                        [name for name in triplets.sparql._REGISTRY.auto
                         if name in (sparql_engine, "rdflib")])

    rdflib = pytest.importorskip("rdflib")
    graph = rdflib.Graph()
    graph.parse(data=SPARQL_HEAVY_SHAPE, format="turtle")
    compiled = triplets.validation.compile(graph)
    benchmark.extra_info.update({"sparql_engine": sparql_engine})
    benchmark(lambda: triplets.validation.validate(svedala, compiled, engine="pandas"))


def test_invalid_constraint_query_reported_not_fixed(no_qlever, svedala):
    """A shape oxigraph's strict parser rejects (ungrouped projection):
    results stay complete via the rdflib fallback, and the report carries a
    triplets:invalidSparql Warning row. (The ENTSO-E bare-HAVING defect that
    qlever rejects is accepted by oxigraph — implicit grouping — so the probe
    here is an ungrouped projection.)"""
    pytest.importorskip("pyshacl")
    graph = shape_graph('SELECT $this ?value WHERE { $this $PATH ?value . '
                        'FILTER (str(?value) = "no-such-name") } GROUP BY $this')
    violations = triplets.validation.validate(svedala, graph, engine="pandas")
    flags = violations[violations["VIOLATION_TYPE"] == "triplets:invalidSparql"]
    assert len(flags) == 1                                       # flagged once, not per fanout
    assert flags["SEVERITY"].iloc[0] == "Warning"
    assert "oxigraph rejected the query" in flags["MESSAGE"].iloc[0]
    assert (violations["VIOLATION_TYPE"] != "sh:sparql").all()   # rdflib fallback found no hits
