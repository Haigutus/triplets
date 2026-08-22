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
from .shacl_ir import CompiledShapes, IR_COLUMNS, compile_shapes as compile  # noqa: A001 — public API name
from .schema_ir import compile_schema, PRESENTED as _PRESENTED  # noqa: F401 — public API
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
        Validate only these instances' named graphs — data outside the scope
        is not loaded, so references into unscoped instances count as absent.
        Include dependency instances in the scope (or validate the full
        union, scope=None) for cross-instance checks.
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
    table_ref = _table_ref(data, **kwargs)
    violations = _run(data, compiled, engine_name, engine_mod, rdf_map, scope, lexical, **kwargs)
    violations = _present(violations, data, compiled.ir, compiled.language, table_ref)
    if context:
        violations = enrich(violations, data=data, shapes=compiled, rdf_map=rdf_map)
    violations.attrs["validation"] = _report_metadata(
        violations, data, compiled, engine_name, started, table_name=table_ref)
    return violations


def _run(data, compiled, engine_name, engine_mod, rdf_map, scope, lexical, **kwargs):
    """One engine pass, plus the lexical datatype supplement when the engine
    does not emit it itself — raw violations, presentation columns not yet added."""
    violations = engine_mod.validate(data, compiled, rdf_map=rdf_map, scope=scope, **kwargs)
    if lexical and engine_name not in _LEXICAL_BUILTIN:
        from . import shacl_pandas
        supplement = shacl_pandas.validate(data, compiled, rdf_map=rdf_map, scope=scope,
                                           components=("sh:datatype",))
        violations = (pandas.concat([violations, supplement], ignore_index=True)
                      .drop_duplicates(subset=["ID", "KEY", "VALUE", "VIOLATION_TYPE",
                                               "SOURCE_SHAPE", "SEVERITY"], ignore_index=True))
    return violations


def _present(violations, data, ir, language, table_name="triplets"):
    """The shared presentation pass: TARGET / EXPECTED / MESSAGE_SOURCE and
    the vocabulary-accurate violation types for non-SHACL constraint languages."""
    violations = _describe_associations(violations, data, ir, table_name=table_name)
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


def _describe_associations(violations, data, ir, table_name="triplets"):
    """Findings get a TARGET entry stating what was actually found in the
    data — the raw MESSAGE stays verbatim (exporters emit TARGET as its own
    [context_message] entry), so the error is self-contained:

    - reference checks (``sh:class``/``triplets:range``, VALUE = the ref):
      the target's id, Type and name — or the fact that it does not exist;
    - valueType rows (``via_type``): the found type (the id is not carried);
    - ``sh:maxCount``: the actual duplicate values.
    One shared pass over the violations frame; no per-engine code.
    """
    violations["TARGET"] = pandas.Series(None, index=violations.index, dtype=object)
    if violations.empty or ir.empty:
        return violations
    via_rules = set(zip(ir.loc[ir["via_type"], "shape_id"],
                        ir.loc[ir["via_type"], "path"]))
    keys = pandas.Series(list(zip(violations["SOURCE_SHAPE"], violations["KEY"])),
                         index=violations.index)
    described = violations["VALUE"].notna()
    via = described & keys.isin(via_rules)
    of_class = (described & ~via
                & violations["VIOLATION_TYPE"].isin(("sh:class", "triplets:range")))
    over_count = violations["VIOLATION_TYPE"].eq("sh:maxCount") & violations["KEY"].notna()
    if via.any():
        violations.loc[via, "TARGET"] = ("association target found, of type "
                                         + violations.loc[via, "VALUE"].astype(str))
    if of_class.any():
        types, names = _type_map(data, table_name), _name_map(data, table_name)

        def describe_reference(ref):
            found = types.get(ref)
            if found is None:
                return f"referenced object {ref} not found in the data"
            name = names.get(ref)
            return f'referenced object {ref} found — {found}' + (f' "{name}"' if name else "")

        violations.loc[of_class, "TARGET"] = (
            violations.loc[of_class, "VALUE"].astype(str).map(describe_reference))
    if over_count.any():
        violations.loc[over_count, "TARGET"] = _found_values(
            violations.loc[over_count, ["ID", "KEY"]], data, table_name)
    return violations


