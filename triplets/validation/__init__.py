"""Validation over triplet data — three generic validations, scoped by the caller:

- ``validate_shacl`` — SHACL shapes;
- ``validate_schema`` — the export schema's cardinality, datatypes and
  enumerations, per instance;
- ``validate_schema_references`` — the reference types of associations, per
  source instance, against target instances and profiles.

Every input has a scope named after it: ``data`` + ``data_scope`` (INSTANCE_IDs),
``rdf_map`` + ``rdf_map_scope`` (profiles, merged — ``schema_scope``). Nothing
here reads instance headers; ``triplets.cgmes_tools.validate_by_header`` does.
``enrich`` adds the context columns in a separate call.

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

    validate(data, compiled, rdf_map=None, data_scope=None, ancestors=None, **kwargs) → violations DataFrame

(``rdf_map`` the scoped schema, ``ancestors`` the ``class_ancestors`` map.)

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
from ..iri import CIM_NS, load_rdf_map
from .shacl_ir import CompiledShapes, compile_shapes as compile  # noqa: A001 — public API name
from .schema_ir import compile_references, compile_schema, PRESENTED as _PRESENTED  # noqa: F401 — public API
from .schema_scope import SCOPE_KEY, _class_ancestors, require_entries, scope_sections
from .schema_scope import class_ancestors, scoped_rdf_map  # noqa: F401 — public API
from .shacl_report import (VIOLATION_COLUMNS, export_to_shacl_report,  # noqa: F401 — public API
                           violations_to_csv, violations_to_excel)
from .context import ENRICHMENT_COLUMNS, concat_violations, enrich  # noqa: F401 — public API
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


def validate_shacl(data, shapes, rdf_map=None, data_scope=None, rdf_map_scope=None, engine="auto", lexical=True,
                   undefined_namespace=CIM_NS, inheritance=True, **kwargs):
    """Validate triplet data against SHACL shapes; return a violations DataFrame.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    shapes : str | path | list of paths | rdflib.Graph | CompiledShapes
        SHACL shapes (format auto-detected by extension). Pass the result of
        ``compile(shapes)`` to reuse the parsed shapes across validations.
    rdf_map : dict or str, optional
        Export schema — xsd-typed literals in the data graph, the schema-driven
        sh:nodeKind decision and the class inheritance (see ``inheritance``),
        from the profiles in ``rdf_map_scope``.
    data_scope : iterable of INSTANCE_ID, optional
        Validate only these instances' named graphs — data outside the scope
        is not loaded, so references into unscoped instances count as absent.
        Include dependency instances in the scope (or validate the full
        union, data_scope=None) for cross-instance checks.
    rdf_map_scope : profile identifier or sequence of them, optional
        The rdf_map profiles the run uses, merged into one: section keys
        (``"EQ"``) and/or declared profile IRIs; ``"*"`` = every section;
        None = the rdf_map's root entries. A scope without entries raises
        ValueError (see ``triplets.validation.schema_scope``).
    engine : str, default "auto"
        "polars" (performance), "pandas" (debugging), "duckdb"
        (larger-than-memory) or "pyshacl" (reference). "auto" picks
        polars → pandas → pyshacl; duckdb is always an explicit choice.
    lexical : bool, default True
        Append the lexical-form datatype findings (the deliberate deviation
        from pyshacl — see shacl_pandas) to the engine's report.
    undefined_namespace : str, default CIM100
        Namespace for names the schema does not declare (every name without
        an rdf_map) in the graph the sh:sparql constraints and pyshacl see.
    inheritance : bool, default True
        ``sh:targetClass`` and ``sh:class`` also match instances of subclasses,
        from the scoped profiles' ``inheritance`` lists
        (:func:`~triplets.validation.schema_scope.class_ancestors`; pyshacl: as
        ``rdfs:subClassOf`` in ``ont_graph``). False matches the exact
        ``rdf:type`` only. No effect without an rdf_map.

    The returned frame carries the validation-run metadata in
    ``violations.attrs["validation"]`` (start/end timestamps and duration,
    engine, tool version, data/shape file names, shape and constraint counts,
    and any shapes/components the run skipped) — every report exporter reads
    it, so SARIF, the sh:ValidationReport and the csv/excel exports tell the
    same story.
    """
    compiled = shapes if isinstance(shapes, CompiledShapes) else compile(shapes)
    rdf_map = load_rdf_map(rdf_map)        # dict from here on — the engines and enrich take only dicts
    started = datetime.now(timezone.utc)   # after compile — duration is the run, cache-independent
    if not rdf_map and rdf_map_scope is not None:
        raise ValueError("rdf_map_scope names profiles of an rdf_map — pass the rdf_map too")
    scoped, notes = scoped_rdf_map(rdf_map, rdf_map_scope) if rdf_map else ({}, None)
    if rdf_map:
        require_entries(scoped[SCOPE_KEY], rdf_map_scope)
    ancestors = _class_ancestors(scoped[SCOPE_KEY]) if rdf_map and inheritance else None
    engine_name, engine_mod = get_engine(engine)
    table_ref = _table_ref(data, **kwargs)
    violations = _run(data, compiled, engine_name, engine_mod, scoped, data_scope, lexical,
                      undefined_namespace=undefined_namespace, ancestors=ancestors, **kwargs)
    violations = _present(violations, compiled.ir, compiled.language)
    violations.attrs["validation"] = _report_metadata(
        violations, data, compiled, engine_name, started, table_name=table_ref, notes=notes)
    violations.attrs["validation"]["inheritance"] = bool(ancestors)   # applied: an rdf_map with classes, the flag on
    violations.attrs["validation"]["profiles"] = (scoped[SCOPE_KEY].get("ProfileMetadata", {}).get("sections")
                                                  if rdf_map else None)
    return violations


def _run(data, compiled, engine_name, engine_mod, rdf_map, data_scope, lexical, undefined_namespace=CIM_NS,
         ancestors=None, **kwargs):
    """One engine pass, plus the lexical datatype supplement when the engine
    does not emit it itself — raw violations, presentation columns not yet added."""
    violations = engine_mod.validate(data, compiled, rdf_map=rdf_map, data_scope=data_scope,
                                     undefined_namespace=undefined_namespace, ancestors=ancestors, **kwargs)
    if lexical and engine_name not in _LEXICAL_BUILTIN:
        from . import shacl_pandas
        supplement = shacl_pandas.validate(data, compiled, rdf_map=rdf_map, data_scope=data_scope,
                                           components=("sh:datatype",), undefined_namespace=undefined_namespace,
                                           ancestors=ancestors)
        violations = pandas.concat([violations, supplement], ignore_index=True)
    # identical findings once: a property shape on a parent and a child node shape
    # both reach a subclass instance, the same triple sits in two instance files,
    # the lexical supplement repeats pyshacl
    return violations.drop_duplicates(subset=["ID", "KEY", "VALUE", "VIOLATION_TYPE",
                                              "SOURCE_SHAPE", "SEVERITY"], ignore_index=True)


def _present(violations, ir, language):
    """The shared presentation pass — from the rules alone: EXPECTED /
    MESSAGE_SOURCE and the vocabulary-accurate violation types for non-SHACL
    constraint languages. What was found in the data is ``enrich``'s TARGET."""
    violations["EXPECTED"] = _expected(violations, ir)
    violations["MESSAGE_SOURCE"] = _message_sources(violations, ir)
    if language != "shacl":              # present vocabulary-accurate types, not fake SHACL
        violations["VIOLATION_TYPE"] = (violations["VIOLATION_TYPE"].map(_PRESENTED)
                                        .fillna(violations["VIOLATION_TYPE"]))
    return violations


