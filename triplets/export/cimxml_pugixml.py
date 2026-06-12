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

import pyarrow

from .cimxml_cython_pugixml import generate_xml_from_arrow
from .cimxml_utils import load_rdf_map, resolve_instance_config

logger = logging.getLogger(__name__)


def _string_array(series):
    """Pandas column → flat Arrow string array (32-bit offsets, as the extension expects).

    series is always pandas here: the export_to_cimxml orchestrator groups with
    pandas groupby before calling the engine. astype("string[pyarrow]") accepts
    any input dtype — numbers become text (as the lxml engine formats them),
    nulls stay null — and is near zero-copy for already-arrow-backed columns.
    """
    array = pyarrow.array(series.astype("string[pyarrow]"), type=pyarrow.string())
    # Arrow-backed pandas columns are stored as a ChunkedArray (one chunk per
    # parsed file after concat); pyarrow.array() passes that through unchanged.
    # The compiled extension pointer-casts the buffers of ONE contiguous array,
    # so flatten: zero-copy for a single chunk, one concatenation otherwise.
    return array.combine_chunks() if isinstance(array, pyarrow.ChunkedArray) else array


def generate_xml(instance_data,
                 rdf_map=None,
                 namespace_map=None,
                 class_KEY="Type",
                 export_undefined=True,
                 comment=None,
                 debug=False):
    """Generate an RDF XML file from a triplet dataset instance.

    Same parameters and return value as :func:`cimxml_pandas.generate_xml`;
    see there for full documentation.

    Returns
    -------
    dict
        {'filename': str, 'file': bytes (UTF-8 XML)}
    """
    rdf_map = load_rdf_map(rdf_map)
    file_name, namespace_map, instance_rdf_map = resolve_instance_config(instance_data, rdf_map, namespace_map)

    if instance_rdf_map is None:
        logger.warning("No rdf mapping available for {}".format(file_name))
        if not export_undefined:
            logger.warning("File not created for {}".format(file_name))
            return

    batch = pyarrow.RecordBatch.from_arrays(
        [_string_array(instance_data[column]) for column in ("ID", "KEY", "VALUE")],
        names=["ID", "KEY", "VALUE"],
    )

    xml = generate_xml_from_arrow(batch, rdf_map, namespace_map, instance_rdf_map, file_name,
                                  class_KEY=class_KEY, export_undefined=export_undefined, comment=comment)

    logger.info("Exporting RDF to {}".format(file_name))

    return {"filename": file_name, "file": xml}
