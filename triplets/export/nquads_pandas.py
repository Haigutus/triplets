"""N-Quads export using pandas — schema-aware value classification."""

from io import BytesIO

import pandas

from .nquads_utils import (
    make_subject, make_predicate, make_object, make_graph,
    build_key_metadata,
)


def export_to_nquads(data, path=None, rdf_map=None, export_to_memory=False):
    """Export triplet DataFrame to N-Quads file.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    path : str, optional
        Output file path (.nq). Ignored when export_to_memory=True.
    rdf_map : dict or str, optional
        Export schema for proper enum/association detection and literal
        datatype annotations ("400"^^<...XMLSchema#float>). If None,
        enumerations won't get namespace and literals stay untyped.
    export_to_memory : bool, default False
        If True, return an in-memory BytesIO (with .name) instead of writing to disk.
    """
    enum_keys, key_namespaces, key_datatypes = build_key_metadata(rdf_map) if rdf_map else (set(), {}, {})

    data = data[data["VALUE"].notna()]  # no object to state (parity with the polars engine)

    id_col = data["ID"].astype(str)
    key_col = data["KEY"].astype(str)
    val_col = data["VALUE"].astype(str)
    inst_col = data["INSTANCE_ID"].astype(str)

    subjects = id_col.apply(make_subject)
    predicates = key_col.apply(lambda k: make_predicate(k, key_namespaces))
    objects = pandas.Series(
        [make_object(k, v, enum_keys, key_datatypes) for k, v in zip(key_col, val_col)],
        index=data.index,
    )
    graphs = inst_col.apply(make_graph)

    quads = subjects + " " + predicates + " " + objects + " " + graphs + " ."
    content = "\n".join(quads.values) + "\n"

    if export_to_memory:
        buffer = BytesIO(content.encode("utf-8"))
        buffer.name = "export.nq"
        return buffer

    with open(path, "w") as f:
        f.write(content)