def _name_map(data, table_name="triplets"):
    """{ID: IdentifiedObject.name} from the data (any input flavor)."""
    kind = flavor(data)
    if kind == "duckdb":
        return dict(data.execute(
            f"SELECT ID, VALUE FROM {table_name} "
            f"WHERE KEY = 'IdentifiedObject.name'").fetchall())
    if kind == "pyarrow":
        data = data.to_pandas(types_mapper=pandas.ArrowDtype)
        kind = "pandas"
    if kind == "polars":
        rows = data.filter(data["KEY"] == "IdentifiedObject.name")
        return dict(zip(rows["ID"].to_list(), rows["VALUE"].to_list()))
    rows = data.loc[data["KEY"] == "IdentifiedObject.name"]
    return dict(zip(rows["ID"].astype(str), rows["VALUE"]))


def _found_values(pairs, data, table_name="triplets", cap=5):
    """Per violated (ID, KEY): "found N values: 'a', 'b', …" — the validator
    sees WHICH duplicates to remove without opening the instance data."""
    kind = flavor(data)
    keys = list(pairs["KEY"].astype(str).unique())
    if kind == "duckdb":
        placeholders = ", ".join("?" for _ in keys)
        rows = pandas.DataFrame(data.execute(
            f"SELECT ID, KEY, VALUE FROM {table_name} WHERE KEY IN ({placeholders})",
            keys).fetchall(), columns=["ID", "KEY", "VALUE"])
    else:
        if kind == "pyarrow":
            data = data.to_pandas(types_mapper=pandas.ArrowDtype)
            kind = "pandas"
        if kind == "polars":
            rows = data.filter(data["KEY"].is_in(keys)).to_pandas()
        else:
            rows = data.loc[data["KEY"].isin(keys)]
    rows = rows.astype({"ID": str, "KEY": str})

    def describe(values):
        quoted = [f"'{value}'" for value in values[:cap]]
        more = f", … ({len(values)} total)" if len(values) > cap else ""
        return f"found {len(values)} values: {', '.join(quoted)}{more}"

    grouped = (rows.groupby(["ID", "KEY"], observed=True)["VALUE"]
               .apply(lambda values: describe(list(values))))
    return pandas.Series(list(zip(pairs["ID"].astype(str), pairs["KEY"].astype(str))),
                         index=pairs.index).map(grouped)


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


_HEADER_KEYS = ("Model.messageType", "keyword", "Model.profile", "conformsTo")


def validate_schema(data, rdf_map, engine="auto", closed=False, profiles=None, **kwargs):
    """Validate triplet data against the export schema — per instance, per
    declared profile; profiles are never merged.

    Every INSTANCE_ID is validated separately (the scope filter), against
    each profile its own header declares: the header's profile-identity
    fields (``conformsTo``, ``Model.profile``, ``keyword``,
    ``Model.messageType``) are matched against the schema profiles' declared
    identity (versionIRI / conformsTo / keyword / section key; legacy 2.4
    profile URLs by substring). One instance may declare several profiles —
    each runs on its own, so per-profile constraints (e.g. mRID 1..1 in
    every CGMES 3.0 profile) are checked against that instance's rows alone.

    profiles : sequence of profile identifiers, optional
        Explicit override applied to EVERY instance (section key, keyword or
        profile URI) — for header-less or legacy data. Unknown identifiers
        raise ValueError. Default None = resolve from each instance's header;
        instances resolving to nothing are skipped and reported in the run
        metadata coverage (``skipped_shapes``).
    """
    import triplets

    started = datetime.now(timezone.utc)
    compiled_set = compile_schema(rdf_map, closed=closed)
    table_name = _table_ref(data, **kwargs)
    engine_name, engine_mod = get_engine(engine)
    lexical = kwargs.pop("lexical", True)
    context = kwargs.pop("context", False)

    chosen = None
    if profiles is not None:
        chosen = []
        for identifier in profiles:
            section = compiled_set.section(identifier)
            if section is None:
                raise ValueError(f"unknown schema profile {identifier!r}; available: "
                                 f"{', '.join(sorted(compiled_set.profiles))}")
            if section not in chosen:
                chosen.append(section)

    unresolved, runs = [], []
    for instance, hints in _instance_hints(data, table_name).items():
        sections = chosen if chosen is not None else _match_profiles(hints, compiled_set)
        if not sections:
            unresolved.append(f"instance {instance}: no schema profile matched "
                              f"(hints: {hints[:4]})")
            continue
        runs.extend((instance, section) for section in sections)

    # one raw engine pass per (instance, profile) — the scope filter keeps
    # counting per instance; describe/expected/enrich run ONCE on the concat
    frames = []
    for instance, section in runs:
        sub = _run(data, compiled_set.profiles[section], engine_name, engine_mod,
                   rdf_map, [instance], lexical, **kwargs)
        sub["PROFILE"] = section
        frames.append(sub)

    used = sorted({section for _, section in runs})
    violations = (pandas.concat(frames, ignore_index=True) if frames
                  else pandas.DataFrame(columns=VIOLATION_COLUMNS + ["PROFILE"]))
    ir = (pandas.concat([compiled_set.profiles[section].ir for section in used],
                        ignore_index=True) if used
          else pandas.DataFrame(columns=IR_COLUMNS))
    violations = _present(violations, data, ir, "rdfs", table_name)
    if context:
        union = CompiledShapes(graph=None, ir=ir, hash=compiled_set.hash,
                               sources=compiled_set.sources, language="rdfs")
        violations = enrich(violations, data=data, shapes=union, rdf_map=rdf_map)
    skipped = sorted(
        {f"{section}: {entry}" for section in used
         for entry in compiled_set.profiles[section].stats["skipped_shapes"]}
        | set(unresolved))
    skipped_components = sorted({
        component for section in used for component in
        compiled_set.profiles[section].plans.get(engine_name, ((), (), ()))[2]})
    finished = datetime.now(timezone.utc)
    violations.attrs["validation"] = {
        "started_at": _iso(started),
        "generated_at": _iso(finished),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "engine": engine_name,
        "creator": f"triplets {triplets.__version__}",
        "source": _source_labels(data, table_name=table_name),
        "references": list(compiled_set.sources),
        "language": "rdfs",
        "profiles": used,
        "node_shapes": sum(compiled_set.profiles[s].stats["node_shapes"] for s in used),
        "constraints": sum(compiled_set.profiles[s].stats["constraints"] for s in used),
        "skipped_shapes": skipped,
        "skipped_components": skipped_components,
        "source_shapes": {},
    }
    return violations