def _table_ref(data, **kwargs):
    """The SQL relation the helper queries read: duckdb inputs resolve through
    the connection's configured table/schema exactly like the duckdb engine
    (call kwargs → connection config → package default); other flavors never
    read it."""
    if flavor(data) == "duckdb":
        from ..tools.duckdb_engine import _resolve_table
        return _resolve_table(data, table=kwargs.get("table"),
                              schema=kwargs.get("schema"),
                              table_name=kwargs.get("table_name"))
    return kwargs.get("table_name", "triplets")


# what a violated constraint requires, worded from its IR parameter — the
# report states what IS allowed even when the authored message does not
_EXPECTED = {
    "sh:minCount": "at least {} value(s)".format,
    "sh:maxCount": "at most {} value(s)".format,
    "sh:minLength": "length >= {}".format,
    "sh:maxLength": "length <= {}".format,
    "sh:minInclusive": "value >= {}".format,
    "sh:maxInclusive": "value <= {}".format,
    "sh:minExclusive": "value > {}".format,
    "sh:maxExclusive": "value < {}".format,
    "sh:pattern": "value matching {}".format,
    "sh:datatype": "a {} value".format,
    "sh:class": "a reference to a {}".format,
    "sh:nodeKind": "an {} value".format,
    "sh:hasValue": "value {}".format,
    "sh:in": lambda params: "one of: " + ", ".join(map(str, params)),
    "triplets:range": lambda params: "a reference to one of: " + ", ".join(map(str, params)),
    "triplets:missing": lambda params: "a referenced object in the data, one of: " + ", ".join(map(str, params)),
    "sh:equals": "equal to {}".format,
    "sh:disjoint": "different from {}".format,
    "sh:lessThan": "less than {}".format,
    "sh:lessThanOrEquals": "at most {}".format,
}


