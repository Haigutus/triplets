"""SHACL validation over triplet data.

Engines (registry dispatch, mirroring triplets.parser / triplets.sparql):
- pyshacl — reference, spec-complete, rdflib-based; always available with the
  `validation` extra
- polars — compiled-IR executor (performance; one lazy plan per constraint,
  single collect_all; auto-preferred when polars is installed)
- pandas — compiled-IR executor (debugging; same complete registry and
  semantics, eager. sh:sparql delegates to triplets.sparql with optional
  max_workers; sh:node runs the compile-time-expanded referenced shape against
  the value nodes; sh:nodeKind is decided by the rdf_map schema)
- duckdb — compiled-IR executor for larger-than-memory data (one SQL query
  per constraint against the triplets table, streams/spills via DuckDB;
  explicit engine="duckdb", not in auto — polars owns the in-memory fast path)

Compile once: ``compile(shapes)`` parses the shapes with rdflib exactly once
into ``CompiledShapes`` (shapes graph + flat constraint table, cached by
content hash). Engines receive the compiled object:

    validate(data, compiled, rdf_map=None, scope=None, **kwargs) → violations DataFrame

pyshacl consumes the graph; the vectorized engines consume the IR (and cache
their own plan per engine in ``CompiledShapes.plans``) — they never touch
rdflib. sh:sparql IR rows are delegated to triplets.sparql by the vectorized
engines (pyshacl evaluates them itself via advanced=True).

One deliberate deviation from pyshacl: the datatype check inspects the raw
lexical form of VALUE (see shacl_pandas) — with ``lexical=True`` (default)
those findings are appended to any engine's report.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas

from .._registry import EngineRegistry
from .shacl_ir import CompiledShapes, compile_shapes as compile  # noqa: A001 — public API name
from .shacl_report import VIOLATION_COLUMNS, export_to_shacl_report  # noqa: F401 — public API
from .context import ENRICHMENT_COLUMNS, enrich  # noqa: F401 — public API
from .locations import LOCATION_COLUMNS, locate_violations  # noqa: F401 — public API
from .sarif import export_to_sarif  # noqa: F401 — public API

logger = logging.getLogger(__name__)

# Auto preference: first importable — polars (lazy, fast) → pandas (same
# semantics, eager) → pyshacl (spec reference). duckdb is deliberately NOT in
# auto: it is the explicit choice for larger-than-memory data (polars owns the
# in-memory fast path). The vectorized engines share the IR and the deliberate
# deviations (lexical datatype, schema-driven nodeKind); engine="reference"
# always gives the pure pyshacl view.
_REGISTRY = EngineRegistry(
    "validation", __package__,
    modules={
        "polars": ".shacl_polars",
        "pandas": ".shacl_pandas",
        "duckdb": ".shacl_duckdb",
        "pyshacl": ".shacl_pyshacl",
    },
    aliases={
        "reference": "pyshacl",
    },
    default_hint="Install with: pip install triplets[validation].",
    auto=["polars", "pandas", "pyshacl"],
    requires={"polars": ("polars",), "duckdb": ("duckdb",),
              "pyshacl": ("pyshacl", "rdflib")},
)
# engines whose datatype check already emits the lexical findings itself
_LEXICAL_BUILTIN = {"polars", "pandas", "duckdb"}


def register_engine(name: str, module: Any) -> None:
    """Register a custom validation engine for future extensibility."""
    _REGISTRY.register(name, module)


def get_engine(name: str = "auto"):
    """Resolve validation engine name (with aliases) and return (name, module)."""
    return _REGISTRY.get(name)


def validate(data, shapes, rdf_map=None, scope=None, engine="auto", lexical=True,
             context=False, **kwargs):
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
        "polars" (performance), "pandas" (debugging), "duckdb"
        (larger-than-memory) or "pyshacl" (reference). "auto" picks
        polars → pandas → pyshacl; duckdb is always an explicit choice.
    lexical : bool, default True
        Append the lexical-form datatype findings (the deliberate deviation
        from pyshacl — see shacl_pandas) to the engine's report.
    context : bool, default False
        Run the slower enrichment pass (triplets.validation.context.enrich):
        adds instance/file, object type/name, shape name/description and
        schema definition columns to the report.
    """
    compiled = shapes if isinstance(shapes, CompiledShapes) else compile(shapes)
    engine_name, engine_mod = get_engine(engine)
    violations = engine_mod.validate(data, compiled, rdf_map=rdf_map, scope=scope, **kwargs)

    if lexical and engine_name not in _LEXICAL_BUILTIN:
        from . import shacl_pandas
        supplement = shacl_pandas.validate(data, compiled, rdf_map=rdf_map, scope=scope,
                                           components=("sh:datatype",))
        violations = (pandas.concat([violations, supplement], ignore_index=True)
                      .drop_duplicates(subset=["ID", "KEY", "VALUE", "VIOLATION_TYPE",
                                               "SOURCE_SHAPE", "SEVERITY"], ignore_index=True))
    if context:
        violations = enrich(violations, data=data, shapes=compiled, rdf_map=rdf_map)
    return violations
