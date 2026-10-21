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
from ..iri import TYPE_KEY, load_rdf_map, value_types
from .schema_scope import _merged_entries
from .shacl_ir import CompiledShapes, IR_COLUMNS, compile_shapes

logger = logging.getLogger(__name__)

ENRICHMENT_COLUMNS = [
    "TARGET",                                     # data: what the finding found (reference, duplicates)
    "INSTANCE_ID", "INSTANCE_LABEL",              # data: which instance / file
    "OBJECT_TYPE", "OBJECT_NAME",                 # data: what the object is
    "SHAPE_NAME", "SHAPE_DESCRIPTION",            # shapes IR: sh:name / sh:description
    "SCHEMA_DESCRIPTION", "SCHEMA_MULTIPLICITY",  # rdf_map: the KEY's property entry
    "CLASS_DESCRIPTION",                          # rdf_map: the OBJECT_TYPE class entry
]


def enrich(violations, data=None, shapes=None, rdf_map=None, rdf_map_scope=None, **kwargs):
    """Return a copy of the violations frame with ENRICHMENT_COLUMNS added.

    Parameters
    ----------
    violations : DataFrame from validate_shacl / validate_schema / validate_schema_references.
    data : triplet DataFrame, arrow or DuckDB connection, optional
        Source data — fills TARGET (the referenced object found, or not; the
        duplicate values of a maximum-count finding), INSTANCE_ID /
        INSTANCE_LABEL (the parsed file name) / OBJECT_TYPE / OBJECT_NAME. An
        object present in several instances is attributed to its first
        occurrence. DuckDB: ``table`` / ``schema`` / ``table_name`` kwargs as
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
    """
    enriched = violations.copy()
    for column in ENRICHMENT_COLUMNS:
        enriched[column] = pandas.NA
    rdf_map = load_rdf_map(rdf_map) if rdf_map is not None else None
    compiled = None if shapes is None else shapes if isinstance(shapes, CompiledShapes) else compile_shapes(shapes)

    if data is not None:
        from . import _table_ref
        ir = compiled.ir if compiled is not None and compiled.ir is not None else pandas.DataFrame(columns=IR_COLUMNS)
        _add_target(enriched, data, ir, value_types(rdf_map), _table_ref(data, **kwargs))
        _add_instance_context(enriched, _to_pandas(data))
    if compiled is not None:
        _add_shape_context(enriched, compiled.ir)
    if rdf_map is not None:
        _add_schema_context(enriched, rdf_map, _row_scopes(enriched, rdf_map_scope))
    return enriched


def concat_violations(frames):
    """Concatenate violations frames, merging their run metadata (``attrs["validation"]``):
    the earliest start, the latest end and the duration between them; shape and
    constraint counts once per distinct run (language, profiles, references); lists
    and dicts united; other values kept when equal, else the list of distinct
    values. The runs must share one constraint language (ValueError otherwise)."""
    frames = list(frames)
    if not frames:
        return pandas.DataFrame()
    violations = pandas.concat(frames, ignore_index=True)
    metas = [frame.attrs["validation"] for frame in frames if frame.attrs.get("validation")]
    if not metas:
        return violations
    if len({meta.get("language") for meta in metas}) > 1:
        raise ValueError("concat_violations joins runs of one constraint language (shacl or rdfs)")
    merged = {}
    for meta in metas:
        for key, value in meta.items():
            if key not in merged:
                merged[key] = list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value
            elif isinstance(value, list) and isinstance(merged[key], list):
                merged[key] = list(dict.fromkeys([*merged[key], *value]))
            elif isinstance(value, dict) and isinstance(merged[key], dict):
                merged[key] = {**merged[key], **value}
            elif key not in ("started_at", "generated_at", "duration_seconds"):
                distinct = merged.setdefault("_distinct", {}).setdefault(key, [merged[key]])
                if value not in distinct:
                    distinct.append(value)
    for key, values in merged.pop("_distinct", {}).items():
        merged[key] = values[0] if len(values) == 1 else values
    merged["started_at"] = min(meta["started_at"] for meta in metas)
    merged["generated_at"] = max(meta["generated_at"] for meta in metas)
    merged["duration_seconds"] = round((_moment(merged["generated_at"]) - _moment(merged["started_at"]))
                                       .total_seconds(), 3)
    runs = {(meta.get("language"), str(meta.get("profiles")), tuple(meta.get("references", ()))):
            (meta.get("node_shapes", 0), meta.get("constraints", 0)) for meta in metas}
    merged["node_shapes"] = sum(counts[0] for counts in runs.values())
    merged["constraints"] = sum(counts[1] for counts in runs.values())
    violations.attrs["validation"] = merged
    return violations


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


