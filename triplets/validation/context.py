"""Context enrichment for violations frames, a separate call after validation.

``enrich`` adds human/schema context columns to the canonical violations
frame: what a finding found in the data (TARGET), which instance/file the
object came from, what the object is, what the shape says about itself, and
what the export schema defines for the class and attribute. Every source is
optional — absent sources leave their columns null, so the output schema is
stable. ``concat_violations`` joins the frames of several validation runs.

All lookups are vectorized maps built once from the sources — cost is
O(data), not O(violations × data).
"""
import logging

import pandas

from .._engine_detect import flavor
from ..iri import REFERENCE, TYPE_KEY, load_rdf_map, value_types
from .schema_scope import _merged_entries
from .shacl_ir import CompiledShapes, IR_COLUMNS, compile_shapes
from .locations import member_name
from .shacl_report import VIOLATION_COLUMNS, run_language

logger = logging.getLogger(__name__)

REFERENCED_OBJECT = "referenced object "       # + its id: how the TARGET text of a reference finding starts

ENRICHMENT_COLUMNS = [
    "TARGET",                                     # data: what the finding found (reference, duplicates)
    "TARGET_ID", "TARGET_TYPE",                   # data: the referenced object found (reference findings)
    "INSTANCE_ID", "INSTANCE_LABEL",              # data: which instance / file
    "OBJECT_TYPE", "OBJECT_NAME",                 # data: what the object is
    "SHAPE_NAME", "SHAPE_DESCRIPTION",            # shapes IR: sh:name / sh:description
    "SCHEMA_DESCRIPTION", "SCHEMA_MULTIPLICITY",  # rdf_map: the KEY's property entry
    "CLASS_DESCRIPTION",                          # rdf_map: the OBJECT_TYPE class entry
]


def enrich(violations, data=None, shapes=None, rdf_map=None, rdf_map_scope=None, data_scope=None, **kwargs):
    """Return a copy of the violations frame with ENRICHMENT_COLUMNS added.

    A cell the frame already fills is kept, only empty ones are filled: enriching
    an enriched frame again changes nothing, and a second call with other
    sources adds what the first could not.

    Parameters
    ----------
    violations : DataFrame from validate_shacl / validate_schema / validate_schema_references.
    data : triplet DataFrame, arrow or DuckDB connection, optional
        Source data — fills TARGET (the referenced object found, or not; the
        duplicate values of a maximum-count finding), TARGET_ID / TARGET_TYPE (the
        id and Type of a reference finding's referenced object found in the data), INSTANCE_ID /
        INSTANCE_LABEL (the parsed file name) / OBJECT_TYPE / OBJECT_NAME. A row's
        own INSTANCE_ID (the schema validations name the instance a finding was
        found in) is kept, and its duplicate values are that instance's; a row
        without one (``validate_shacl`` runs over the merged graph) is attributed
        to the first instance holding its triple (ID, KEY, VALUE), else its ID, and
        its duplicate values are those of every instance. OBJECT_TYPE / OBJECT_NAME
        are the row's instance's, else the first instance's stating them. DuckDB: ``table`` / ``schema`` / ``table_name`` kwargs as
        for the duckdb engine.
    shapes : CompiledShapes or shape source, optional
        Fills SHAPE_NAME / SHAPE_DESCRIPTION (sh:name / sh:description of
        the source shape; a property shape without its own inherits the
        parent NodeShape's) and tells TARGET which findings are valueType rows.
        Pass the same shapes object the validation ran with — anonymous
        property shapes get fresh blank-node ids per parse, so a re-parsed
        graph cannot be matched to the violations.
    rdf_map : dict or str, optional
        Export schema — fills SCHEMA_DESCRIPTION / SCHEMA_MULTIPLICITY for
        the violation's KEY and CLASS_DESCRIPTION for the object's type, from
        the profiles the violation was checked against: the PROFILE column
        (the schema validations — a list of section keys), else *rdf_map_scope*,
        else the run's ``attrs["validation"]["profiles"]``, merged like the run.
        A row with none of them reads the root entries; a name its profiles lack
        stays empty — another profile's text is never borrowed.
    rdf_map_scope : profile identifier(s), or {INSTANCE_ID: profile identifier(s)}, optional
        The profiles of rows without a PROFILE (a validate_shacl run): one scope
        for every row, or one per instance.
    data_scope : iterable of INSTANCE_ID, optional
        Read only these instances of *data* — pass the validation's own scope,
        so TARGET finds exactly what the run saw (data outside is not loaded).
    """
    enriched = violations.copy()
    filled = {column: violations[column].astype(object) for column in ENRICHMENT_COLUMNS if column in violations}
    own = filled.get("INSTANCE_ID", pandas.Series(None, index=violations.index, dtype=object))
    for column in ENRICHMENT_COLUMNS:
        enriched[column] = pandas.NA
    enriched["INSTANCE_ID"] = own                # the schema context below reads it
    rdf_map = load_rdf_map(rdf_map) if rdf_map is not None else None
    compiled = None if shapes is None else shapes if isinstance(shapes, CompiledShapes) else compile_shapes(shapes)

    if data is not None:
        from . import _table_ref
        table_name = _table_ref(data, **kwargs)
        if data_scope is not None:
            data, table_name = _scoped(data, data_scope, table_name), "triplets"
        ir = compiled.ir if compiled is not None and compiled.ir is not None else pandas.DataFrame(columns=IR_COLUMNS)
        _add_target(enriched, own, data, ir, value_types(rdf_map), table_name)
        _add_instance_context(enriched, own, _to_pandas(data))
    if compiled is not None:
        _add_shape_context(enriched, compiled.ir)
    if rdf_map is not None:
        _add_schema_context(enriched, rdf_map, _row_scopes(enriched, rdf_map_scope))
    for column, values in filled.items():      # a filled cell is kept: enriching again changes nothing
        enriched[column] = values.where(values.notna(), enriched[column])
    return enriched


