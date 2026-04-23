# -------------------------------------------------------------------------------
# Name:        RDF parser - Rust + Arrow variant
# Purpose:     Uses Rust (quick-xml + arrow-rs) for maximum performance.
#              XML parsing, data extraction, and Arrow table construction
#              all happen in Rust. Zero-copy to Polars/pandas.
#
# Based on:    rdf_parser.py by kristjan.vilgo
# Variant:     Rust + Arrow (benchmark variant)
# -------------------------------------------------------------------------------
import os
from io import BytesIO
import zipfile

import rdf_parser_rust
import pandas
import polars as pl


def find_all_xml(list_of_paths_to_zip_globalzip_xml, debug=False):
    """Resolve all XML file paths from a list of paths/zips.

    For direct XML files, returns absolute paths (Rust reads them directly).
    For ZIPs, the Rust side handles extraction internally.
    """
    resolved = []

    if type(list_of_paths_to_zip_globalzip_xml) != list:
        list_of_paths_to_zip_globalzip_xml = [list_of_paths_to_zip_globalzip_xml]

    for item in list_of_paths_to_zip_globalzip_xml:
        if isinstance(item, str):
            resolved.append(os.path.abspath(item))
        elif hasattr(item, 'name'):
            resolved.append(os.path.abspath(item.name))

    return resolved


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None):
    """Parse RDF XMLs using Rust and return pandas DataFrame via Arrow zero-copy."""
    paths = find_all_xml(list_of_paths_to_zip_globalzip_xml)
    parallel = max_workers is None or max_workers > 1
    batch = rdf_parser_rust.load_rdf_to_arrow(paths, parallel=parallel)

    import pyarrow as pa
    pa_batch = pa.record_batch(batch)
    return pa_batch.to_pandas()


def load_all_to_polars(list_of_paths_to_zip_globalzip_xml, debug=False, max_workers=None):
    """Parse RDF XMLs using Rust and return Polars DataFrame via Arrow zero-copy."""
    paths = find_all_xml(list_of_paths_to_zip_globalzip_xml)
    parallel = max_workers is None or max_workers > 1
    batch = rdf_parser_rust.load_rdf_to_arrow(paths, parallel=parallel)

    import pyarrow as pa
    pa_batch = pa.record_batch(batch)
    return pl.from_arrow(pa_batch)
