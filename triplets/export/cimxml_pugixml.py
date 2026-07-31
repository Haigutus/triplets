# -------------------------------------------------------------------------------
# Name:        export/cimxml_pugixml.py
# Purpose:     CIM XML export engine backed by the compiled cython/pugixml extension
# -------------------------------------------------------------------------------
"""Performance CIM XML export engine.

Same generate_xml() contract as cimxml_pandas, but the XML is built by the
compiled extension (Arrow string arrays → pugixml DOM → bytes) instead of lxml.
~Identical output, much faster on large instances.
"""
import logging

import pandas
import pyarrow

from .cimxml_cython_pugixml import generate_xml_from_arrow
from .cimxml_utils import load_rdf_map, resolve_instance_config
from .._engine_detect import to_arrow

logger = logging.getLogger(__name__)


def _string_like(arrow_type):
    """utf8 / large_utf8, or a dictionary of either — what the extension reads."""
    if pyarrow.types.is_dictionary(arrow_type):
        arrow_type = arrow_type.value_type
    return pyarrow.types.is_string(arrow_type) or pyarrow.types.is_large_string(arrow_type)


def _flat(column):
    """ChunkedArray/Array → one contiguous Array (zero-copy for a single chunk).

    The compiled extension reads the buffers of ONE contiguous array; arrow-backed
    pandas columns arrive as a ChunkedArray (one chunk per parsed file after concat).
    """
    if isinstance(column, pyarrow.ChunkedArray):
        return column.chunk(0) if column.num_chunks == 1 else column.combine_chunks()
    return column


def _pandas_string_column(series):
    """Pandas column → contiguous Arrow string-ish array.

    Already-string columns (arrow-backed string / dictionary-of-string, pandas
    string dtype, categorical of strings) pass through zero-copy and undecoded.
    Everything else — numeric, object, mixed — takes the legacy
    astype("string[pyarrow]") formatting path, so numbers render exactly as the
    lxml engine formats them and nulls stay null.
    """
    dtype = series.dtype
    passthrough = (
        (isinstance(dtype, pandas.ArrowDtype) and _string_like(dtype.pyarrow_dtype))
        or isinstance(dtype, pandas.StringDtype)
        or (isinstance(dtype, pandas.CategoricalDtype)
            and dtype.categories.inferred_type == "string")
    )
    if not passthrough:
        series = series.astype("string[pyarrow]")
    return _flat(pyarrow.Array.from_pandas(series))


def _string_batch(instance_data):
    """ID/KEY/VALUE of any frame flavor as one contiguous string RecordBatch.

    pandas goes per-column (see _pandas_string_column); polars exports
    large_utf8/dictionary zero-copy, with an arrow cast for the rare
    non-string column.
    """
    if isinstance(instance_data, pandas.DataFrame):
        arrays = [_pandas_string_column(instance_data[name]) for name in ("ID", "KEY", "VALUE")]
    else:
        table = to_arrow(instance_data, columns=["ID", "KEY", "VALUE"])
        arrays = [_flat(table.column(name)) for name in ("ID", "KEY", "VALUE")]
        arrays = [a if _string_like(a.type) else a.cast(pyarrow.string()) for a in arrays]
    return pyarrow.RecordBatch.from_arrays(arrays, names=["ID", "KEY", "VALUE"])


def generate_xml(instance_data,
                 rdf_map=None,
                 namespace_map=None,
                 class_KEY="Type",
                 export_undefined=False,
                 comment=None,
                 debug=False,
                 datatypes=False):
    """Generate an RDF XML file from a triplet dataset instance.

    Same parameters and return value as :func:`cimxml_pandas.generate_xml`;
    see there for full documentation.

    Returns
    -------
    dict
        {'filename': str, 'file': bytes (UTF-8 XML)}
    """
    if datatypes:
        raise NotImplementedError(
            "rdf:datatype annotations are not implemented in the cython_pugixml engine yet — "
            "use engine='python_lxml' (engine='auto' picks it automatically when datatypes=True)")
    rdf_map = load_rdf_map(rdf_map)
    file_name, namespace_map, instance_rdf_map = resolve_instance_config(instance_data, rdf_map, namespace_map)

    if instance_rdf_map is None:
        logger.warning("No rdf mapping available for {}".format(file_name))
        if not export_undefined:
            logger.warning("File not created for {}".format(file_name))
            return

    batch = _string_batch(instance_data)

    xml = generate_xml_from_arrow(batch, rdf_map, namespace_map, instance_rdf_map, file_name,
                                  class_KEY=class_KEY, export_undefined=export_undefined, comment=comment)

    logger.info("Exporting RDF to {}".format(file_name))

    return {"filename": file_name, "file": xml}
