"""Optional context enrichment for violations frames.

The slower opt-in pass (``validate(..., context=True)`` or a standalone
``enrich`` call) that adds human/schema context columns to the canonical
violations frame: which instance/file the object came from, what the object
is, what the shape says about itself, and what the export schema defines for
the class and attribute. Every source is optional — absent sources leave
their columns null, so the output schema is stable.

All lookups are vectorized maps built once from the sources — cost is
O(data), not O(violations × data).
"""
import logging

import pandas

from ..export.nquads_utils import flatten_schema
from .._header import profile_sections
from ..iri import TYPE_KEY, load_rdf_map
from .shacl_ir import CompiledShapes, compile_shapes

logger = logging.getLogger(__name__)

ENRICHMENT_COLUMNS = [
    "INSTANCE_ID", "INSTANCE_LABEL",              # data: which instance / file
    "OBJECT_TYPE", "OBJECT_NAME",                 # data: what the object is
    "SHAPE_NAME", "SHAPE_DESCRIPTION",            # shapes IR: sh:name / sh:description
    "SCHEMA_DESCRIPTION", "SCHEMA_MULTIPLICITY",  # rdf_map: the KEY's property entry
    "CLASS_DESCRIPTION",                          # rdf_map: the OBJECT_TYPE class entry
]


def enrich(violations, data=None, shapes=None, rdf_map=None):
    """Return a copy of the violations frame with ENRICHMENT_COLUMNS added.

    Parameters
    ----------
    violations : DataFrame in VIOLATION_COLUMNS (any engine's output).
    data : triplet DataFrame, optional
        Source data — fills INSTANCE_ID / INSTANCE_LABEL (the parsed file
        name) / OBJECT_TYPE / OBJECT_NAME. An object present in several
        instances is attributed to its first occurrence.
    shapes : CompiledShapes or shape source, optional
        Fills SHAPE_NAME / SHAPE_DESCRIPTION (sh:name / sh:description of
        the source shape; a property shape without its own inherits the
        parent NodeShape's). Pass the same shapes object the validation ran
        with — anonymous property shapes get fresh blank-node ids per parse,
        so a re-parsed graph cannot be matched to the violations.
    rdf_map : dict or str, optional
        Export schema — fills SCHEMA_DESCRIPTION / SCHEMA_MULTIPLICITY for
        the violation's KEY and CLASS_DESCRIPTION for the object's type, from
        the violation's own profile: the PROFILE column (``validate_schema``),
        else the profile its instance header resolves to (needs *data*). A
        violation with no resolvable profile, or a KEY its profile lacks, falls
        back to the first-wins merged view.
    """
    enriched = violations.copy()
    for column in ENRICHMENT_COLUMNS:
        enriched[column] = pandas.NA

    if data is not None:
        _add_instance_context(enriched, _to_pandas(data))
    if shapes is not None:
        compiled = shapes if isinstance(shapes, CompiledShapes) else compile_shapes(shapes)
        _add_shape_context(enriched, compiled.ir)
    if rdf_map is not None:
        rdf_map = load_rdf_map(rdf_map)
        if "PROFILE" in enriched.columns:
            profiles = enriched["PROFILE"].map(lambda profile: [profile] if isinstance(profile, str) else [])
        else:
            profiles = enriched["INSTANCE_ID"].map(_instance_profiles(data, rdf_map)) if data is not None else None
        _add_schema_context(enriched, rdf_map, profiles)
    return enriched


def _instance_profiles(data, rdf_map):
    """{INSTANCE_ID: [schema sections]} by the header hints, in hint order — the CIM XML
    exporter's rule (an instance may declare several profiles)."""
    from . import _instance_hints
    return {instance: profile_sections(hints, rdf_map)
            for instance, hints in _instance_hints(_to_pandas(data)).items()}


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


def _add_schema_context(violations, rdf_map, profiles=None):
    """Per-profile lookup first — each of the row's profiles in order (profiles differ in
    multiplicity and description) — the first-wins merged view (``flatten_schema``) where a
    row has no profile or none of its profiles has the name."""
    key_info, class_info = flatten_schema(rdf_map)
    columns = (("SCHEMA_DESCRIPTION", "KEY", "description", {k: i.get("description") for k, i in key_info.items()}),
               ("SCHEMA_MULTIPLICITY", "KEY", "multiplicity", {k: i.get("multiplicity") for k, i in key_info.items()}),
               ("CLASS_DESCRIPTION", "OBJECT_TYPE", "description", class_info))
    for column, name_column, field, merged in columns:
        fallback = violations[name_column].map(merged)
        if profiles is None:
            violations[column] = fallback
            continue
        own = [_entry_field(rdf_map, sections if isinstance(sections, list) else [], name, field)
               for sections, name in zip(profiles, violations[name_column])]
        violations[column] = pandas.Series(own, index=violations.index, dtype=object).where(
            lambda series: series.notna(), fallback)


def _entry_field(rdf_map, sections, name, field):
    """*field* of *name* in the first of *sections* that defines it, else None."""
    for profile in sections:
        entry = rdf_map.get(profile, {}).get(name) if isinstance(rdf_map.get(profile), dict) else None
        if isinstance(entry, dict) and entry.get(field) is not None:
            return entry[field]
    return None


def _to_pandas(data):
    from .._engine_detect import to_pandas
    return to_pandas(data)
