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
  the value nodes (on a node shape, the focus node); sh:nodeKind is decided by the rdf_map schema)
- duckdb — compiled-IR executor for larger-than-memory data (one SQL query
  per constraint against the triplets table, streams/spills via DuckDB;
  explicit engine="duckdb", not in auto — polars owns the in-memory fast path)

Compile once: ``compile(shapes)`` parses the shapes with rdflib exactly once
into ``CompiledShapes`` (shapes graph + flat constraint table, cached by
content hash and file names). Engines receive the compiled object::

    validate(data, compiled, rdf_map=None, data_scope=None, ancestors=None, **kwargs) → violations DataFrame

(``rdf_map`` the scoped schema, ``ancestors`` the ``class_ancestors`` map.)

pyshacl consumes the graph; the vectorized engines consume the IR (and cache
their own plan per engine in ``CompiledShapes.plans``) — they never touch
rdflib. sh:sparql IR rows and SPARQL targets are delegated to triplets.sparql
by the vectorized engines (pyshacl evaluates both itself via advanced=True).

A deliberate deviation from pyshacl: the datatype check inspects the raw
lexical form of VALUE (see shacl_pandas) — with ``lexical=True`` (default)
every engine's report carries the triplets:lexicalForm Warnings, with
``lexical=False`` none does.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import pandas

from .._engine_detect import flavor
from .._registry import EngineRegistry
from ..iri import CIM_NS, LITERAL, REFERENCE, absolute_value, is_iri, load_rdf_map, local_value, split_iri, value_types
from .shacl_ir import CompiledShapes, compile_shapes as compile  # noqa: A001 — public API name
from .shacl_ir import MESSAGE_VARIABLE, _is_iri, _mark_switched_off, _term, is_literal
from .schema_ir import compile_references, compile_schema, PRESENTED as _PRESENTED  # noqa: F401 — public API
from .schema_ir import schema_name
from .schema_scope import SCOPE_KEY, _class_ancestors, require_entries, scope_sections
from .schema_scope import class_ancestors, scoped_rdf_map  # noqa: F401 — public API
from .shacl_report import (VALIDATION_FAILURE, VIOLATION_COLUMNS, export_to_shacl_report, has_violation,  # noqa: F401 — public API
                           violations_to_csv, violations_to_excel)
