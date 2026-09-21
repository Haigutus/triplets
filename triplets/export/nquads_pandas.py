"""N-Quads export using pandas — vectorized over the ``triplets.iri`` pandas flavor."""

from io import BytesIO

from ..iri import SchemaTerms, iri_pandas


def _escape(series):
    return (series.str.replace("\\", "\\\\", regex=False)
            .str.replace('"', '\\"', regex=False)
            .str.replace("\n", "\\n", regex=False)
            .str.replace("\r", "\\r", regex=False))


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
    terms = SchemaTerms.from_rdf_map(rdf_map)

    data = data[data["VALUE"].notna()]  # no object to state (parity with the polars engine)

    ids = data["ID"].astype(str)
    keys = data["KEY"].astype(str)
    values = data["VALUE"].astype(str)
    instances = data["INSTANCE_ID"].astype(str)

    subjects = "<" + iri_pandas.expand_id(ids) + ">"
    predicates = "<" + iri_pandas.expand_key(keys, terms) + ">"
    kind, payload = iri_pandas.expand_value(keys, values, terms)
    payload = payload.fillna("")
    literal = '"' + _escape(values) + '"'
    objects = ("<" + payload + ">").where(kind == "iri",
                                          literal.where(payload == "", literal + "^^<" + payload + ">"))
    graphs = "<" + iri_pandas.expand_id(instances) + ">"

    quads = subjects + " " + predicates + " " + objects + " " + graphs + " ."
    content = "\n".join(quads.values) + "\n"

    if export_to_memory:
        buffer = BytesIO(content.encode("utf-8"))
        buffer.name = "export.nq"
        return buffer

    with open(path, "w") as f:
        f.write(content)