def concat_violations(frames):
    """Concatenate violations frames, merging their run metadata (``attrs["validation"]``):
    the earliest start, the latest end and the duration between them; shape and
    constraint counts once per rule set (``compiled_hash`` + ``deactivated_shapes`` —
    the joined runs' rule sets are kept in ``rule_sets``); lists and dicts united;
    other values kept when equal, else the list of distinct values; None is no value.
    A joined frame joins again: the result is the same however the frames are grouped.
    The frames must share one constraint language (ValueError otherwise): the run
    metadata's, and for a frame without metadata the one its rows show
    (``shacl_report.run_language``)."""
    frames = list(frames)
    if not frames:
        return pandas.DataFrame(columns=VIOLATION_COLUMNS)
    # a frame's language is its run metadata's, else what its rows say (run_language);
    # an empty frame without metadata says nothing
    languages = {run_language(frame) for frame in frames if len(frame) or frame.attrs.get("validation")}
    if len(languages) > 1:
        raise ValueError("concat_violations joins runs of one constraint language (shacl or rdfs)")
    violations = pandas.concat(frames, ignore_index=True)
    metas = [frame.attrs["validation"] for frame in frames if frame.attrs.get("validation")]
    if not metas:
        return violations
    merged = {}
    for meta in metas:
        for key, value in meta.items():
            if key not in _DERIVED:
                merged[key] = _united(merged[key], value) if key in merged else _united(value, None)
    timed = [meta for meta in metas if meta.get("started_at")]   # validate_by_header with no instance resolved: no run
    if timed:
        merged["started_at"] = min(meta["started_at"] for meta in timed)
        merged["generated_at"] = max(meta["generated_at"] for meta in timed)
        merged["duration_seconds"] = round((_moment(merged["generated_at"]) - _moment(merged["started_at"]))
                                           .total_seconds(), 3)
    rule_sets = {(rule_set["compiled_hash"], *rule_set["deactivated_shapes"]): rule_set
                 for meta in timed for rule_set in _rule_sets(meta)}
    merged["rule_sets"] = list(rule_sets.values())
    for count in _COUNTS:
        merged[count] = sum(rule_set[count] for rule_set in rule_sets.values())
    violations.attrs["validation"] = merged
    return violations


_COUNTS = ("node_shapes", "constraints")
# keys concat_violations computes from every frame, not value by value
_DERIVED = ("started_at", "generated_at", "duration_seconds", "rule_sets", *_COUNTS)


def _rule_sets(meta):
    """The rule sets a frame's metadata counts: a joined frame's ``rule_sets``, else
    the run's own (``compiled_hash`` + ``deactivated_shapes``) with its counts."""
    return meta.get("rule_sets") or [{"compiled_hash": meta.get("compiled_hash"),
                                      "deactivated_shapes": list(meta.get("deactivated_shapes") or ()),
                                      **{count: meta.get(count, 0) for count in _COUNTS}}]