from .context import ENRICHMENT_COLUMNS, concat_violations, enrich  # noqa: F401 — public API
from .context import file_names as _file_names
from .locations import LOCATION_COLUMNS, locate_violations  # noqa: F401 — public API
from .sarif import export_to_sarif  # noqa: F401 — public API
from .value_space import pair_operands, range_bound

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
                   undefined_namespace=CIM_NS, inheritance=False, deactivated_shapes=None, abort_on_first=False,
                   **kwargs):
    """Validate triplet data against SHACL shapes; return a violations DataFrame.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    shapes : str | path | list of paths | rdflib.Graph | CompiledShapes
        SHACL shapes (format auto-detected by extension). Pass the result of
        ``compile(shapes)`` to reuse the parsed shapes across validations.
    rdf_map : dict or str, optional
        Export schema — xsd-typed literals in the data graph, the schema-driven
        sh:nodeKind decision, the value space of the sh:lessThan /
        sh:lessThanOrEquals operands and the class inheritance (see
        ``inheritance``), from the profiles in ``rdf_map_scope``.
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
        Report valid-but-narrower datatype forms (``"1"`` for xsd:float) as
        triplets:lexicalForm Warnings — a deliberate deviation from pyshacl,
        see shacl_pandas. The vectorized engines check them inline, pyshacl
        gets the pandas datatype check as a supplement. False skips the check
        on every engine (pyshacl: the pure pyshacl report); values outside the
        lexical space stay sh:datatype violations.
    undefined_namespace : str, default CIM100
        Namespace for names the schema does not declare (every name without
        an rdf_map) in the graph the sh:sparql constraints and pyshacl see.
    inheritance : bool, default False
        False matches the exact ``rdf:type`` only: plain SHACL reads
        ``rdfs:subClassOf`` from the data graph, and CIM files carry none. True:
        ``sh:targetClass`` and ``sh:class`` also match instances of subclasses,
        from the scoped profiles' ``inheritance`` lists
        (:func:`~triplets.validation.schema_scope.class_ancestors`; pyshacl: as
        ``rdfs:subClassOf`` in ``ont_graph``). The ancestor map is one per run,
        built from all profiles in ``rdf_map_scope``: same-named Class entries
        union their ``inheritance`` lists, and the closure goes only through
        ancestors that have a Class entry in the scope. No effect without an rdf_map.
    deactivated_shapes : shape IRI or iterable of them, optional
        The shapes to switch off for this run — replaces the set the graph
        declares (``compiled.deactivated``): None = the declared set, ``[]`` =
        every shape runs, the declared ones included; extend or reduce it
        freely. The NC validation aggregates switch off different shapes of a
        shared base this way, and one compile serves every aggregate
        (``CompiledShapes.active``).
    abort_on_first : bool, default False
        Stop at the first Violation — for a yes/no gate, not a full report.
        Warning and Info findings and ``triplets:invalidSparql`` / ``triplets:validationFailure`` rows never
        stop the run. The result holds the findings of the work finished
        before the stop, so engines agree on whether a Violation exists, not
        on the rows. polars / duckdb stop after the vectorized pass, pandas
        between rules, pyshacl after its first failing constraint (the lexical
        supplement is then skipped).

    Notes
    -----
    The returned frame carries the validation-run metadata in
    ``violations.attrs["validation"]`` (start/end timestamps and duration,
    engine, tool version, data/shape file names, shape and constraint counts,
    any shapes/components the run skipped, and ``aborted``: True when
    ``abort_on_first`` met a Violation) — every report exporter reads
    it, so SARIF, the sh:ValidationReport and the csv/excel exports tell the
    same story.
    """
    compiled = (shapes if isinstance(shapes, CompiledShapes) else compile(shapes)).active(deactivated_shapes)
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
                      undefined_namespace=undefined_namespace, ancestors=ancestors,
                      abort_on_first=abort_on_first, **kwargs)
    violations = _focus_kinds(violations, compiled.ir, value_types(scoped[SCOPE_KEY]) if rdf_map else {},
                              lambda ids: _subjects(data, ids, data_scope, table_ref))
    violations = _present(violations, compiled.ir, compiled.language)
    violations.attrs["validation"] = _report_metadata(
        violations, data, compiled, engine_name, started, table_name=table_ref, notes=notes)
    # a run the engine gave up on (pyshacl's ValidationFailure) is partial: said where the coverage is read
    violations.attrs["validation"]["skipped_shapes"] += list(
        violations.loc[violations["VIOLATION_TYPE"] == VALIDATION_FAILURE, "MESSAGE"])
    violations.attrs["validation"]["aborted"] = abort_on_first and has_violation(violations)
    violations.attrs["validation"]["inheritance"] = bool(ancestors)   # applied: an rdf_map with classes, the flag on
    violations.attrs["validation"]["profiles"] = (scoped[SCOPE_KEY].get("ProfileMetadata", {}).get("sections")
                                                  if rdf_map else None)
    return violations


def _run(data, compiled, engine_name, engine_mod, rdf_map, data_scope, lexical=True, undefined_namespace=CIM_NS,
         ancestors=None, abort_on_first=False, **kwargs):
    """One engine pass, plus the lexical datatype supplement when the engine
    does not emit it itself — raw violations, presentation columns not yet added."""
    violations = engine_mod.validate(data, compiled, rdf_map=rdf_map, data_scope=data_scope, lexical=lexical,
                                     undefined_namespace=undefined_namespace, ancestors=ancestors,
                                     abort_on_first=abort_on_first, **kwargs)
    if lexical and engine_name not in _LEXICAL_BUILTIN and not (abort_on_first and has_violation(violations)):
        from . import shacl_pandas
        supplement = shacl_pandas.validate(data, compiled, rdf_map=rdf_map, data_scope=data_scope,
                                           components=("sh:datatype",), undefined_namespace=undefined_namespace,
                                           ancestors=ancestors)
        violations = pandas.concat([violations, supplement], ignore_index=True)
    # identical findings once: a property shape on a parent and a child node shape
    # both reach a subclass instance, the same triple sits in two instance files,
    # the lexical supplement repeats pyshacl (in its own words). The solutions of a
    # sh:sparql constraint are told apart by their message too: two that differ in
    # another variable than ?value are two findings, and by the term kind of ?value
    # (the class Breaker and the literal "Breaker")
    solution = violations["MESSAGE"].where(violations["VIOLATION_TYPE"] == "sh:sparql")
    kinds = ["VALUE_KIND"] if "VALUE_KIND" in violations.columns else []
    repeated = violations.assign(MESSAGE=solution).duplicated(subset=["ID", "KEY", "VALUE", "VIOLATION_TYPE",
                                                                      "SOURCE_SHAPE", "SEVERITY", "MESSAGE", *kinds])
    return violations.loc[~repeated].reset_index(drop=True)


