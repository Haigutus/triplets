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
from datetime import datetime, timezone
from typing import Any

import pandas

from .._engine_detect import flavor
from .._registry import EngineRegistry
from .shacl_ir import CompiledShapes, compile_shapes as compile  # noqa: A001 — public API name
from .shacl_report import (VIOLATION_COLUMNS, export_to_shacl_report,  # noqa: F401 — public API
                           violations_to_csv, violations_to_excel)
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

    The returned frame carries the validation-run metadata in
    ``violations.attrs["validation"]`` (start/end timestamps and duration,
    engine, tool version, data/shape file names, shape and constraint counts,
    and any shapes/components the run skipped) — every report exporter reads
    it, so SARIF, the sh:ValidationReport and the csv/excel exports tell the
    same story.
    """
    compiled = shapes if isinstance(shapes, CompiledShapes) else compile(shapes)
    started = datetime.now(timezone.utc)   # after compile — duration is the run, cache-independent
    engine_name, engine_mod = get_engine(engine)
    violations = engine_mod.validate(data, compiled, rdf_map=rdf_map, scope=scope, **kwargs)

    if lexical and engine_name not in _LEXICAL_BUILTIN:
        from . import shacl_pandas
        supplement = shacl_pandas.validate(data, compiled, rdf_map=rdf_map, scope=scope,
                                           components=("sh:datatype",))
        violations = (pandas.concat([violations, supplement], ignore_index=True)
                      .drop_duplicates(subset=["ID", "KEY", "VALUE", "VIOLATION_TYPE",
                                               "SOURCE_SHAPE", "SEVERITY"], ignore_index=True))
    violations = _describe_associations(violations, data, compiled,
                                        table_name=kwargs.get("table_name", "triplets"))
    violations["MESSAGE_SOURCE"] = _message_sources(violations, compiled)
    if context:
        violations = enrich(violations, data=data, shapes=compiled, rdf_map=rdf_map)
    violations.attrs["validation"] = _report_metadata(
        data, compiled, engine_name, started, table_name=kwargs.get("table_name", "triplets"))
    return violations


def _describe_associations(violations, data, compiled, table_name="triplets"):
    """Association type-check findings get a DETAIL entry stating what the
    reference points at — the raw MESSAGE stays verbatim (exporters emit
    DETAIL as its own [detail]-tagged message).

    ``sh:class`` rows carry the referenced id in VALUE — DETAIL names the
    target's actual Type, or the fact that no such object exists in the
    data. valueType rows (``via_type`` paths) carry the found type in VALUE.
    One shared pass over the violations frame; no per-engine code.
    """
    violations["DETAIL"] = pandas.Series(None, index=violations.index, dtype=object)
    if violations.empty or compiled.ir.empty:
        return violations
    via_rules = set(zip(compiled.ir.loc[compiled.ir["via_type"], "shape_id"],
                        compiled.ir.loc[compiled.ir["via_type"], "path"]))
    keys = pandas.Series(list(zip(violations["SOURCE_SHAPE"], violations["KEY"])),
                         index=violations.index)
    described = violations["VALUE"].notna()
    via = described & keys.isin(via_rules)
    of_class = described & ~via & violations["VIOLATION_TYPE"].eq("sh:class")
    if via.any():
        violations.loc[via, "DETAIL"] = ("association target found, of type "
                                         + violations.loc[via, "VALUE"].astype(str))
    if of_class.any():
        found = violations.loc[of_class, "VALUE"].astype(str).map(_type_map(data, table_name))
        violations.loc[of_class, "DETAIL"] = (
            "referenced object found, of type " + found).where(
            found.notna(), "referenced object not found in the data")
    return violations


def _message_sources(violations, compiled):
    """Per row: "shacl" when the text is the shape's own sh:message (engines
    use it verbatim; post-pass suffixes append after it), else "engine"."""
    authored = tuple(sorted({rule.message for rule in compiled.ir.itertuples()
                             if isinstance(rule.message, str) and rule.message},
                            key=len, reverse=True))
    return ["shacl" if isinstance(message, str) and message.startswith(authored) else "engine"
            for message in violations["MESSAGE"]]


def _type_map(data, table_name="triplets"):
    """{ID: Type} from the data's Type rows (any input flavor)."""
    kind = flavor(data)
    if kind == "duckdb":
        return dict(data.execute(
            f"SELECT ID, VALUE FROM {table_name} WHERE KEY = 'Type'").fetchall())
    if kind == "pyarrow":
        data = data.to_pandas(types_mapper=pandas.ArrowDtype)
        kind = "pandas"
    if kind == "polars":
        rows = data.filter(data["KEY"] == "Type")
        return dict(zip(rows["ID"].to_list(), rows["VALUE"].to_list()))
    rows = data.loc[data["KEY"] == "Type"]
    return dict(zip(rows["ID"].astype(str), rows["VALUE"]))


def _report_metadata(data, compiled, engine_name, started, table_name="triplets"):
    """The validation-run facts every report exporter reads (violations.attrs).

    Coverage (skipped_shapes / skipped_components) is what THIS run did not
    evaluate: compile-level skips plus the engine's own gaps for vectorized
    engines; empty for the spec-complete pyshacl reference engine.
    """
    import triplets

    finished = datetime.now(timezone.utc)
    if engine_name == "pyshacl":
        skipped_shapes, skipped_components = [], []
    else:
        skipped_shapes = compiled.stats.get("skipped_shapes", [])
        skipped_components = sorted({*compiled.stats.get("unknown_components", ()),
                                     *compiled.plans.get(engine_name, ((), (), ()))[2]})
    return {
        "started_at": _iso(started),
        "generated_at": _iso(finished),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "engine": engine_name,
        "creator": f"triplets {triplets.__version__}",
        "source": _source_labels(data, table_name=table_name),
        "references": list(compiled.sources),
        "node_shapes": compiled.stats.get("node_shapes", 0),
        "constraints": compiled.stats.get("constraints", 0),
        "skipped_shapes": skipped_shapes,
        "skipped_components": skipped_components,
    }


def _iso(moment):
    """UTC datetime → Zulu ISO string — one lexical form across RDF and SARIF."""
    return moment.isoformat().replace("+00:00", "Z")


def _source_labels(data, table_name="triplets"):
    """File names from the data's Distribution meta rows (``KEY="label"``, the
    parser convention — the same lookup context.enrich uses), any input flavor."""
    kind = flavor(data)
    if kind == "duckdb":
        rows = data.execute(f"SELECT VALUE FROM {table_name} WHERE KEY = 'label'").fetchall()
        labels = (value for (value,) in rows)
    elif kind == "polars":
        labels = data.filter(data["KEY"] == "label")["VALUE"]
    else:   # pandas — and pyarrow through its arrow-backed pandas view
        if kind == "pyarrow":
            data = data.to_pandas(types_mapper=pandas.ArrowDtype)
        labels = data.loc[data["KEY"] == "label", "VALUE"]
    return [label for label in dict.fromkeys(labels) if label]