def _united(old, new):
    """Two values of one metadata key as one, in any grouping: dicts and lists
    united (a single value joins a list as its member), equal values once,
    different ones as the list of both; None is no value. Always a new container."""
    if old is None or new is None:
        kept = new if old is None else old
        return list(kept) if isinstance(kept, list) else dict(kept) if isinstance(kept, dict) else kept
    if isinstance(old, dict) and isinstance(new, dict):
        return {**old, **new}
    if isinstance(old, list) or isinstance(new, list):
        return list(dict.fromkeys(item for value in (old, new) for item in (value if isinstance(value, list) else [value])))
    return old if old == new else [old, new]


def _moment(text):
    from datetime import datetime
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _row_scopes(violations, rdf_map_scope):
    """Per row: the profile identifiers its schema text comes from, or None."""
    def as_scope(scope):
        return None if scope is None else (scope,) if isinstance(scope, str) else tuple(scope)

    if rdf_map_scope is None:
        rdf_map_scope = violations.attrs.get("validation", {}).get("profiles")
    if isinstance(rdf_map_scope, dict):
        given = violations["INSTANCE_ID"].map(lambda instance: as_scope(rdf_map_scope.get(instance)))
    else:
        given = pandas.Series([as_scope(rdf_map_scope)] * len(violations), index=violations.index, dtype=object)
    if "PROFILE" not in violations.columns:
        return given
    own = violations["PROFILE"].map(lambda profile: tuple(profile) if isinstance(profile, (list, tuple)) else None)
    return own.where(own.notna(), given)


def _add_target(violations, own, data, ir, value_kinds, table_name="triplets"):
    """TARGET: what was actually found in the data — the raw MESSAGE stays verbatim
    (exporters emit TARGET as its own [context_message] entry), so the error is
    self-contained:

    - reference checks (``sh:class``, ``triplets:range`` — presented ``rdfs:range`` —
      and ``triplets:missing``; VALUE = the ref): the target's id, Type and name, or
      the fact that it does not exist — a found target's id and Type also in
      TARGET_ID and TARGET_TYPE;
    - valueType rows (``via_type``): the found type (the id is not carried);
    - ``sh:maxCount`` / ``xsd:maxOccurs``: the actual duplicate values, in the row's
      *own* instance when it names one.

    A presented ``rdfs:range`` is a reference when its rule (*ir*) is
    ``triplets:range`` or the schema (*value_kinds*) declares its KEY an Association —
    an enumeration's ``rdfs:range`` is not.
    """
    for column in ("TARGET", "TARGET_ID", "TARGET_TYPE"):
        violations[column] = pandas.Series(None, index=violations.index, dtype=object)
    if violations.empty:
        return
    keys = pandas.Series(list(zip(violations["SOURCE_SHAPE"], violations["KEY"])), index=violations.index)
    via_rules = set(zip(ir.loc[ir["via_type"], "shape_id"], ir.loc[ir["via_type"], "path"])) if len(ir) else set()
    range_rules = set(zip(ir.loc[ir["component"] == "triplets:range", "shape_id"],
                          ir.loc[ir["component"] == "triplets:range", "path"])) if len(ir) else set()
    described = violations["VALUE"].notna()
    via = described & keys.isin(via_rules)
    presented_range = violations["VIOLATION_TYPE"].eq("rdfs:range") & (
        keys.isin(range_rules) | violations["KEY"].map(value_kinds).eq(REFERENCE))
    of_class = described & ~via & (violations["VIOLATION_TYPE"].isin(("sh:class", "triplets:range",
                                                                        "triplets:missing")) | presented_range)
    over_count = violations["VIOLATION_TYPE"].isin(("sh:maxCount", "xsd:maxOccurs")) & violations["KEY"].notna()
    if via.any():
        violations.loc[via, "TARGET"] = ("association target found, of type "
                                         + violations.loc[via, "VALUE"].astype(str))
    if of_class.any():
        types, names = _type_map(data, table_name), _name_map(data, table_name)

        def describe_reference(ref):
            found = types.get(ref)
            if found is None:
                return f"{REFERENCED_OBJECT}{ref} not found in the data"
            name = names.get(ref)
            return f'{REFERENCED_OBJECT}{ref} found — {found}' + (f' "{name}"' if name else "")

        refs = violations.loc[of_class, "VALUE"].astype(str)
        violations.loc[of_class, "TARGET"] = refs.map(describe_reference)
        violations.loc[of_class, "TARGET_ID"] = refs.map(lambda ref: ref if ref in types else None)
        violations.loc[of_class, "TARGET_TYPE"] = refs.map(types.get)
    if over_count.any():
        violations.loc[over_count, "TARGET"] = _found_values(
            violations.loc[over_count, ["ID", "KEY", "VIOLATION_TYPE"]].assign(INSTANCE_ID=own[over_count]),
            data, table_name)


