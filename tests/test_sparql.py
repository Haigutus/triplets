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
    in_scope = int(svedala.sparql.query(q, data_scope=[eq_instance])["n"].iloc[0])
    out_scope = int(svedala.sparql.query(q, data_scope=[other])["n"].iloc[0])
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
    triplets.sparql.query(shuffled, q, data_scope=[instance])
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


# ── content_key: the schema serialization rides on the data digest ──────────

SALT = b"test-salt"
SCHEMA = {"EQ": {"ACLineSegment.length": {"type": "Attribute", "datatype": "float"}}}


def _frame():
    return pandas.DataFrame({"ID": ["a", "a"], "KEY": ["Type", "IdentifiedObject.name"],
                             "VALUE": ["ACLineSegment", "line"], "INSTANCE_ID": ["i", "i"]})


def _expected_key(data, schema_bytes):
    import hashlib
    content = data.content_hash(ignore_types=(), columns=("ID", "KEY", "VALUE", "INSTANCE_ID"))
    return hashlib.sha256(SALT + content.encode() + schema_bytes).hexdigest()[:24]


@pytest.fixture
def dumps_calls(monkeypatch):
    """Counts the schema serializations content_key runs."""
    import json
    from types import SimpleNamespace
    from triplets import _content_key
    calls = []
    monkeypatch.setattr(_content_key, "json", SimpleNamespace(
        dumps=lambda *args, **kwargs: calls.append(args[0]) or json.dumps(*args, **kwargs)))
    return calls


def test_content_key_formula(tmp_path):
    """The key is sha256(salt + data digest + schema bytes): JSON of a dict
    (sorted keys), the raw bytes of a path, nothing for no schema — with and
    without data_unchanged, first call and reuse alike."""
    import json
    from triplets._content_key import content_key
    path = tmp_path / "rdf_map.json"
    path.write_text(json.dumps(SCHEMA, indent=1))
    cases = [(SCHEMA, json.dumps(SCHEMA, sort_keys=True, default=str).encode()), (str(path), path.read_bytes()),
             (path, path.read_bytes()), (None, b""), ({}, b"")]
    for rdf_map, schema_bytes in cases:
        data = _frame()
        for data_unchanged in (False, True, True, False):
            assert content_key(data, rdf_map, SALT, data_unchanged) == _expected_key(data, schema_bytes), rdf_map


def test_content_key_reuses_schema_of_same_rdf_map_object(dumps_calls):
    """data_unchanged=True reuses the stored serialization only for the very
    rdf_map object it was computed from; another object is serialized again
    (equal content → equal key, other content → other key); without the flag
    it always runs."""
    import copy
    from triplets._content_key import content_key
    data = _frame()
    first = content_key(data, SCHEMA, SALT)
    assert len(dumps_calls) == 1
    assert content_key(data, SCHEMA, SALT, data_unchanged=True) == first
    assert len(dumps_calls) == 1
    assert content_key(data, copy.deepcopy(SCHEMA), SALT, data_unchanged=True) == first
    assert len(dumps_calls) == 2
    other = {"EQ": {"ACLineSegment.length": {"type": "Attribute", "datatype": "string"}}}
    assert content_key(data, other, SALT, data_unchanged=True) != first
    assert len(dumps_calls) == 3
    assert content_key(data, other, SALT) == content_key(data, other, SALT)
    assert len(dumps_calls) == 5


def test_content_key_schema_mutated_in_place_is_the_callers_assertion(dumps_calls):
    """data_unchanged=True asserts the rdf_map dict is unchanged too: mutated
    in place, the same object keeps its stored serialization (stale key);
    without the flag the mutation shows."""
    import copy
    from triplets._content_key import content_key
    data, schema = _frame(), copy.deepcopy(SCHEMA)
    before = content_key(data, schema, SALT)
    schema["EQ"]["ACLineSegment.length"]["datatype"] = "string"
    assert content_key(data, schema, SALT, data_unchanged=True) == before
    assert content_key(data, schema, SALT) != before


def test_content_key_entry_releases_data_and_rdf_map():
    """The entry holds the rdf_map strongly and the data weakly: no cycle keeps
    the data alive (freed by refcount alone), and its eviction drops the
    rdf_map reference."""
    import copy
    import gc
    import sys
    import weakref
    from triplets import _content_key
    data, schema = _frame(), copy.deepcopy(SCHEMA)
    references = sys.getrefcount(schema)
    _content_key.content_key(data, schema, SALT)
    assert sys.getrefcount(schema) == references + 1
    object_id, alive = id(data), weakref.ref(data)
    gc.disable()
    try:
        del data
        assert alive() is None
    finally:
        gc.enable()
    assert object_id not in _content_key._HASHES
    assert sys.getrefcount(schema) == references
