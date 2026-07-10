"""SPARQL querying over triplet data.

Engines (registry dispatch, mirroring triplets.parser):
- qlever  — performance (embedded C++ via the official libqlever facade; needs
  the compiled extension, see setup_qlever.py; takes auto priority when built)
- rdflib  — reference, built-in SPARQL 1.1, always available with the `sparql` extra

Data is loaded via the N-Quads export into an rdflib Dataset (INSTANCE_ID as
named graph). No oxigraph engine: our native tooling is C/C++/Cython and qlever
is the chosen performance path.
"""
from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

logger = logging.getLogger(__name__)

# Engine name → module (lazy import). Auto preference: first importable —
# qlever (embedded C++, needs the compiled extension: setup_qlever.py) wins
# over rdflib (reference, always available with the sparql extra).
_ENGINE_MODULES = {
    "qlever": ".sparql_qlever",
    "rdflib": ".sparql_rdflib",
}
_ENGINE_ALIASES = {
    "reference": "rdflib",
    "performance": "qlever",
}
_ENGINES: dict[str, Any] = {}  # loaded-module cache


def register_engine(name: str, module: Any) -> None:
    """Register a custom SPARQL engine for future extensibility."""
    _ENGINES[name] = module


def content_key(data, rdf_map, salt):
    """Identity of exactly what an engine loads — shared by every engine's
    state cache (qlever on-disk indexes, rdflib in-memory datasets):
    content_hash over all four columns, nothing ignored (the key must match
    the loaded content, or queries against ignored triples would answer from
    another dataset's state), mixed with the export schema and an
    engine/format-version salt."""
    import os
    import json
    import hashlib

    content = data.content_hash(ignore_types=(), columns=("ID", "KEY", "VALUE", "INSTANCE_ID"))
    if isinstance(rdf_map, (str, os.PathLike)):
        with open(rdf_map, "rb") as file:
            schema = file.read()
    else:
        schema = json.dumps(rdf_map, sort_keys=True, default=str).encode() if rdf_map else b""
    return hashlib.sha256(salt + content.encode() + schema).hexdigest()[:24]


def _load_engine(name: str):
    if name in _ENGINES:
        return _ENGINES[name]
    module_name = _ENGINE_MODULES.get(name)
    if module_name is None:
        raise ValueError(f"Unknown sparql engine: {name}. Known: {', '.join(_ENGINE_MODULES)}")
    try:
        _ENGINES[name] = import_module(module_name, __package__)
    except ImportError as e:
        raise ImportError(f"{name} sparql engine not available. "
                          "Install with: pip install triplets[sparql]. "
                          f"Original error: {e}") from e
    return _ENGINES[name]


def get_engine(name: str = "auto"):
    """Resolve SPARQL engine name (with aliases) and return (name, module)."""
    if name == "auto":
        for candidate in _ENGINE_MODULES:
            try:
                return candidate, _load_engine(candidate)
            except ImportError:
                continue
    resolved = _ENGINE_ALIASES.get(name, name)
    logger.debug(f"sparql engine: {resolved}")
    return resolved, _load_engine(resolved)


def query(data, query_string, rdf_map=None, scope=None, engine="auto", return_type="auto"):
    """Run a SPARQL query over triplet data.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars) or DuckDB connection
    query_string : str
        SPARQL query. SELECT → DataFrame (columns = projected vars),
        ASK → bool, CONSTRUCT/DESCRIBE → triplet DataFrame.
    rdf_map : dict or str, optional
        Export schema — enables xsd-typed literals in the queried graph (optional).
    scope : iterable of INSTANCE_ID, optional
        Restrict the queried data to these instances' named graphs; all data
        stays loaded for reference resolution. None = full union.
    engine : str, default "auto"
        "qlever" (performance, embedded C++) or "rdflib" (reference).
        "auto" picks qlever when its extension is built, else rdflib.
    return_type : str, default "auto"
        Output flavor for data results: "auto" matches the input (polars in →
        polars out; pandas/duckdb → pandas), or explicit "pandas" / "polars" /
        "arrow". Honored by the qlever engine (arrow-native results); the
        rdflib reference engine always returns pandas.
    """
    engine_name, engine_mod = get_engine(engine)
    return engine_mod.query(data, query_string, rdf_map=rdf_map, scope=scope, return_type=return_type)