def _match_profiles(hints, compiled_set):
    """Sections a header's hints resolve to, in hint-priority order — exact
    identity first, the legacy 2.4 profile-URL substrings as fallback."""
    sections = []
    for hint in hints:
        section = compiled_set.section(hint)
        if section and section not in sections:
            sections.append(section)
    if not sections:
        from ..export.cimxml_utils import PROFILE_URL_MAP
        for hint in hints:
            for url_part, section in PROFILE_URL_MAP.items():
                if url_part in hint and section in compiled_set.profiles \
                        and section not in sections:
                    sections.append(section)
    return sections


def _instance_hints(data, table_name="triplets"):
    """{INSTANCE_ID: [header profile hints, priority-ordered]} — every
    instance appears, hint-less ones with an empty list (any input flavor)."""
    kind = flavor(data)
    if kind == "duckdb":
        placeholders = ", ".join("?" for _ in _HEADER_KEYS)
        header = pandas.DataFrame(data.execute(
            f"SELECT INSTANCE_ID, KEY, VALUE FROM {table_name} WHERE KEY IN ({placeholders})",
            list(_HEADER_KEYS)).fetchall(), columns=["INSTANCE_ID", "KEY", "VALUE"])
        instances = [row[0] for row in data.execute(
            f"SELECT DISTINCT INSTANCE_ID FROM {table_name}").fetchall()]
    else:
        if kind == "pyarrow":
            data = data.to_pandas(types_mapper=pandas.ArrowDtype)
            kind = "pandas"
        if kind == "polars":
            header = data.filter(data["KEY"].is_in(list(_HEADER_KEYS))).to_pandas()
            instances = data["INSTANCE_ID"].unique().to_list()
        else:
            header = data.loc[data["KEY"].isin(_HEADER_KEYS)]
            instances = data["INSTANCE_ID"].unique().tolist()

    hints = {str(instance): [] for instance in instances}
    priority = {key: rank for rank, key in enumerate(_HEADER_KEYS)}
    header = header.sort_values("KEY", key=lambda keys: keys.map(priority), kind="stable")
    for instance, value in zip(header["INSTANCE_ID"].astype(str), header["VALUE"]):
        if value is not None and not pandas.isna(value):
            hints[instance].append(str(value))
    return hints


def _report_metadata(violations, data, compiled, engine_name, started, table_name="triplets"):
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
        "language": compiled.language,
        "skipped_shapes": skipped_shapes,
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
