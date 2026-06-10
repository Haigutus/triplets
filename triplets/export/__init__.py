"""Triplet DataFrame export functions.

Formats: Excel, CSV, CIM XML, N-Quads, NetworkX.
Each format has its own {format}_{engine}.py file.
"""

import pandas
import logging
logger = logging.getLogger(__name__)

from .excel_pandas import export_to_excel
from .cimxml_pandas import export_to_cimxml, generate_xml, ExportType, _get_qname
from .networkx_pandas import export_to_networkx


def _is_polars(data):
    return hasattr(data, '__module__') and 'polars' in type(data).__module__


def export_to_csv(data, path=None, multivalue=True, export_to_memory=False, single_file=False, base_filename=None):
    """Export triplet DataFrame to CSV files.

    Auto-detects engine: polars if input is polars DataFrame, else pandas.
    """
    if _is_polars(data):
        logger.debug("format=csv, engine=polars (auto-detected)")
        from .csv_polars import export_to_csv as _fn
    else:
        logger.debug("format=csv, engine=pandas (auto-detected)")
        from .csv_pandas import export_to_csv as _fn
    return _fn(data, path=path, multivalue=multivalue, export_to_memory=export_to_memory,
               single_file=single_file, base_filename=base_filename)


def export_to_nquads(data, path, rdf_map=None):
    """Export triplet DataFrame to N-Quads file.

    Parameters
    ----------
    rdf_map : dict or str, optional
        Export schema for proper enum detection. If None, enums exported as literals.
    """
    if _is_polars(data):
        logger.debug("format=nquads, engine=polars (auto-detected)")
        from .nquads_polars import export_to_nquads as _fn
        return _fn(data, path, rdf_map=rdf_map)
    logger.debug("format=nquads, engine=pandas (auto-detected)")
    from .nquads_pandas import export_to_nquads as _fn
    return _fn(data, path, rdf_map=rdf_map)


__all__ = [
    "export_to_excel",
    "export_to_csv",
    "export_to_cimxml",
    "export_to_nquads",
    "export_to_networkx",
    "generate_xml",
    "ExportType",
    "_get_qname",
]

# Register monkey-patches on pandas.DataFrame
pandas.DataFrame.export_to_excel = export_to_excel
pandas.DataFrame.export_to_csv = export_to_csv
pandas.DataFrame.export_to_cimxml = export_to_cimxml
pandas.DataFrame.export_to_nquads = export_to_nquads
pandas.DataFrame.export_to_networkx = export_to_networkx
