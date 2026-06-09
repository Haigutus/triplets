# -------------------------------------------------------------------------------
# Name:        export
# Purpose:     Package for triplet DataFrame export functions
# -------------------------------------------------------------------------------
import pandas

from triplets.export.pandas_engine import (
    export_to_excel,
    export_to_csv,
    _get_qname,
    generate_xml,
    ExportType,
    export_to_cimxml,
    export_to_networkx,
)

__all__ = [
    "export_to_excel",
    "export_to_csv",
    "_get_qname",
    "generate_xml",
    "ExportType",
    "export_to_cimxml",
    "export_to_networkx",
]

# Register monkey-patches on pandas.DataFrame
pandas.DataFrame.export_to_excel = export_to_excel
pandas.DataFrame.export_to_csv = export_to_csv
pandas.DataFrame.export_to_cimxml = export_to_cimxml
pandas.DataFrame.to_networkx = export_to_networkx