def _expected(violations, ir):
    """EXPECTED column: the violated constraint's requirement in words."""
    params = {(rule.shape_id, rule.path, rule.component): rule.params
              for rule in ir.itertuples() if rule.component in _EXPECTED}

    def lookup(shape, key, violation_type):
        component = "sh:datatype" if violation_type == "triplets:lexicalForm" else violation_type
        value = params.get((shape, key, component))
        return None if value is None else _EXPECTED[component](value)

    return [lookup(shape, key, violation_type) for shape, key, violation_type in
            zip(violations["SOURCE_SHAPE"], violations["KEY"], violations["VIOLATION_TYPE"])]


def _source_shape_graphs(violations, compiled):
    """{shape id: rdflib CBD graph} of the violated shapes — the SHACL report
    embeds these so sh:sourceShape is never an empty node (term identity is
    preserved: the CBD's blank nodes ARE the compiled graph's, so they align
    with the report's sh:sourceShape labels)."""
    import rdflib

    graphs = {}
    if compiled.graph is None:           # schema-compiled IR — no shapes graph to embed
        return graphs
    for shape in set(violations["SOURCE_SHAPE"].dropna().astype(str)):
        node = (rdflib.URIRef(shape) if "://" in shape or shape.startswith("urn:")
                else rdflib.BNode(shape))
        cbd = compiled.graph.cbd(node)
        if len(cbd):
            graphs[shape] = cbd
    return graphs


def _message_sources(violations, ir):
    """Per row: "shacl" when the text is the shape's own sh:message (engines
    use it verbatim; post-pass suffixes append after it), else "engine"."""
    authored = tuple(sorted({rule.message for rule in ir.itertuples()
                             if isinstance(rule.message, str) and rule.message},
                            key=len, reverse=True))
    return ["shacl" if isinstance(message, str) and message.startswith(authored) else "engine"
            for message in violations["MESSAGE"]]


def validate_schema(data, rdf_map, data_scope=None, rdf_map_scope=None, report_undefined_keys=False,
                    engine="auto", undefined_namespace=CIM_NS, **kwargs):
    """Validate triplet data against the export schema: cardinality, datatypes and
    enumerations of the scoped profiles, per instance.

    Every instance in *data_scope* is validated on its own (cardinality never
    counts rows of another instance) against the profiles in *rdf_map_scope*,
    merged into one (``triplets.validation.schema_scope``). Class rules never
    widen to subclasses; references are checked by ``validate_schema_references``.

    data_scope : iterable of INSTANCE_ID, optional
        The instances to validate; None = every instance in *data*.
    rdf_map_scope : profile identifier or sequence of them
        Section keys and/or declared profile IRIs (``["EQ", "OP", "SC"]`` for a
        CGMES 3.0 EQ file); ``"*"`` = every section; None = the root entries.
        A scope without Class entries raises ValueError.
    report_undefined_keys : bool, default False
        Also report keys on an object that no scoped profile defines for its class.

    The PROFILE column lists the scoped section keys (``["EQ", "OP", "SC"]``).
    """
    started = datetime.now(timezone.utc)
    compiled = compile_schema(rdf_map, rdf_map_scope, report_undefined_keys)   # caches on the file digest
    rdf_map = load_rdf_map(rdf_map)
    sections = scope_sections(rdf_map, rdf_map_scope)
    table_name = _table_ref(data, **kwargs)
    engine_name, engine_mod = get_engine(engine)

    instances = list(data_scope) if data_scope is not None else _instances(data, table_name)
    # one raw engine pass per instance — the presentation pass runs ONCE on the concat
    frames = [_run(data, compiled, engine_name, engine_mod, None, [instance], False,
                   undefined_namespace=undefined_namespace, **kwargs) for instance in instances]
    violations = (pandas.concat(frames, ignore_index=True) if frames
                  else pandas.DataFrame(columns=VIOLATION_COLUMNS))
    violations["PROFILE"] = _profile_column(violations, sections)
    violations = _present(violations, compiled.ir, "rdfs")
    violations.attrs["validation"] = _schema_metadata(compiled, engine_name, started, data, table_name, sections)
    return violations