def _focus_kinds(violations, ir, types, subjects):
    """ID_KIND (``iri.TERM_KINDS``) per row when a focus node is a literal. A focus that
    a ``sh:targetObjectsOf`` KEY gives has the term kind the export writes for that VALUE
    (:func:`~triplets.iri.absolute_value`: the KEY's schema entry, else the text — an IRI
    or UUID is a node, any other text a literal); a focus any other target gives is a
    node. When the targets of the row's shape give its ID both kinds, the ID is a node if
    it is a subject in the data (*subjects*: ids → the subjects among them), else a
    literal. No column when no focus is a literal. A frame with the column (the pyshacl
    report's terms) fills the rows without a kind (the lexical supplement) as nodes."""
    if "ID_KIND" in violations.columns:
        violations["ID_KIND"] = violations["ID_KIND"].fillna(REFERENCE)
        return violations
    targets = {}   # shape → the KEYs of its sh:targetObjectsOf, None for any other target
    for shape, target_by, target_on in zip(ir["shape_id"], ir["target_by"], ir["target_on"]):
        targets.setdefault(shape, set()).add(target_on if target_by == "VALUE" else None)
    targets = {shape: keys for shape, keys in targets.items() if keys != {None}}

    def term_kinds(shape, focus):
        """The term kinds the targets of *shape* give *focus*."""
        if shape not in targets or pandas.isna(focus):
            return {REFERENCE}
        return {LITERAL if key is not None and absolute_value(key, str(focus), value_types=types)[0] == LITERAL
                else REFERENCE for key in targets[shape]}

    kinds = [term_kinds(shape, focus) for shape, focus in zip(violations["SOURCE_SHAPE"], violations["ID"])]
    if not any(LITERAL in kind for kind in kinds):
        return violations
    both = {focus for focus, kind in zip(violations["ID"], kinds) if len(kind) > 1}
    nodes = subjects(both) if both else set()
    violations["ID_KIND"] = [LITERAL if kind == {LITERAL} or (len(kind) > 1 and focus not in nodes) else REFERENCE
                             for focus, kind in zip(violations["ID"], kinds)]
    return violations


def _subjects(data, ids, data_scope=None, table_name="triplets"):
    """The *ids* that are a subject (an ID) in the data's *data_scope* instances (any input flavor)."""
    ids = [str(identifier) for identifier in ids]
    scope = None if data_scope is None else [str(instance) for instance in data_scope]
    kind = flavor(data)
    if kind == "duckdb":
        where = f"ID IN ({', '.join('?' for _ in ids)})"
        if scope is not None:
            where += f" AND INSTANCE_ID IN ({', '.join('?' for _ in scope) or 'NULL'})"
        return {row[0] for row in data.execute(f"SELECT DISTINCT ID FROM {table_name} WHERE {where}",
                                               [*ids, *(scope or [])]).fetchall()}
    if kind == "pyarrow":
        data = data.to_pandas(types_mapper=pandas.ArrowDtype)
    if kind == "polars":
        rows = data.filter(data["ID"].is_in(ids))
        if scope is not None:
            rows = rows.filter(rows["INSTANCE_ID"].is_in(scope))
        return set(rows["ID"].to_list())
    rows = data.loc[data["ID"].isin(ids)]
    if scope is not None:
        rows = rows.loc[rows["INSTANCE_ID"].astype(str).isin(scope)]
    return set(rows["ID"].astype(str))


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


def _shown(member):
    """A sh:in / sh:hasValue member as EXPECTED words it: an IRI by its local name, a literal
    as its text."""
    return local_value(str(member)) if is_iri(str(member)) and not is_literal(member) else str(member)


