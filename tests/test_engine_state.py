"""Engine-state lifecycle (triplets.clear_caches / cache_scope) and the shared
engine registry (triplets._registry.EngineRegistry)."""
import pandas
import pytest

import triplets
from triplets._registry import EngineRegistry

rdflib = pytest.importorskip("rdflib")

DATA = pandas.DataFrame([("b1", "Type", "Breaker", "i1")],
                        columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def _load():
    from triplets._rdflib_loader import load_dataset
    return load_dataset(DATA)


def test_clear_caches_drops_engine_state():
    first = _load()
    assert _load() is first            # cached by content key
    triplets.clear_caches()
    assert _load() is not first        # state rebuilt after clear


def test_cache_scope_keeps_prior_state_drops_inner():
    triplets.clear_caches()
    outer = _load()
    inner_data = pandas.DataFrame([("x1", "Type", "Line", "i2")],
                                  columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    from triplets._rdflib_loader import load_dataset, _DATASETS
    with triplets.cache_scope():
        load_dataset(inner_data)
        assert len(_DATASETS) == 2
    assert len(_DATASETS) == 1         # inner dropped ...
    assert _load() is outer            # ... pre-existing survives


# ── EngineRegistry unit behavior ──────────────────────────────────────────────

def _registry(**overrides):
    options = dict(kind="demo", package="triplets.sparql",
                   modules={"ghost": ".does_not_exist", "rdflib": ".sparql_rdflib"},
                   aliases={"reference": "rdflib"},
                   hints={"ghost": "Build the ghost extension."},
                   default_hint="Install with: pip install triplets[demo].")
    options.update(overrides)
    return EngineRegistry(**options)


def test_registry_unknown_engine():
    with pytest.raises(ValueError, match="Unknown demo engine: nope"):
        _registry().load("nope")


def test_registry_unavailable_engine_carries_hint():
    with pytest.raises(ImportError, match="Build the ghost extension"):
        _registry().load("ghost")


def test_registry_alias_and_auto():
    registry = _registry()
    name, module = registry.get("reference")
    assert name == "rdflib" and module.__name__.endswith("sparql_rdflib")
    name, _ = registry.get("auto")     # ghost fails → falls through to rdflib
    assert name == "rdflib"


def test_registry_auto_exhausted():
    registry = _registry(modules={"ghost": ".does_not_exist"})
    with pytest.raises(ImportError, match=r"no demo engine available .* triplets\[demo\]"):
        registry.get("auto")


def test_registry_custom_engine():
    registry = _registry()
    sentinel = object()
    registry.register("mine", sentinel)
    assert registry.get("mine") == ("mine", sentinel)


def test_public_registries_resolve():
    assert triplets.parser.get_engine("native")[0] == "python_lxml_pandas"
    assert triplets.sparql.get_engine("reference")[0] == "rdflib"
    assert triplets.validation.get_engine("reference")[0] == "pyshacl"
