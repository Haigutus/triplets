"""N-Quads export using pandas — vectorized schema-aware value classification."""

import logging

import numpy
import pandas

from .nquads_utils import CIM_NS, RDF_TYPE, UUID_RE, build_key_metadata

logger = logging.getLogger(__name__)

URI_PREFIXES = ("http://", "https://", "urn:")


def export_to_nquads(data, path, rdf_map=None):
    """Export triplet DataFrame to N-Quads file.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    path : str
        Output file path (.nq).
    rdf_map : dict or str, optional
        Export schema for proper enum/association detection and literal
        datatype annotations ("400"^^<...XMLSchema#float>). If None,
        enumerations won't get namespace and literals stay untyped.
    """
    enum_keys, key_namespaces, key_datatypes = build_key_metadata(rdf_map) if rdf_map else (set(), {}, {})

    null_values = data["VALUE"].isna()
    if null_values.any():
        logger.debug("Skipping %d rows with null VALUE (no object to state)", int(null_values.sum()))
        data = data[~null_values]

    ids = data["ID"].astype(str)
    keys = data["KEY"].astype(str)
    vals = data["VALUE"].astype(str)
    insts = data["INSTANCE_ID"].astype(str)

    # ── subjects / graphs: <urn:uuid:x> unless already a URI ────────────────
    subjects = numpy.where(ids.str.startswith(URI_PREFIXES), "<" + ids + ">", "<urn:uuid:" + ids + ">")
    graphs = numpy.where(insts.str.startswith(URI_PREFIXES), "<" + insts + ">", "<urn:uuid:" + insts + ">")

    # ── predicates: Type → rdf:type; URI keys pass through; else namespace ──
    namespaces = keys.map(key_namespaces).fillna(CIM_NS) if key_namespaces else CIM_NS
    predicates = numpy.select(
        [keys == "Type", keys.str.startswith(("http://", "https://"))],
        ["<" + RDF_TYPE + ">", "<" + keys + ">"],
        default="<" + namespaces + keys + ">",
    )

    # ── objects: same priority chain as the row-wise make_object had ────────
    escaped = (vals.str.replace("\\", "\\\\", regex=False)
                   .str.replace('"', '\\"', regex=False)
                   .str.replace("\n", "\\n", regex=False))
    datatypes = keys.map(key_datatypes) if key_datatypes else pandas.Series(pandas.NA, index=keys.index)

    is_type = keys == "Type"
    val_is_uri = vals.str.startswith(URI_PREFIXES)
    is_enum = keys.isin(list(enum_keys)) if enum_keys else numpy.zeros(len(keys), dtype=bool)
    is_literal_by_schema = keys.isin(list(key_datatypes)) if key_datatypes else numpy.zeros(len(keys), dtype=bool)
    val_is_uuid = vals.str.match(UUID_RE.pattern)

    objects = numpy.select(
        [
            is_type & val_is_uri,
            is_type,
            val_is_uri,
            is_enum,
            is_literal_by_schema & datatypes.notna(),
            is_literal_by_schema,                       # xsd:string — plain literal
            val_is_uuid,
        ],
        [
            "<" + vals + ">",
            "<" + CIM_NS + vals + ">",
            "<" + vals + ">",
            "<" + CIM_NS + vals + ">",
            '"' + escaped + '"^^<' + datatypes.astype(str) + ">",
            '"' + escaped + '"',
            "<urn:uuid:" + vals + ">",
        ],
        default='"' + escaped + '"',
    )

    quads = subjects + " " + predicates + " " + objects + " " + graphs + " ."

    with open(path, "w") as f:
        f.write("\n".join(quads) + "\n")