def file_names(data, table_name="triplets"):
    """{INSTANCE_ID: file name} from the data's Distribution meta rows (any input
    flavor): the ``label`` (the parser convention), and for a file read from a
    zip ``<zip file name>/<label>`` (its ``belongsToContainer`` row) — the name
    the locate pass gives the same file (locations.member_name)."""
    keys, columns = ("label", "belongsToContainer"), ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    kind = flavor(data)
    if kind == "duckdb":
        rows = data.execute(f"SELECT {', '.join(columns)} FROM {table_name} "
                            f"WHERE KEY IN ('label', 'belongsToContainer')").fetchall()
    elif kind == "polars":
        rows = data.filter(data["KEY"].is_in(keys)).select(columns).rows()
    else:   # pandas — and pyarrow through its arrow-backed pandas view
        if kind == "pyarrow":
            data = data.to_pandas(types_mapper=pandas.ArrowDtype)
        rows = list(data.loc[data["KEY"].isin(keys), columns].itertuples(index=False, name=None))
    containers = {identifier: value for identifier, key, value, _ in rows if key == "belongsToContainer"}
    names = {}
    for identifier, key, value, instance in rows:
        if key == "label" and value:
            names.setdefault(str(instance), member_name(containers.get(identifier), value))
    return names


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
    sees WHICH duplicates to remove without opening the instance data. The
    values are what the count counted: those of the row's INSTANCE_ID when it
    names one, else those of every instance; for ``sh:maxCount`` each value once
    (a set of value nodes), for ``xsd:maxOccurs`` every element; a row without a
    VALUE is none."""
    kind = flavor(data)
    keys = list(pairs["KEY"].astype(str).unique())
    columns = ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    if kind == "duckdb":
        placeholders = ", ".join("?" for _ in keys)
        rows = pandas.DataFrame(data.execute(
            f"SELECT {', '.join(columns)} FROM {table_name} WHERE KEY IN ({placeholders})",
            keys).fetchall(), columns=columns)
    else:
        if kind == "pyarrow":
            data = data.to_pandas(types_mapper=pandas.ArrowDtype)
            kind = "pandas"
        if kind == "polars":
            rows = data.filter(data["KEY"].is_in(keys)).select(columns).to_pandas()
        else:
            rows = data.loc[data["KEY"].isin(keys), columns]
    rows = rows.loc[rows["VALUE"].notna() & rows["ID"].astype(str).isin(set(pairs["ID"].astype(str)))]
    rows = rows.astype({"ID": str, "KEY": str, "INSTANCE_ID": str})
    every = rows.groupby(["ID", "KEY"], observed=True)["VALUE"].apply(list)
    per_instance = rows.groupby(["ID", "KEY", "INSTANCE_ID"], observed=True)["VALUE"].apply(list)

    def describe(object_id, key, violation_type, instance):
        values = (every.get((object_id, key)) if pandas.isna(instance)
                  else per_instance.get((object_id, key, str(instance))))
        if values is None:
            return None
        values = list(dict.fromkeys(values)) if violation_type == "sh:maxCount" else values
        quoted = [f"'{value}'" for value in values[:cap]]
        more = f", … ({len(values)} total)" if len(values) > cap else ""
        return f"found {len(values)} values: {', '.join(quoted)}{more}"

    return pandas.Series([describe(*row) for row in zip(pairs["ID"].astype(str), pairs["KEY"].astype(str),
                                                        pairs["VIOLATION_TYPE"], pairs["INSTANCE_ID"])],
                         index=pairs.index, dtype=object)


def _type_map(data, table_name="triplets"):
    """{ID: Type} from the data's Type rows (any input flavor)."""
    kind = flavor(data)
    if kind == "duckdb":
        return dict(data.execute(
            f"SELECT ID, VALUE FROM {table_name} WHERE KEY = '{TYPE_KEY}'").fetchall())
    if kind == "pyarrow":
        data = data.to_pandas(types_mapper=pandas.ArrowDtype)
        kind = "pandas"
    if kind == "polars":
        rows = data.filter(data["KEY"] == TYPE_KEY)
        return dict(zip(rows["ID"].to_list(), rows["VALUE"].to_list()))
    rows = data.loc[data["KEY"] == TYPE_KEY]
    return dict(zip(rows["ID"].astype(str), rows["VALUE"]))


