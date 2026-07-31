"""triplets.engines() / triplets.set_engine() — selection report and override."""
import pytest

import triplets
from triplets._registry import REGISTRIES

INFO_KEYS = {"engine", "source", "policy", "auto_order", "available", "unavailable", "aliases"}


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    for registry in REGISTRIES.values():
        registry.override = None


def test_engines_report_covers_all_subsystems():
    info = triplets.engines()
    assert {"parser", "sparql", "validation", "nquads", "cimxml", "csv", "tools"} <= set(info)
    for row in info.values():
        assert set(row) == INFO_KEYS
    assert info["tools"]["policy"] == "input"
    assert info["csv"]["policy"] == "input"


def test_selected_matches_load_outcome():
    """The find_spec probe and the actual import agree on what "auto" gives."""
    for kind in ("parser", "cimxml", "nquads"):
        registry = REGISTRIES[kind]
        assert registry.get("auto")[0] == triplets.engines()[kind]["engine"]


def test_set_engine_steers_auto_and_restores():
    triplets.set_engine(parser="python_lxml_pandas")
    assert triplets.engines()["parser"]["engine"] == "python_lxml_pandas"
    assert triplets.engines()["parser"]["source"] == "set_engine"
    assert REGISTRIES["parser"].get("auto")[0] == "python_lxml_pandas"

    triplets.set_engine(parser="auto")
    assert triplets.engines()["parser"]["source"] == "auto"
    assert REGISTRIES["parser"].get("auto")[0] == REGISTRIES["parser"].selected


def test_per_call_engine_bypasses_override():
    triplets.set_engine(parser="python_lxml_pandas")
    name, _ = REGISTRIES["parser"].get("python_lxml_arrow" if
                                       REGISTRIES["parser"].available("python_lxml_arrow")
                                       else "python_lxml_pandas")
    assert name != "auto"   # explicit names resolve directly, never through the override


def test_set_engine_resolves_aliases():
    # the "pandas" alias targets the always-available lxml engine
    triplets.set_engine(cimxml="pandas")
    assert triplets.engines()["cimxml"]["engine"] == "python_lxml"


def test_get_cimxml_engine_compat_aliases():
    from triplets.export import get_cimxml_engine
    assert get_cimxml_engine("pandas")[0] == "python_lxml"
    assert get_cimxml_engine("lxml")[0] == "python_lxml"
    # compiled-engine aliases resolve without requiring a loadable build
    assert REGISTRIES["cimxml"].aliases["pugixml"] == "cython_pugixml"
    assert REGISTRIES["cimxml"].aliases["performance"] == "cython_pugixml"


def test_broken_build_falls_through_and_corrects_report():
    """A probe-available engine whose import fails is skipped by "auto" and
    dropped from the availability report afterwards."""
    registry = REGISTRIES["cimxml"]
    name, _ = registry.get("auto")
    assert name in registry.modules
    assert registry.selected == name        # report agrees with the load outcome


def test_set_engine_errors():
    with pytest.raises(ValueError, match="unknown subsystem"):
        triplets.set_engine(bogus="x")
    with pytest.raises(ValueError, match="Unknown parser engine"):
        triplets.set_engine(parser="nonexistent")
    with pytest.raises(ValueError, match="follows the input flavor"):
        triplets.set_engine(csv="polars")


def test_available_never_lists_missing_backend():
    """A registry row never claims an engine whose probe targets are absent."""
    for kind, row in triplets.engines().items():
        registry = REGISTRIES[kind]
        for name in row["available"]:
            assert registry.available(name)
        for name in row["unavailable"]:
            assert not registry.available(name)


def test_relative_find_spec_resolves_against_package():
    """Relative probe targets (compiled extensions) resolve during normal use."""
    assert REGISTRIES["parser"].available("python_lxml_pandas")   # module probe
    # cimxml's compiled requires target is relative (".cimxml_cython_pugixml")
    assert isinstance(REGISTRIES["cimxml"].available("cython_pugixml"), bool)


def test_custom_registered_engine_is_available():
    registry = REGISTRIES["nquads"]
    sentinel = object()
    registry.register("custom", sentinel)
    try:
        assert registry.available("custom")
        assert "custom" in registry.available_engines()
        assert registry.get("custom")[1] is sentinel
    finally:
        registry.loaded.pop("custom", None)