# what a violated constraint requires, worded from its IR parameter — the
# report states what IS allowed even when the authored message does not
_EXPECTED = {
    "sh:minCount": "at least {} value(s)".format,
    "sh:maxCount": "at most {} value(s)".format,
    "sh:minLength": "length >= {}".format,
    "sh:maxLength": "length <= {}".format,
    "sh:minInclusive": lambda params: f"value >= {range_bound(params)[1]}",
    "sh:maxInclusive": lambda params: f"value <= {range_bound(params)[1]}",
    "sh:minExclusive": lambda params: f"value > {range_bound(params)[1]}",
    "sh:maxExclusive": lambda params: f"value < {range_bound(params)[1]}",
    "sh:pattern": "value matching {}".format,
    "sh:datatype": "a {} value".format,
    "sh:class": "a reference to a {}".format,
    "sh:nodeKind": "an {} value".format,
    "sh:hasValue": lambda param: f"value {_shown(param)}",
    "sh:in": lambda params: "one of: " + ", ".join(dict.fromkeys(_shown(param) for param in params)),
    "triplets:range": lambda params: "a reference to one of: " + ", ".join(map(str, params)),
    "triplets:missing": lambda params: "a referenced object in the data, one of: " + ", ".join(map(str, params)),
    "sh:equals": "equal to {}".format,
    "sh:disjoint": "different from {}".format,
    "sh:lessThan": lambda params: f"less than {pair_operands(params)[0]}",
    "sh:lessThanOrEquals": lambda params: f"at most {pair_operands(params)[0]}",
    "sh:xone": lambda params: f"exactly one of {len(params)} alternatives",
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


def _source_shapes(violations, compiled):
    """{shape id: N-Triples text} of the violated shapes' definitions (CBD) — the
    SHACL report embeds these so sh:sourceShape is never an empty node. Text, so
    the frame's attrs stay plain values; the blank-node labels in it are the
    compiled graph's, the ones SOURCE_SHAPE names (shacl_report.shape_triples
    reads them back unchanged). sh:deactivated as the run used it."""
    texts = {}
    if compiled.graph is None:           # schema-compiled IR — no shapes graph to embed
        return texts
    for shape in set(violations["SOURCE_SHAPE"].dropna().astype(str)):
        cbd = compiled.graph.cbd(_term(shape))
        if len(cbd):
            texts[shape] = _mark_switched_off(cbd, compiled).serialize(format="nt")
    return texts


def _message_sources(violations, ir):
    """Per row: "shacl" when the text is the shape's own sh:message (engines
    use it verbatim; post-pass suffixes append after it), else "engine"."""
    authored = tuple(sorted({rule.message for rule in ir.itertuples()
                             if isinstance(rule.message, str) and rule.message},
                            key=len, reverse=True))
    # the sh:sparql messages of a shape, their {?variable} placeholders filled with any values
    filled = {}
    for rule in ir.itertuples():
        if rule.component == "sh:sparql" and isinstance(rule.message, str) and MESSAGE_VARIABLE.search(rule.message):
            parts = MESSAGE_VARIABLE.sub("\0", rule.message).split("\0")
            filled.setdefault(rule.shape_id, []).append(re.compile(".*".join(map(re.escape, parts)), re.DOTALL))

    def is_authored(message, violation_type, shape):
        return message.startswith(authored) or (violation_type == "sh:sparql"
                                                and any(pattern.match(message) for pattern in filled.get(shape, ())))
    return ["shacl" if isinstance(message, str) and is_authored(message, violation_type, shape) else "engine"
            for message, violation_type, shape in zip(violations["MESSAGE"], violations["VIOLATION_TYPE"],
                                                      violations["SOURCE_SHAPE"])]


def validate_schema(data, rdf_map, data_scope=None, rdf_map_scope=None, report_undefined_keys=False,
                    engine="auto", severity="Violation", **kwargs):
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
    severity : {"Violation", "Warning", "Info"}, default "Violation"
        The SEVERITY of every finding (``triplets:lexicalForm`` stays Warning).

    The PROFILE column lists the scoped section keys (``["EQ", "OP", "SC"]``).
    """
    started = datetime.now(timezone.utc)
    schema = load_rdf_map(rdf_map)
    compiled = compile_schema(schema, rdf_map_scope, report_undefined_keys, severity)
    sections = scope_sections(schema, rdf_map_scope)
    table_name = _table_ref(data, **kwargs)
    engine_name, engine_mod = get_engine(engine)

    instances = list(data_scope) if data_scope is not None else _instances(data, table_name)
    # one raw engine pass per instance — the presentation pass runs ONCE on the concat
    frames = [_run(data, compiled, engine_name, engine_mod, None, [instance], **kwargs) for instance in instances]
    violations = (pandas.concat(frames, ignore_index=True) if frames
                  else pandas.DataFrame(columns=VIOLATION_COLUMNS))
    violations["PROFILE"] = _profile_column(violations, sections)
    violations = _present(violations, compiled.ir, "rdfs")
    violations.attrs["validation"] = _schema_metadata(compiled, rdf_map, engine_name, started, data, table_name,
                                                     sections)
    return violations


def validate_schema_references(data, rdf_map, data_scope=None, rdf_map_scope=None, target_data_scope=None,
                               target_rdf_map_scope=(), report_missing_targets=True, engine="auto",
                               inheritance=True, severity="Violation", **kwargs):
    """Check the reference types of the source instances' associations.

    Every instance in *data_scope* is checked on its own against the
    associations of its profiles (*rdf_map_scope*, merged); the referenced
    objects are looked up in that instance and the *target_data_scope*
    instances. A reference conforms when ANY type of its object is the range
    class or, with *inheritance*, one of its subclasses — the classes the source
    and *target_rdf_map_scope* profiles define. Findings are on source objects only.

    data_scope : iterable of INSTANCE_ID, optional
        The source instances; None = every instance.
    rdf_map_scope : profile identifier or sequence of them, optional
        The source profiles — their associations are checked; None = the root entries.
    target_data_scope : iterable of INSTANCE_ID, optional
        Instances holding the referenced objects (e.g. the EQ for an SSH);
        None = every instance, ``[]`` = none (only the source itself).
    target_rdf_map_scope : profile identifier or sequence of them, optional
        Profiles defining the referenced classes. A range class no source or
        target profile defines cannot be decided: the association is listed in
        ``skipped_shapes``.
    report_missing_targets : bool, default True
        Report references whose object is in neither the source nor the target
        instances (``triplets:missing``); False leaves them silent.
    inheritance : bool, default True
        False accepts the range class itself only.
    severity : {"Violation", "Warning", "Info"}, default "Violation"
        The SEVERITY of every finding.
    """
    started = datetime.now(timezone.utc)
    schema = load_rdf_map(rdf_map)
    compiled = compile_references(schema, rdf_map_scope, target_rdf_map_scope, report_missing_targets, inheritance,
                                  severity)
    sections = scope_sections(schema, rdf_map_scope)
    table_name = _table_ref(data, **kwargs)
    engine_name, engine_mod = get_engine(engine)
    loaded = _instances(data, table_name) if data_scope is None or target_data_scope is None else None
    sources = loaded if data_scope is None else list(data_scope)
    targets = loaded if target_data_scope is None else list(target_data_scope)
    # the association triple itself must be in the source instance — an object
    # repeated in a target file (EQ and SSH share IDs) brings the target's own rows
    own_triples = _instance_triples(data, sources, compiled.ir["path"].dropna().unique().tolist(), table_name)
    frames = []
    for instance in sources:
        found = _run(data, compiled, engine_name, engine_mod, None,
                     [instance, *(target for target in targets if target != instance)], **kwargs)
        own = own_triples.get(str(instance), set())
        mask = [triple in own for triple in zip(found["ID"], found["KEY"], found["VALUE"])]
        frames.append(found.loc[pandas.Series(mask, index=found.index, dtype=bool)])
    violations = (pandas.concat(frames, ignore_index=True) if frames
                  else pandas.DataFrame(columns=VIOLATION_COLUMNS))
    violations["PROFILE"] = _profile_column(violations, sections)
    violations = _present(violations, compiled.ir, "rdfs")
    violations.attrs["validation"] = _schema_metadata(compiled, rdf_map, engine_name, started, data, table_name,
                                                     sections)
    violations.attrs["validation"]["inheritance"] = inheritance
    return violations


def _schema_metadata(compiled, rdf_map, engine_name, started, data, table_name, sections):
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
        # the entry point's own argument: the compile below it was handed the loaded dict
        "references": [schema_name(rdf_map)],
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


def _instance_triples(data, instances, keys, table_name="triplets"):
    """{INSTANCE_ID: {(ID, KEY, VALUE)}} of the *instances*' rows at *keys* — one pass
    over the data for every source instance (any input flavor)."""
    instances = [str(instance) for instance in instances]
    if not instances or not keys:
        return {}
    kind = flavor(data)
    if kind == "duckdb":
        rows = pandas.DataFrame(data.execute(
            f"SELECT INSTANCE_ID, ID, KEY, VALUE FROM {table_name} "
            f"WHERE INSTANCE_ID IN ({', '.join('?' for _ in instances)}) "
            f"AND KEY IN ({', '.join('?' for _ in keys)})", [*instances, *keys]).fetchall(),
            columns=["INSTANCE_ID", "ID", "KEY", "VALUE"])
    else:
        if kind == "pyarrow":
            data = data.to_pandas(types_mapper=pandas.ArrowDtype)
        if kind == "polars":
            rows = data.filter(data["INSTANCE_ID"].is_in(instances) & data["KEY"].is_in(keys)).to_pandas()
        else:
            rows = data.loc[data["KEY"].isin(keys)]
            rows = rows.loc[rows["INSTANCE_ID"].astype(str).isin(instances)]
    triples = {}
    for instance, identifier, key, value in zip(rows["INSTANCE_ID"].astype(str), rows["ID"].astype(str),
                                                rows["KEY"].astype(str), rows["VALUE"].astype(str)):
        triples.setdefault(instance, set()).add((identifier, key, value))
    return triples


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
    plus the engine's own gaps for vectorized engines — only the malformed shapes'
    entries for the spec-complete pyshacl reference engine. relaxed_constraints: the
    merge's loosened checks.
    """
    import triplets

    finished = datetime.now(timezone.utc)
    notes = notes or {"skipped_shapes": [], "relaxed_constraints": []}
    if engine_name == "pyshacl":
        skipped_shapes = [*notes["skipped_shapes"], *compiled.stats.get("malformed_shapes", [])]
        skipped_components = []
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
        "deactivated_shapes": sorted(compiled.deactivated),
        "node_shapes": compiled.stats.get("node_shapes", 0),
        "constraints": compiled.stats.get("constraints", 0),
        "language": compiled.language,
        "skipped_shapes": skipped_shapes,
        "relaxed_constraints": list(notes["relaxed_constraints"]),
        "skipped_components": skipped_components,
        "anonymous_shapes": _anonymous_shapes(violations, compiled),
        # the violated shapes' definitions as N-Triples — the SHACL report embeds
        # them; SARIF properties and the csv/excel sidecars skip this key
        "source_shapes": _source_shapes(violations, compiled),
    }


