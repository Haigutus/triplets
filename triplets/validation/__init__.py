"""SHACL validation over triplet data.

Engines (registry dispatch, mirroring triplets.parser):
- pyshacl — reference, spec-complete, rdflib-based; always available with the
  `validation` extra
- (future) pandas / polars / duckdb — experimental compiled-IR executors

Data is loaded via the N-Quads export into an rdflib graph. pyshacl is the
correctness baseline the future engines are cross-checked against.
"""
from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

logger = logging.getLogger(__name__)

# Engine name → module (lazy import). Auto preference: first importable.
_ENGINE_MODULES = {
    "pyshacl": ".shacl_pyshacl",
}
_ENGINE_ALIASES = {
    "reference": "pyshacl",
}
_ENGINES: dict[str, Any] = {}  # loaded-module cache


def register_engine(name: str, module: Any) -> None:
    """Register a custom validation engine for future extensibility."""
    _ENGINES[name] = module


def _load_engine(name: str):
    if name in _ENGINES:
        return _ENGINES[name]
    module_name = _ENGINE_MODULES.get(name)
    if module_name is None:
        raise ValueError(f"Unknown validation engine: {name}. Known: {', '.join(_ENGINE_MODULES)}")
    try:
        _ENGINES[name] = import_module(module_name, __package__)
    except ImportError as e:
        raise ImportError(f"{name} validation engine not available. "
                          "Install with: pip install triplets[validation]. "
                          f"Original error: {e}") from e
    return _ENGINES[name]


def get_engine(name: str = "auto"):
    """Resolve validation engine name (with aliases) and return (name, module)."""
    if name == "auto":
        for candidate in _ENGINE_MODULES:
            try:
                return candidate, _load_engine(candidate)
            except ImportError:
                continue
    resolved = _ENGINE_ALIASES.get(name, name)
    logger.debug(f"validation engine: {resolved}")
    return resolved, _load_engine(resolved)


def validate(data, shapes, rdf_map=None, scope=None, engine="auto", **kwargs):
    """Validate triplet data against SHACL shapes; return a violations DataFrame.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    shapes : str | path | list of paths | rdflib.Graph
        SHACL shapes (format auto-detected by extension).
    rdf_map : dict or str, optional
        Export schema — xsd-typed literals in the data graph (optional).
    scope : iterable of INSTANCE_ID, optional
        Validate only these instances' named graphs; all data stays loaded for
        reference resolution. None = full union.
    engine : str, default "auto"
        "pyshacl" (reference). "auto" picks the best available.
    """
    engine_name, engine_mod = get_engine(engine)
    return engine_mod.validate(data, shapes, rdf_map=rdf_map, scope=scope, **kwargs)
