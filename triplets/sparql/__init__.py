"""SPARQL querying over triplet data.

Engines (registry dispatch, mirroring triplets.parser):
- qlever   — performance (embedded C++ via the official libqlever facade; needs
  the compiled extension, see setup_qlever.py; takes auto priority when built)
- oxigraph — portable performance (embedded Rust via the pyoxigraph wheel,
  `oxigraph` extra; auto priority when qlever is not built)
- rdflib   — reference, built-in SPARQL 1.1, always available with the `sparql` extra

Data reaches every engine through the N-Quads export conventions
(INSTANCE_ID as named graph): rdflib and oxigraph load the export directly,
qlever ingests the same term mapping as Arrow batches.
"""
from __future__ import annotations

import logging
from typing import Any

from .._registry import EngineRegistry

logger = logging.getLogger(__name__)

# Auto preference: first importable — qlever (embedded C++, needs the compiled
# extension: setup_qlever.py) wins over oxigraph (embedded Rust, needs the
# pyoxigraph wheel), which wins over rdflib (reference, always available with
# the sparql extra).
_REGISTRY = EngineRegistry(
    "sparql", __package__,
    modules={
        "qlever": ".sparql_qlever",
        "oxigraph": ".sparql_oxigraph",
        "rdflib": ".sparql_rdflib",
    },
    aliases={
        "reference": "rdflib",
        "performance": "qlever",
    },
    hints={"oxigraph": "Install with: pip install triplets[oxigraph]."},
    default_hint="Install with: pip install triplets[sparql].",
)


def register_engine(name: str, module: Any) -> None:
    """Register a custom SPARQL engine for future extensibility."""
    _REGISTRY.register(name, module)


def get_engine(name: str = "auto"):
    """Resolve SPARQL engine name (with aliases) and return (name, module)."""
    return _REGISTRY.get(name)


def query(data, query_string, rdf_map=None, scope=None, engine="auto", return_type="auto",
          data_unchanged=False):
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
        "qlever" (performance, embedded C++), "oxigraph" (portable
        performance, embedded Rust) or "rdflib" (reference). "auto" picks
        the first available in that order.
    return_type : str, default "auto"
        Output flavor for data results: "auto" matches the input (polars in →
        polars out; pandas/duckdb → pandas), or explicit "pandas" / "polars" /
        "arrow". All SELECT values are lexical strings in every engine
        (triplets are all-string; consumers cast).
    data_unchanged : bool, default False
        Assert that `data` has not been mutated since it was last hashed:
        the engine reuses the stored content digest for this exact object
        and skips the content_hash (the dominant cost of small warm
        queries). Only skips work when this object was hashed before;
        otherwise the hash runs and is remembered.
    """
    engine_name, engine_mod = get_engine(engine)
    return engine_mod.query(data, query_string, rdf_map=rdf_map, scope=scope,
                            return_type=return_type, data_unchanged=data_unchanged)
