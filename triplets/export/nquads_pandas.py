"""N-Quads export using pandas — schema-aware value classification."""

import pandas

from .nquads_utils import (
    make_subject, make_predicate, make_object, make_graph,
    build_key_metadata,
)


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

    with open(path, "w") as f:
        f.write("\n".join(quads.values) + "\n")
