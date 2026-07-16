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
        the violation's KEY and CLASS_DESCRIPTION for the object's type.
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
        _add_schema_context(enriched, *flatten_schema(rdf_map))
    return enriched


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

    for column, key in (("OBJECT_TYPE", "Type"), ("OBJECT_NAME", "IdentifiedObject.name")):
        rows = data[data["KEY"] == key]
        rows = rows[~rows["ID"].astype(str).duplicated()]
        violations[column] = violations["ID"].map(
            pandas.Series(rows["VALUE"].values, index=rows["ID"].astype(str)))


def _add_shape_context(violations, ir):
    meta = ir.groupby("shape_id", sort=False)[["name", "description"]].first()
    violations["SHAPE_NAME"] = violations["SOURCE_SHAPE"].map(meta["name"])
    violations["SHAPE_DESCRIPTION"] = violations["SOURCE_SHAPE"].map(meta["description"])


def _add_schema_context(violations, key_info, class_info):
    violations["SCHEMA_DESCRIPTION"] = violations["KEY"].map(
        {key: info.get("description") for key, info in key_info.items()})
    violations["SCHEMA_MULTIPLICITY"] = violations["KEY"].map(
        {key: info.get("multiplicity") for key, info in key_info.items()})
    violations["CLASS_DESCRIPTION"] = violations["OBJECT_TYPE"].map(class_info)


def _to_pandas(data):
    from .shacl_pandas import _to_pandas as convert
    return convert(data)
