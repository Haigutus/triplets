"""Tests for the rdflib reference SPARQL engine (triplets.sparql)."""
import pytest

pytest.importorskip("rdflib")

import pandas
import triplets

from pathlib import Path

from _parity import SVEDALA_DIR, SVEDALA_FILES, SKIP_REASON

PREFIXES = ("PREFIX cim: <http://iec.ch/TC57/CIM100#> "
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> ")


@pytest.fixture(scope="module")
def svedala():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


def test_select_count_matches_tableview(svedala):
    """SPARQL count of a type == type_tableview row count (cross-engine consistency)."""
    result = svedala.sparql.query(
        PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }")
    assert isinstance(result, pandas.DataFrame)
    assert int(result["n"].iloc[0]) == len(svedala.triplets.type_tableview("ACLineSegment"))


def test_select_returns_columns_and_rows(svedala):
    result = svedala.sparql.query(
        PREFIXES + "SELECT ?s ?name WHERE { ?s cim:IdentifiedObject.name ?name } LIMIT 5")
    assert list(result.columns) == ["s", "name"]
    assert len(result) == 5


def test_ask(svedala):
    assert svedala.sparql.query(PREFIXES + "ASK { ?s rdf:type cim:Substation }") is True
    assert svedala.sparql.query(PREFIXES + "ASK { ?s rdf:type cim:NoSuchClass }") is False


def test_construct_returns_triplets(svedala):
    result = svedala.sparql.query(
        PREFIXES + "CONSTRUCT { ?s rdf:type cim:ACLineSegment } WHERE { ?s rdf:type cim:ACLineSegment }")
    assert list(result.columns) == ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    assert (result["KEY"] == "Type").all()


def test_values_are_lexical_strings(svedala):
    """The shared engine contract: all SELECT values are lexical strings
    (triplets are all-string; consumers cast) — swapping engines never
    changes result dtypes. rdf_map still types the *loaded graph* (drives
    comparisons inside the query), not the returned representation."""
    from triplets.export_schema import schemas
    result = svedala.sparql.query(
        PREFIXES + "SELECT ?l WHERE { ?s cim:Conductor.length ?l } LIMIT 1",
        rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, engine="rdflib")
    value = result["l"].iloc[0]
    assert isinstance(value, str)
    assert float(value) > 0


def test_scope_restricts_to_named_graph(svedala):
    """Scoping to the SSH instance finds no ACLineSegment (they live in EQ)."""
    instances = svedala[(svedala["KEY"] == "Type") & (svedala["VALUE"] == "ACLineSegment")]["INSTANCE_ID"]
    eq_instance = str(instances.astype(str).iloc[0])
    all_instances = set(svedala["INSTANCE_ID"].astype(str).unique())
    other = next(i for i in all_instances if i != eq_instance)

    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    in_scope = int(svedala.sparql.query(q, scope=[eq_instance])["n"].iloc[0])
    out_scope = int(svedala.sparql.query(q, scope=[other])["n"].iloc[0])
    assert in_scope > 0
    assert out_scope == 0


def test_dataset_shared_across_row_order_and_scope(svedala):
    """Same logic as the qlever index cache: the loaded Dataset is keyed by
    content (row-order-invariant), and scope is applied after loading — so a
    shuffled frame and a scoped query reuse the cached dataset, no re-export."""
    from triplets import _rdflib_loader
    q = PREFIXES + "ASK { ?s rdf:type cim:Substation }"
    svedala.sparql.query(q)
    cached = len(_rdflib_loader._DATASETS)
    shuffled = svedala.sample(frac=1, random_state=3).reset_index(drop=True)
    triplets.sparql.query(shuffled, q)
    instance = str(svedala["INSTANCE_ID"].astype(str).iloc[0])
    triplets.sparql.query(shuffled, q, scope=[instance])
    assert len(_rdflib_loader._DATASETS) == cached


def test_polars_input_parity(svedala):
    """return_type="auto": polars in → polars out (every engine), same values."""
    polars = pytest.importorskip("polars")
    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    pandas_result = triplets.sparql.query(svedala, q)
    polars_result = triplets.sparql.query(polars.from_pandas(svedala), q)
    assert isinstance(polars_result, polars.DataFrame)
    assert int(pandas_result["n"].iloc[0]) == int(polars_result["n"][0])


def test_duckdb_input(svedala):
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.register("src", svedala)
    con.execute("CREATE TABLE triplets AS SELECT * FROM src")
    q = PREFIXES + "SELECT (COUNT(?s) AS ?n) WHERE { ?s rdf:type cim:ACLineSegment }"
    assert int(triplets.sparql.query(con, q)["n"].iloc[0]) == len(svedala.triplets.type_tableview("ACLineSegment"))
    assert int(con.sparql.query(q)["n"].iloc[0]) > 0  # namespace accessor on the connection


def test_data_unchanged_skips_rehash(svedala):
    """data_unchanged=True reuses the digest stored for this exact object —
    content_hash must not run; without the flag it always runs."""
    q = PREFIXES + "ASK { ?s rdf:type cim:Substation }"
    data = svedala.copy()
    triplets.sparql.query(data, q)                       # hashes and remembers

    original = type(data).content_hash
    type(data).content_hash = lambda *a, **k: (_ for _ in ()).throw(AssertionError("hash ran"))
    try:
        assert triplets.sparql.query(data, q, data_unchanged=True) is True
        with pytest.raises(AssertionError, match="hash ran"):
            triplets.sparql.query(data, q)               # no flag → rehash
    finally:
        type(data).content_hash = original


def test_data_unchanged_computes_when_unknown(svedala):
    """The flag only skips work when this object was hashed before; an
    unknown object is hashed (and remembered) as usual."""
    q = PREFIXES + "ASK { ?s rdf:type cim:Substation }"
    fresh = svedala.copy()
    assert triplets.sparql.query(fresh, q, data_unchanged=True) is True


def test_hash_memo_evicted_on_gc(svedala):
    """The digest memo holds the object only weakly: after collection the
    entry is gone, so a new object reusing the same id() can never inherit
    a dead frame's digest."""
    import gc
    from triplets import _content_key

    data = svedala.copy()
    triplets.sparql.query(data, PREFIXES + "ASK { ?s rdf:type cim:Substation }")
    object_id = id(data)
    assert object_id in _content_key._HASHES
    del data
    gc.collect()
    assert object_id not in _content_key._HASHES
