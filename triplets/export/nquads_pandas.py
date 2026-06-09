"""N-Quads export using pandas string operations.

Converts [ID, KEY, VALUE, INSTANCE_ID] triplet DataFrame to N-Quads format.
Each quad: <subject> <predicate> <object> <graph> .

Pandas approach: vectorized string concat (no iterrows).
"""

import pandas

CIM_NS = "http://iec.ch/TC57/CIM100#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def export_to_nquads(data, path):
    """Export triplet DataFrame to N-Quads file using pandas.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    path : str
        Output file path (.nq).
    """
    id_col = data["ID"].astype(str)
    key_col = data["KEY"].astype(str)
    val_col = data["VALUE"].astype(str)
    inst_col = data["INSTANCE_ID"].astype(str)

    is_type = key_col == "Type"

    s = "<urn:uuid:" + id_col + ">"

    p = pandas.Series(f"<{CIM_NS}" + key_col + ">", index=data.index)
    p[is_type] = f"<{RDF_TYPE}>"

    o = '"' + val_col + '"'
    o[is_type] = f"<{CIM_NS}" + val_col[is_type] + ">"

    g = "<urn:uuid:" + inst_col + ">"

    quads = s + " " + p + " " + o + " " + g + " ."

    with open(path, "w") as f:
        f.write("\n".join(quads.values) + "\n")
