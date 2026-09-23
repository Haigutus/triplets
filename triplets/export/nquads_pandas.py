"""N-Quads export using pandas — vectorized over the ``triplets.iri`` pandas flavor."""

from io import BytesIO

import pyarrow
import pyarrow.compute

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
    instances = data["INSTANCE_ID"].astype(str).where(data["INSTANCE_ID"].notna(), None)

    kind, payload = iri_pandas.absolute_value(keys, values, terms)
    is_iri = (kind == "iri").to_numpy()
    typed = payload.notna().to_numpy() & ~is_iri
    objects = '"' + _escape(values) + '"'                     # plain literal by default
    objects[typed] = objects[typed] + "^^<" + payload[typed] + ">"
    objects[is_iri] = "<" + payload[is_iri] + ">"

    graphs = ("<" + iri_pandas.absolute_id(instances) + ">").where(instances.notna(), ".")
    content = _lines("<" + iri_pandas.absolute_id(ids) + ">",
                     "<" + iri_pandas.absolute_key(keys, terms) + ">",
                     objects,
                     graphs)

    if export_to_memory:
        buffer = BytesIO(content)
        buffer.name = "export.nq"
        return buffer

    with open(path, "wb") as f:
        f.write(content)


def _lines(subjects, predicates, objects, graphs):
    """Term columns → ``s p o g .\n`` lines as UTF-8 bytes, joined in Arrow
    (one C++ pass; ~4x faster than str concat + ``"\n".join``). A row with no
    graph carries ``.`` in *graphs* and comes out as an N-Triples line."""
    arrays = [pyarrow.array(column, type=pyarrow.string()) for column in (subjects, predicates, objects, graphs)]
    arrays = [array.combine_chunks() if isinstance(array, pyarrow.ChunkedArray) else array
              for array in arrays]
    quads = pyarrow.compute.binary_join_element_wise(*arrays, " ")
    quads = pyarrow.compute.if_else(pyarrow.compute.ends_with(quads, " ."), quads,
                                    pyarrow.compute.binary_join_element_wise(quads, ".", " "))
    offsets = pyarrow.array([0, len(quads)], type=pyarrow.int32())
    joined = pyarrow.compute.binary_join(pyarrow.ListArray.from_arrays(offsets, quads), "\n")
    return joined[0].as_buffer().to_pybytes() + b"\n"
