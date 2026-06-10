"""N-Quads export using polars — schema-aware value classification.

Uses polars vectorized ops where possible, falls back to row-wise for
complex value classification (enum/UUID/literal detection).
"""

import polars as pl

from .nquads_utils import (
    make_subject, make_predicate, make_object, make_graph,
    build_key_metadata,
)


def export_to_nquads(data, path, rdf_map=None):
    """Export triplet DataFrame to N-Quads file.

    Parameters
    ----------
    data : polars.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    path : str
        Output file path (.nq).
    rdf_map : dict or str, optional
        Export schema for proper enum/association detection.
    """
    enum_keys, key_namespaces = build_key_metadata(rdf_map) if rdf_map else (set(), {})

    # Build quads row by row (complex classification can't be fully vectorized)
    quads = []
    for row in data.iter_rows(named=True):
        s = make_subject(str(row["ID"]))
        p = make_predicate(str(row["KEY"]), key_namespaces)
        o = make_object(str(row["KEY"]), str(row["VALUE"]), enum_keys)
        g = make_graph(str(row["INSTANCE_ID"]))
        quads.append(f"{s} {p} {o} {g} .")

    with open(path, "w") as f:
        f.write("\n".join(quads) + "\n")