def validate_schema_references(data, rdf_map, data_scope, rdf_map_scope, target_data_scope=(),
                               target_rdf_map_scope=(), report_missing_targets=True, engine="auto",
                               inheritance=True, undefined_namespace=CIM_NS, **kwargs):
    """Check the reference types of the source instances' associations.

    Every instance in *data_scope* is checked on its own against the
    associations of its profiles (*rdf_map_scope*, merged); the referenced
    objects are looked up in that instance and the *target_data_scope*
    instances. A reference conforms when ANY type of its object is the range
    class or, with *inheritance*, one of its subclasses — the classes the source
    and *target_rdf_map_scope* profiles define. Findings are on source objects only.

    data_scope : iterable of INSTANCE_ID
        The source instances.
    rdf_map_scope : profile identifier or sequence of them
        The source profiles — their associations are checked.
    target_data_scope : iterable of INSTANCE_ID, optional
        Instances holding the referenced objects (e.g. the EQ for an SSH).
    target_rdf_map_scope : profile identifier or sequence of them, optional
        Profiles defining the referenced classes. A range class no source or
        target profile defines cannot be decided: the association is listed in
        ``skipped_shapes``.
    report_missing_targets : bool, default True
        Report references whose object is in neither the source nor the target
        instances (``triplets:missing``); False leaves them silent.
    inheritance : bool, default True
        False accepts the range class itself only.
    """
    started = datetime.now(timezone.utc)
    compiled = compile_references(rdf_map, rdf_map_scope, target_rdf_map_scope, report_missing_targets, inheritance)
    rdf_map = load_rdf_map(rdf_map)
    sections = scope_sections(rdf_map, rdf_map_scope)
    table_name = _table_ref(data, **kwargs)
    engine_name, engine_mod = get_engine(engine)
    targets = list(target_data_scope or ())
    frames = []
    for instance in data_scope:
        found = _run(data, compiled, engine_name, engine_mod, None,
                     [instance, *(target for target in targets if target != instance)], False,
                     undefined_namespace=undefined_namespace, **kwargs)
        # the association triple itself must be in the source instance — an object
        # repeated in a target file (EQ and SSH share IDs) brings the target's own rows
        own = _instance_triples(data, instance, compiled.ir["path"].dropna().unique().tolist(), table_name)
        mask = [triple in own for triple in zip(found["ID"], found["KEY"], found["VALUE"])]
        frames.append(found.loc[pandas.Series(mask, index=found.index, dtype=bool)])
    violations = (pandas.concat(frames, ignore_index=True) if frames
                  else pandas.DataFrame(columns=VIOLATION_COLUMNS))
    violations["PROFILE"] = _profile_column(violations, sections)
    violations = _present(violations, compiled.ir, "rdfs")
    violations.attrs["validation"] = _schema_metadata(compiled, engine_name, started, data, table_name, sections)
    violations.attrs["validation"]["inheritance"] = inheritance
    return violations


