"""Tests for the oxigraph-backed rdflib store option (triplets._rdflib_loader).

The pyshacl engine can load its data graph through the oxigraph SPARQL
engine's cached store (oxrdflib wrapper) instead of the rdflib Memory store —
results must be identical either way. Skip when pyoxigraph/oxrdflib are absent.
"""
import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyoxigraph")
pytest.importorskip("oxrdflib")

import pandas
import triplets

from triplets import _rdflib_loader
from triplets._rdflib_loader import load_dataset, _resolve_store

FRAME = pandas.DataFrame(
    [
        ("11111111-2222-3333-4444-555555555555", "Type", "ACLineSegment", "g1"),
        ("11111111-2222-3333-4444-555555555555", "IdentifiedObject.name", "Line 1", "g1"),
        ("11111111-2222-3333-4444-555555555555", "IdentifiedObject.name", "Line 1", "g2"),
        ("22222222-2222-3333-4444-555555555555", "Type", "Substation", "g2"),
    ],
    columns=["ID", "KEY", "VALUE", "INSTANCE_ID"],
)

NAME_QUERY = "SELECT ?name WHERE { ?s <http://iec.ch/TC57/CIM100#IdentifiedObject.name> ?name }"

SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 2 ] ;
    sh:property [ sh:path cim:Conductor.length ; sh:minCount 1 ] .
"""


def test_oxigraph_dataset_matches_memory_semantics():
    """The wrapped dataset exposes the deduplicated union (a triple in two
    named graphs is one solution — exactly the Memory default_union set
    semantics), with the named graphs still reachable."""
    import rdflib
    memory = load_dataset(FRAME, store="memory")
    oxigraph = load_dataset(FRAME.copy(), store="oxigraph")
    assert len(list(oxigraph.query(NAME_QUERY))) == len(list(memory.query(NAME_QUERY))) == 1
    assert (sum(1 for _ in oxigraph.triples((None, None, None)))
            == sum(1 for _ in memory.triples((None, None, None))) == 3)
    g1 = oxigraph.get_context(rdflib.URIRef("urn:uuid:g1"))
    assert sum(1 for _ in g1.triples((None, None, None))) == 2


def test_backends_cached_separately():
    """Memory and oxigraph datasets for the same frame coexist under
    backend-prefixed cache keys."""
    _rdflib_loader._DATASETS.clear()
    frame = FRAME.copy()
    load_dataset(frame, store="memory")
    load_dataset(frame, store="oxigraph", data_unchanged=True)
    prefixes = sorted(key.split(":")[0] for key in _rdflib_loader._DATASETS)
    assert prefixes == ["memory", "oxigraph"]


def test_store_shared_with_sparql_engine():
    """One bulk_load serves both: the wrapped dataset reuses the oxigraph
    SPARQL engine's cached store for the same content."""
    from triplets.sparql import sparql_oxigraph
    frame = FRAME.copy()
    triplets.sparql.query(frame, NAME_QUERY, engine="oxigraph")
    stores = len(sparql_oxigraph._STORES)
    load_dataset(frame, store="oxigraph", data_unchanged=True)
    assert len(sparql_oxigraph._STORES) == stores


def test_resolve_store():
    assert _resolve_store("memory") == "memory"
    assert _resolve_store("oxigraph") == "oxigraph"
    assert _resolve_store("auto") == "oxigraph"      # both libs importable here
    with pytest.raises(ValueError, match="Unknown rdflib store backend"):
        _resolve_store("sqlite")


def test_auto_falls_back_to_memory_without_oxrdflib(monkeypatch):
    import importlib.util
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "oxrdflib" else real_find_spec(name, *a))
    assert _resolve_store("auto") == "memory"


def test_pyshacl_identical_violations_across_stores():
    """engine="pyshacl" with store="oxigraph" produces the same violations
    frame as the default Memory store."""
    pytest.importorskip("pyshacl")
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=SHAPE, format="turtle")

    key = ["ID", "KEY", "VIOLATION_TYPE", "SEVERITY"]
    memory = triplets.validation.validate(FRAME, graph, engine="pyshacl", store="memory")
    oxigraph = triplets.validation.validate(FRAME.copy(), graph, engine="pyshacl", store="oxigraph")
    assert len(memory) > 0    # minCount 2 / missing length must trip
    left = memory[key].sort_values(key).reset_index(drop=True)
    right = oxigraph[key].sort_values(key).reset_index(drop=True)
    assert left.equals(right)


def test_pyshacl_scoped_identical_across_stores():
    """scope goes through scoped_graph (a concrete Memory copy) for both
    backends — identical scoped results."""
    pytest.importorskip("pyshacl")
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=SHAPE, format="turtle")

    key = ["ID", "KEY", "VIOLATION_TYPE"]
    memory = triplets.validation.validate(FRAME, graph, engine="pyshacl",
                                          store="memory", scope=["g1"])
    oxigraph = triplets.validation.validate(FRAME.copy(), graph, engine="pyshacl",
                                            store="oxigraph", scope=["g1"])
    left = memory[key].sort_values(key).reset_index(drop=True)
    right = oxigraph[key].sort_values(key).reset_index(drop=True)
    assert left.equals(right)
