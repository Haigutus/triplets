"""SHACL validation over triplet data.

Engines (registry dispatch, mirroring triplets.parser / triplets.sparql):
- pyshacl — reference, spec-complete, rdflib-based; always available with the
  `validation` extra
- pandas — compiled-IR executor (debugging; full constraint registry.
  sh:sparql delegates to triplets.sparql with optional max_workers; sh:node is
  not implemented — 0 uses across the CGMES SHACL library; sh:nodeKind is
  inferred from the string form. Explicit engine="pandas")
- (future) polars / duckdb — compiled-IR executors for speed / larger-than-memory

Compile once: ``compile(shapes)`` parses the shapes with rdflib exactly once
into ``CompiledShapes`` (shapes graph + flat constraint table, cached by
content hash). Engines receive the compiled object:

    validate(data, compiled, rdf_map=None, scope=None, **kwargs) → violations DataFrame

pyshacl consumes the graph; the vectorized engines consume the IR (and cache
their own plan per engine in ``CompiledShapes.plans``) — they never touch
rdflib. sh:sparql IR rows are delegated to triplets.sparql by future engines
(pyshacl evaluates them itself via advanced=True).

One deliberate deviation from pyshacl: the datatype check inspects the raw
lexical form of VALUE (see shacl_pandas) — with ``lexical=True`` (default)
those findings are appended to any engine's report.
"""
from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

import pandas

from .shacl_ir import CompiledShapes, compile_shapes as compile  # noqa: A001 — public API name
from .shacl_report import VIOLATION_COLUMNS

logger = logging.getLogger(__name__)

# Engine name → module (lazy import).
_ENGINE_MODULES = {
    "pyshacl": ".shacl_pyshacl",
    "pandas": ".shacl_pandas",
}
_ENGINE_ALIASES = {
    "reference": "pyshacl",
}
# Auto preference: reference-first — auto is pyshacl until the polars engine
# lands, then the vectorized engines take priority (polars → pandas → pyshacl).
# The pandas engine is complete for the ENTSO-E constraint subset but stays
# explicit (its nodeKind inference and lexical datatype semantics deliberately
# differ from the spec reference).
_AUTO_ORDER = ["pyshacl"]
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
        for candidate in _AUTO_ORDER:
            try:
                return candidate, _load_engine(candidate)
            except ImportError:
                continue
    resolved = _ENGINE_ALIASES.get(name, name)
    logger.debug(f"validation engine: {resolved}")
    return resolved, _load_engine(resolved)


def validate(data, shapes, rdf_map=None, scope=None, engine="auto", lexical=True, **kwargs):
    """Validate triplet data against SHACL shapes; return a violations DataFrame.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    shapes : str | path | list of paths | rdflib.Graph | CompiledShapes
        SHACL shapes (format auto-detected by extension). Pass the result of
        ``compile(shapes)`` to reuse the parsed shapes across validations.
    rdf_map : dict or str, optional
        Export schema — xsd-typed literals in the data graph (optional).
    scope : iterable of INSTANCE_ID, optional
        Validate only these instances' named graphs; all data stays loaded for
        reference resolution. None = full union.
    engine : str, default "auto"
        "pyshacl" (reference) or "pandas" (partial). "auto" picks the best available.
    lexical : bool, default True
        Append the lexical-form datatype findings (the deliberate deviation
        from pyshacl — see shacl_pandas) to the engine's report.
    """
    compiled = shapes if isinstance(shapes, CompiledShapes) else compile(shapes)
    engine_name, engine_mod = get_engine(engine)
    violations = engine_mod.validate(data, compiled, rdf_map=rdf_map, scope=scope, **kwargs)

    if lexical and engine_name != "pandas":
        from . import shacl_pandas
        supplement = shacl_pandas.validate(data, compiled, scope=scope, components=("sh:datatype",))
        violations = (pandas.concat([violations, supplement], ignore_index=True)
                      .drop_duplicates(subset=["ID", "KEY", "VALUE", "VIOLATION_TYPE"], ignore_index=True))
    return violations
