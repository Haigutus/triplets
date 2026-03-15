import pandas
import logging
import os
import zipfile
from io import BytesIO
from triplets import rdf_parser
from uuid import uuid4

logger = logging.getLogger(__name__)

def cim_to_spreadsheet(cim_path, spreadsheet_path, format=None, zip=None, multivalue=False):
    """
    Convert a CIM XML file (or a collection of them) to a spreadsheet (Excel or CSV).
    
    Parameters
    ----------
    cim_path : str or list
        Path to the CIM XML file or a ZIP file containing CIM XMLs, or a list of such paths.
    spreadsheet_path : str
        Path to save the output spreadsheet (Excel file or directory/ZIP for CSVs).
    format : str, optional
        Output format: 'excel' or 'csv'. If None, deduced from spreadsheet_path.
    zip : bool, optional
        If True, the resulting file(s) will be zipped. 
        Defaults to True for CSV and False for Excel if format is None.
    multivalue : bool, optional
        If True, aggregate duplicate (ID, KEY) pairs into lists (default is False).
    """
    if format is None:
        if spreadsheet_path.endswith((".xlsx", ".xls")):
            format = "excel"
        elif spreadsheet_path.endswith((".csv", ".zip")) or os.path.isdir(spreadsheet_path):
            format = "csv"
        else:
            format = "excel"  # Default fallback
            
    if zip is None:
        zip = True if format == "csv" else False
        
    data = rdf_parser.load_all_to_dataframe(cim_path)
    
    if format == "excel":
        if zip:
            # If Excel and zip requested, we zip the excel file
            # This is less common but we can do it
            excel_buffer = BytesIO()
            _triplet_to_excel(data, excel_buffer, multivalue=multivalue)
            if not spreadsheet_path.endswith(".zip"):
                spreadsheet_path += ".zip"
            with zipfile.ZipFile(spreadsheet_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(os.path.basename(spreadsheet_path).replace(".zip", ".xlsx"), excel_buffer.getvalue())
        else:
            _triplet_to_excel(data, spreadsheet_path, multivalue=multivalue)
    elif format == "csv":
        _triplet_to_csv(data, spreadsheet_path, zip_csv=zip, multivalue=multivalue)
    else:
        raise ValueError(f"Unsupported format: {format}. Supported formats: 'excel', 'csv'.")

def spreadsheet_to_cim(spreadsheet_path, output_path, format=None, rdf_map=None, export_type=None, multivalue=False, zip=None):
    """
    Convert a spreadsheet (Excel or CSV) to a CIM XML file.
    
    Parameters
    ----------
    spreadsheet_path : str
        Path to the spreadsheet file (Excel file or directory/ZIP for CSVs).
    output_path : str
        Directory or filename for the output CIM XML.
    format : str, optional
        Input format: 'excel' or 'csv'. If None, deduced from spreadsheet_path.
    rdf_map : str or dict, optional
        Path to the RDF configuration JSON or a dictionary with the configuration.
    export_type : str, optional
        How the export is to be packaged. If None, uses zip parameter or defaults to ZIP.
    multivalue : bool, optional
        If True, unpack list values into separate triplets (default is False).
    zip : bool, optional
        If True, the output XMLs will be zipped. Defaults to True for RDF/XML.
    """
    if format is None:
        if spreadsheet_path.endswith((".xlsx", ".xls")):
            format = "excel"
        elif spreadsheet_path.endswith((".csv", ".zip")) or os.path.isdir(spreadsheet_path):
            format = "csv"
        else:
            format = "excel"

    if format == "excel":
        data = _excel_to_triplet(spreadsheet_path, multivalue=multivalue)
    elif format == "csv":
        data = _csv_to_triplet(spreadsheet_path, multivalue=multivalue)
    else:
        raise ValueError(f"Unsupported format: {format}. Supported formats: 'excel', 'csv'.")
    
    # Set converter version (following tools/excel_to_rdf.py)
    from ._version import get_versions
    version = get_versions()['version']
    data.set_VALUE_at_KEY("Model.applicationSoftware", f"triplets_spreadsheet_converter_{version}")

    # Add instance ID if missing
    if "INSTANCE_ID" not in data.columns or data["INSTANCE_ID"].isna().all():
        data["INSTANCE_ID"] = str(uuid4())

    # ZIP default for RDF/XML
    if zip is None:
        zip = True
        
    if export_type is None:
        export_type = "xml_per_instance_zip_per_all" if zip else "xml_per_instance"

    # Ensure output directory exists
    if not os.path.exists(output_path):
        os.makedirs(output_path)

    # Export to CIM XML
    return rdf_parser.export_to_cimxml(data,
                                      rdf_map=rdf_map,
                                      export_undefined=False,
                                      export_type=export_type,
                                      export_base_path=output_path,
                                      debug=False)

def _triplet_to_excel(data, excel_path, multivalue=False):
    """
    Convert triplet DataFrame to Excel with sheets per class.
    """
    types = data.types_dict()
    
    with pandas.ExcelWriter(excel_path) as writer:
        for class_name in types:
            table_view = data.type_tableview(class_name, multivalue=multivalue)
            if table_view is not None:
                table_view.to_excel(writer, sheet_name=class_name)

def _triplet_to_csv(data, output_path, zip_csv=True, multivalue=False):
    """
    Convert triplet DataFrame to multiple CSV files, optionally zipped.
    """
    types = data.types_dict()
    
    if zip_csv:
        if not output_path.endswith(".zip"):
            output_path += ".zip"
        
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for class_name in types:
                table_view = data.type_tableview(class_name, multivalue=multivalue)
                if table_view is not None:
                    csv_buffer = BytesIO()
                    table_view.to_csv(csv_buffer)
                    zf.writestr(f"{class_name}.csv", csv_buffer.getvalue())
    else:
        if not os.path.exists(output_path):
            os.makedirs(output_path)
            
        for class_name in types:
            table_view = data.type_tableview(class_name, multivalue=multivalue)
            if table_view is not None:
                csv_path = os.path.join(output_path, f"{class_name}.csv")
                table_view.to_csv(csv_path)

def _excel_to_triplet(excel_path, multivalue=False):
    """
    Read Excel file with sheets per class and convert to triplet format.
    """
    excel_file = pandas.ExcelFile(excel_path)
    all_triplets = []
    
    for sheet_name in excel_file.sheet_names:
        df = pandas.read_excel(excel_file, sheet_name=sheet_name, index_col=0)
        # Re-add the Type column to allow reconstruction of triplets
        df['Type'] = sheet_name
        triplet = df.tableview_to_triplet(multivalue=multivalue)
        # Filter out NaN values from empty cells
        triplet = triplet[triplet['VALUE'].astype(str) != 'nan']
        all_triplets.append(triplet)
    
    if not all_triplets:
        return pandas.DataFrame(columns=['ID', 'KEY', 'VALUE', 'INSTANCE_ID'])
        
    return pandas.concat(all_triplets, ignore_index=True)

def _csv_to_triplet(csv_path, multivalue=False):
    """
    Read CSV files from a directory or ZIP and convert to triplet format.
    """
    all_triplets = []
    
    if os.path.isfile(csv_path) and zipfile.is_zipfile(csv_path):
        with zipfile.ZipFile(csv_path, 'r') as zf:
            for filename in zf.namelist():
                if filename.endswith(".csv"):
                    class_name = filename[:-4]
                    with zf.open(filename) as f:
                        df = pandas.read_csv(f, index_col=0)
                        df['Type'] = class_name
                        triplet = df.tableview_to_triplet(multivalue=multivalue)
                        triplet = triplet[triplet['VALUE'].astype(str) != 'nan']
                        all_triplets.append(triplet)
    elif os.path.isdir(csv_path):
        for filename in os.listdir(csv_path):
            if filename.endswith(".csv"):
                class_name = filename[:-4]
                df = pandas.read_csv(os.path.join(csv_path, filename), index_col=0)
                df['Type'] = class_name
                triplet = df.tableview_to_triplet(multivalue=multivalue)
                triplet = triplet[triplet['VALUE'].astype(str) != 'nan']
                all_triplets.append(triplet)
    else:
        raise ValueError(f"Path {csv_path} must be a directory or a ZIP file for CSV format.")
            
    if not all_triplets:
        return pandas.DataFrame(columns=['ID', 'KEY', 'VALUE', 'INSTANCE_ID'])
        
    return pandas.concat(all_triplets, ignore_index=True)