def _add_instance_context(violations, own, data):
    """INSTANCE_ID: the row's *own*, else the first instance holding its triple (ID, KEY,
    VALUE), else the first holding its ID; its INSTANCE_LABEL; OBJECT_TYPE and OBJECT_NAME
    as that instance states them, else as the first instance stating them."""
    identifier = data["ID"].astype(str)
    instances = data["INSTANCE_ID"].astype(str)

    first = ~identifier.duplicated()
    attributed = violations["ID"].astype(str).map(pandas.Series(instances[first].values, index=identifier[first]))
    stated = own.isna() & violations["KEY"].notna() & violations["VALUE"].notna()
    if stated.any():                      # the rows of the violated objects only: one small lookup
        rows = data.loc[identifier.isin(set(violations.loc[stated, "ID"].astype(str))),
                        ["ID", "KEY", "VALUE", "INSTANCE_ID"]].astype(str).drop_duplicates(["ID", "KEY", "VALUE"])
        holder = pandas.Series(rows["INSTANCE_ID"].values,
                               index=pandas.MultiIndex.from_frame(rows[["ID", "KEY", "VALUE"]]))
        triples = violations.loc[stated, ["ID", "KEY", "VALUE"]].astype(str)
        found = holder.reindex(pandas.MultiIndex.from_frame(triples)).set_axis(triples.index)
        attributed[stated] = found.where(found.notna(), attributed[stated])
    violations["INSTANCE_ID"] = own.where(own.notna(), attributed)

    # The parsed file name lives on the per-instance Distribution meta object
    violations["INSTANCE_LABEL"] = violations["INSTANCE_ID"].map(file_names(data))

    # the row's own instance's value, else the first instance's
    pairs = pandas.MultiIndex.from_arrays([violations["ID"].astype(str), violations["INSTANCE_ID"].astype(str)])
    for column, key in (("OBJECT_TYPE", TYPE_KEY), ("OBJECT_NAME", "IdentifiedObject.name")):
        rows = data.loc[data["KEY"] == key, ["ID", "VALUE", "INSTANCE_ID"]].astype({"ID": str, "INSTANCE_ID": str})
        first = rows.drop_duplicates("ID")
        own_value = (rows.drop_duplicates(["ID", "INSTANCE_ID"]).set_index(["ID", "INSTANCE_ID"])["VALUE"]
                     .reindex(pairs).set_axis(violations.index))
        violations[column] = own_value.where(own_value.notna(),
                                             violations["ID"].astype(str).map(first.set_index("ID")["VALUE"]))


def _add_shape_context(violations, ir):
    meta = ir.groupby("shape_id", sort=False)[["name", "description"]].first()
    violations["SHAPE_NAME"] = violations["SOURCE_SHAPE"].map(meta["name"])
    violations["SHAPE_DESCRIPTION"] = violations["SOURCE_SHAPE"].map(meta["description"])


def _add_schema_context(violations, rdf_map, scopes):
    """Per-row lookup in the row's merged profiles (``scoped_rdf_map`` — the
    multiplicity checked, relaxed when profiles differ); rows without profiles
    read the root entries."""
    views = {scope: _merged_entries(rdf_map, None if scope is None else list(scope))[1]
             for scope in set(scopes)}
    columns = (("SCHEMA_DESCRIPTION", "KEY", "description"),
               ("SCHEMA_MULTIPLICITY", "KEY", "multiplicity"),
               ("CLASS_DESCRIPTION", "OBJECT_TYPE", "description"))
    for column, name_column, field in columns:
        violations[column] = pandas.Series(
            [(views[scope].get(name) or {}).get(field) if isinstance(name, str) else None
             for scope, name in zip(scopes, violations[name_column])],
            index=violations.index, dtype=object)


def _scoped(data, data_scope, table_name="triplets"):
    """The rows of the *data_scope* instances (None: every instance), as a pandas /
    polars frame."""
    instances = None if data_scope is None else [str(instance) for instance in data_scope]
    kind = flavor(data)
    if kind == "duckdb":
        where = "TRUE" if instances is None else f"INSTANCE_ID IN ({', '.join('?' for _ in instances)})" if instances else "FALSE"
        return data.execute(f"SELECT * FROM {table_name} WHERE {where}", instances or []).df()
    if kind == "pyarrow":
        data = data.to_pandas(types_mapper=pandas.ArrowDtype)
    if instances is None:
        return data
    if kind == "polars":
        return data.filter(data["INSTANCE_ID"].is_in(instances))
    return data[data["INSTANCE_ID"].astype(str).isin(instances)]


def _to_pandas(data):
    from .._engine_detect import to_pandas
    return to_pandas(data)
