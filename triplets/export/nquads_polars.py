"""N-Quads export using polars vectorized string operations.

Converts [ID, KEY, VALUE, INSTANCE_ID] triplet DataFrame to N-Quads format.
Each quad: <subject> <predicate> <object> <graph> .

Polars approach: fully vectorized string concat + write_csv.
~3.2M rows/s, 468 MB/s on RealGrid (1.14M rows).
"""

import polars as pl

CIM_NS = "http://iec.ch/TC57/CIM100#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def export_to_nquads(data, path):
    """Export triplet DataFrame to N-Quads file using polars.

    Parameters
    ----------
    data : polars.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    path : str
        Output file path (.nq).
    """
    is_type = pl.col("KEY") == "Type"

    result = data.with_columns([
        ("<urn:uuid:" + pl.col("ID").cast(pl.Utf8) + ">").alias("S"),
        pl.when(is_type)
            .then(pl.lit(f"<{RDF_TYPE}>"))
            .otherwise("<" + pl.lit(CIM_NS) + pl.col("KEY").cast(pl.Utf8) + ">")
            .alias("P"),
        pl.when(is_type)
            .then("<" + pl.lit(CIM_NS) + pl.col("VALUE").cast(pl.Utf8) + ">")
            .otherwise('"' + pl.col("VALUE").cast(pl.Utf8) + '"')
            .alias("O"),
        ("<urn:uuid:" + pl.col("INSTANCE_ID").cast(pl.Utf8) + ">").alias("G"),
    ]).select([
        (pl.col("S") + " " + pl.col("P") + " " + pl.col("O") + " " + pl.col("G") + " .").alias("quad")
    ])

    result.write_csv(path, include_header=False, quote_style="never")