def _anonymous_shapes(violations, compiled):
    """{blank-node label: stable name} of the violated shapes without an IRI — the label
    is new at every parse, the stable name is the shape part of the SARIF ruleId: the
    nearest named shape above it, its path, and eight hex digits of the SHA-1 of that
    shape's IRI and the canonical definition (``BreakerShape.IdentifiedObject.name-5c1d07aa``;
    ``BreakerShape-5c1d07aa`` without a path, ``shape-5c1d07aa`` with no named shape
    above). Two anonymous shapes of one parent and path have two names."""
    labels = {shape for shape in violations["SOURCE_SHAPE"].dropna().astype(str) if not _is_iri(shape)}
    if not labels or compiled.graph is None:
        return {}
    ir = (compiled.base or compiled).ir
    rows = ir.loc[ir["shape_id"].isin(labels)].drop_duplicates("shape_id").set_index("shape_id")
    stable = {}
    for label in sorted(labels):
        lineage = rows.at[label, "shapes"] if label in rows.index else ()
        lineage = lineage[:lineage.index(label)] if label in lineage else ()   # the shapes above it
        path = rows.at[label, "path"] if label in rows.index else None
        named = next((shape for shape in reversed(lineage) if _is_iri(shape)), None)
        name = ".".join(filter(None, (split_iri(named)[1] if named else "shape", path if isinstance(path, str) and named else None)))
        stable[label] = f"{name}-{_definition_digest(compiled.graph, label, named)}"
    return stable


def _definition_digest(graph, label, parent=None):
    """Eight hex digits of the SHA-1 of a blank-node shape's definition (CBD), its
    blank nodes labelled canonically, and the IRI of the named shape above it: the
    same for the same Turtle in every parse."""
    import hashlib
    from rdflib.compare import to_canonical_graph

    lines = sorted(to_canonical_graph(graph.cbd(_term(label))).serialize(format="nt").splitlines())
    return hashlib.sha1("\n".join([parent or "", *lines]).encode("utf-8")).hexdigest()[:8]


def _iso(moment):
    """UTC datetime → Zulu ISO string — one lexical form across RDF and SARIF."""
    return moment.isoformat().replace("+00:00", "Z")


def _source_labels(data, table_name="triplets"):
    """The distinct file names of the data's instances, any input flavor — the
    names context.enrich writes as INSTANCE_LABEL (context.file_names)."""
    return list(dict.fromkeys(_file_names(data, table_name).values()))