def _add_target(violations, data, ir, value_kinds, table_name="triplets"):
    """TARGET: what was actually found in the data — the raw MESSAGE stays verbatim
    (exporters emit TARGET as its own [context_message] entry), so the error is
    self-contained:

    - reference checks (``sh:class``, ``triplets:range`` — presented ``rdfs:range`` —
      and ``triplets:missing``; VALUE = the ref): the target's id, Type and name, or
      the fact that it does not exist;
    - valueType rows (``via_type``): the found type (the id is not carried);
    - ``sh:maxCount`` / ``xsd:maxOccurs``: the actual duplicate values.

    A presented ``rdfs:range`` is a reference when its rule (*ir*) is
    ``triplets:range`` or the schema (*value_kinds*) declares its KEY an Association —
    an enumeration's ``rdfs:range`` is not.
    """
    violations["TARGET"] = pandas.Series(None, index=violations.index, dtype=object)
    if violations.empty:
        return
    keys = pandas.Series(list(zip(violations["SOURCE_SHAPE"], violations["KEY"])), index=violations.index)
    via_rules = set(zip(ir.loc[ir["via_type"], "shape_id"], ir.loc[ir["via_type"], "path"])) if len(ir) else set()
    range_rules = set(zip(ir.loc[ir["component"] == "triplets:range", "shape_id"],
                          ir.loc[ir["component"] == "triplets:range", "path"])) if len(ir) else set()
    described = violations["VALUE"].notna()
    via = described & keys.isin(via_rules)
    presented_range = violations["VIOLATION_TYPE"].eq("rdfs:range") & (
        keys.isin(range_rules) | violations["KEY"].map(value_kinds).eq("reference"))
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
                return f"referenced object {ref} not found in the data"
            name = names.get(ref)
            return f'referenced object {ref} found — {found}' + (f' "{name}"' if name else "")

        violations.loc[of_class, "TARGET"] = (
            violations.loc[of_class, "VALUE"].astype(str).map(describe_reference))
    if over_count.any():
        violations.loc[over_count, "TARGET"] = _found_values(
            violations.loc[over_count, ["ID", "KEY"]], data, table_name)


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


def _add_instance_context(violations, data):
    identifier = data["ID"].astype(str)
    instances = data["INSTANCE_ID"].astype(str)

    first = ~identifier.duplicated()
    violations["INSTANCE_ID"] = violations["ID"].map(
        pandas.Series(instances[first].values, index=identifier[first]))

    # The parsed file name lives on the per-instance Distribution meta object
    # (KEY="label" — the parser convention). TODO: exact code locations
    # (line/region inside the source XML) would extend this path once the
    # parser records them.
    labels = data[data["KEY"] == "label"]
    violations["INSTANCE_LABEL"] = violations["INSTANCE_ID"].map(
        pandas.Series(labels["VALUE"].values, index=labels["INSTANCE_ID"].astype(str))
        .groupby(level=0).first())

    for column, key in (("OBJECT_TYPE", TYPE_KEY), ("OBJECT_NAME", "IdentifiedObject.name")):
        rows = data[data["KEY"] == key]
        rows = rows[~rows["ID"].astype(str).duplicated()]
        violations[column] = violations["ID"].map(
            pandas.Series(rows["VALUE"].values, index=rows["ID"].astype(str)))


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


def _to_pandas(data):
    from .._engine_detect import to_pandas
    return to_pandas(data)
