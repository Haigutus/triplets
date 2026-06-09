"""Triplet DataFrame export functions.

Formats: Excel, CSV, CIM XML, N-Quads, NetworkX.
Each format has its own {format}_{engine}.py file.
"""

import pandas

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
        from .csv_polars import export_to_csv as _fn
    else:
        from .csv_pandas import export_to_csv as _fn
    return _fn(data, path=path, multivalue=multivalue, export_to_memory=export_to_memory,
               single_file=single_file, base_filename=base_filename)


def export_to_nquads(data, path):
    """Export triplet DataFrame to N-Quads file.

    Auto-detects engine: polars if input is polars DataFrame, else pandas.
    """
    if hasattr(data, '__module__') and 'polars' in type(data).__module__:
        from .nquads_polars import export_to_nquads as _fn
        return _fn(data, path)
    try:
        import polars
        # Convert pandas to polars for the faster path
        from .nquads_polars import export_to_nquads as _fn
        return _fn(polars.from_pandas(data), path)
    except ImportError:
        from .nquads_pandas import export_to_nquads as _fn
        return _fn(data, path)


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