def _schema_metadata(compiled, engine_name, started, data, table_name, sections):
    """Run metadata of the schema validations (no shapes graph, rdfs language)."""
    import triplets

    finished = datetime.now(timezone.utc)
    return {
        "started_at": _iso(started),
        "generated_at": _iso(finished),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "engine": engine_name,
        "creator": f"triplets {triplets.__version__}",
        "source": _source_labels(data, table_name=table_name),
        "references": list(compiled.sources),
        "compiled_hash": compiled.hash,
        "language": "rdfs",
        "profiles": sections,
        "node_shapes": compiled.stats["node_shapes"],
        "constraints": compiled.stats["constraints"],
        "skipped_shapes": list(compiled.stats["skipped_shapes"]),
        "relaxed_constraints": list(compiled.stats["relaxed_constraints"]),
        "skipped_components": sorted(compiled.plans.get(engine_name, ((), (), ()))[2]),
        "source_shapes": {},
    }


def _instance_triples(data, instance, keys, table_name="triplets"):
    """{(ID, KEY, VALUE)} of one instance's rows at *keys* (any input flavor)."""
    kind = flavor(data)
    if kind == "duckdb":
        if not keys:
            return set()
        placeholders = ", ".join("?" for _ in keys)
        return set(data.execute(f"SELECT ID, KEY, VALUE FROM {table_name} WHERE INSTANCE_ID = ? "
                                f"AND KEY IN ({placeholders})", [str(instance), *keys]).fetchall())
    if kind == "pyarrow":
        data = data.to_pandas(types_mapper=pandas.ArrowDtype)
    if kind == "polars":
        rows = data.filter((data["INSTANCE_ID"] == str(instance)) & data["KEY"].is_in(keys))
        return set(zip(rows["ID"].to_list(), rows["KEY"].to_list(), rows["VALUE"].to_list()))
    rows = data.loc[(data["INSTANCE_ID"].astype(str) == str(instance)) & data["KEY"].isin(keys)]
    return set(zip(rows["ID"].astype(str), rows["KEY"].astype(str), rows["VALUE"].astype(str)))


def _profile_column(violations, sections):
    """PROFILE: the list of scoped section keys on every row (None for the root entries)."""
    return pandas.Series([None if sections is None else list(sections) for _ in range(len(violations))],
                         index=violations.index, dtype=object)


def _instances(data, table_name="triplets"):
    """Every INSTANCE_ID in the data, in first-seen order (any input flavor)."""
    kind = flavor(data)
    if kind == "duckdb":
        return [row[0] for row in data.execute(f"SELECT DISTINCT INSTANCE_ID FROM {table_name}").fetchall()]
    if kind == "pyarrow":
        data = data.to_pandas(types_mapper=pandas.ArrowDtype)
    if kind == "polars":
        return data["INSTANCE_ID"].unique(maintain_order=True).to_list()
    return data["INSTANCE_ID"].unique().tolist()


def _report_metadata(violations, data, compiled, engine_name, started, table_name="triplets", notes=None):
    """The validation-run facts every report exporter reads (violations.attrs).

    Coverage (skipped_shapes / skipped_components) is what THIS run did not
    evaluate: the schema merge's skips in *notes* (every engine), compile-level skips
    plus the engine's own gaps for vectorized engines — none of those for the
    spec-complete pyshacl reference engine. relaxed_constraints: the merge's
    loosened checks.
    """
    import triplets

    finished = datetime.now(timezone.utc)
    notes = notes or {"skipped_shapes": [], "relaxed_constraints": []}
    if engine_name == "pyshacl":
        skipped_shapes, skipped_components = list(notes["skipped_shapes"]), []
    else:
        skipped_shapes = [*notes["skipped_shapes"], *compiled.stats.get("skipped_shapes", [])]
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
        "compiled_hash": compiled.hash,
        "node_shapes": compiled.stats.get("node_shapes", 0),
        "constraints": compiled.stats.get("constraints", 0),
        "language": compiled.language,
        "skipped_shapes": skipped_shapes,
        "relaxed_constraints": list(notes["relaxed_constraints"]),
        "skipped_components": skipped_components,
        # rdflib graphs, in-memory only — the SHACL report embeds them;
        # SARIF properties and the csv/excel sidecars skip this key
        "source_shapes": _source_shape_graphs(violations, compiled),
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
