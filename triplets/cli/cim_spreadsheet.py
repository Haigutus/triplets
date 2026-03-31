"""
CIM Spreadsheet Converter CLI Tool
===================================

Command-line tool for converting between CIM XML (Common Information Model) and
spreadsheet formats (Excel/CSV).

This tool provides bidirectional conversion:
- **CIM to Spreadsheet**: Convert CIM XML files to Excel or CSV format
- **Spreadsheet to CIM**: Convert Excel or CSV files back to CIM XML

The tool automatically detects the conversion direction based on file extensions,
making it simple to use without explicit direction specification.

Installation
------------
Install the triplets package with optional dependencies::

    pip install triplets[optional]

Or install from source::

    git clone https://github.com/Haigutus/triplets.git
    cd triplets
    pip install -e .[optional]

Using uv (recommended)::

    uv pip install -e .[optional]

The ``optional`` extra includes ``openpyxl`` for Excel support.

Usage
-----
After installation, the tool can be invoked in three ways:

1. **As a command-line tool** (recommended)::

    cim-spreadsheet -i input_file -o output_file

2. **As a Python module**::

    python -m triplets.tools.cim_spreadsheet_cli -i input_file -o output_file

3. **Programmatically** in Python code::

    from triplets.tools.cim_spreadsheet_cli import cim_to_spreadsheet, spreadsheet_to_cim

    # Convert CIM to Excel
    cim_to_spreadsheet('model.xml', 'output.xlsx')

    # Convert Excel to CIM
    spreadsheet_to_cim('data.xlsx', 'cim_output/')

Examples
--------
Convert CIM XML to Excel::

    cim-spreadsheet -i model.xml -o output.xlsx

Convert CIM XML to CSV (auto-zipped)::

    cim-spreadsheet -i model.xml -o output.zip -f csv

Convert Excel back to CIM XML::

    cim-spreadsheet -i output.xlsx -o cim_output/

Disable multivalue mode (keep duplicate ID+KEY pairs as separate rows)::

    cim-spreadsheet -i model.xml -o output.xlsx --no-multivalue

Select specific sheets to convert::

    cim-spreadsheet -i data.xlsx -o output/ --sheets ACLineSegment Substation

Include raw triplets sheet::

    cim-spreadsheet -i data.xlsx -o output/ --triplets-sheet RawData

Explicitly specify conversion direction::

    cim-spreadsheet -i model.xml -o output.xlsx -d to-spreadsheet

Force ZIP output for Excel::

    cim-spreadsheet -i model.xml -o output.zip -f excel -z

Disable ZIP output for CSV::

    cim-spreadsheet -i model.xml -o csv_dir/ -f csv --no-zip

Features
--------
- Auto-detection of conversion direction from file extensions
- Support for both Excel (.xlsx) and CSV formats
- ZIP compression support for output files
- Multivalue mode enabled by default (aggregates/unpacks duplicate ID+KEY pairs into lists)
- Sheet/file selection for both Excel and CSV formats
- Raw triplets import from dedicated Excel sheet or CSV file
- Handles zipped input/output files automatically

See Also
--------
cim-diff : Tool for comparing CIM XML files
triplets.rdf_parser : Core module for RDF/CIM data manipulation
"""

import sys
import os
import argparse
import logging
import zipfile
import pandas
from io import BytesIO, StringIO
from uuid import uuid4

from .. import rdf_parser
from ..export_schema import schemas

def cim_to_spreadsheet(cim_path, output_path, format=None, zip_output=None, multivalue=True):
    """
    Convert CIM XML to spreadsheet format (Excel or CSV).

    Handles all orchestration including file I/O, format detection, zipping,
    and conversion through the core rdf_parser functions.

    Parameters
    ----------
    cim_path : str
        Path to input CIM XML file or ZIP containing XML files
    output_path : str
        Path to output file (for Excel/zipped CSV) or directory (for CSV)
    format : {'excel', 'csv'}, optional
        Output format. If None, auto-detected from output_path extension.
        Defaults to 'excel' if ambiguous.
    zip_output : bool, optional
        Whether to ZIP the output. If None, defaults to True for CSV format,
        False for Excel format.
    multivalue : bool, default True
        If True, aggregate duplicate (ID, KEY) pairs into lists in the output.
        Use False to keep duplicate pairs as separate rows.

    Raises
    ------
    ImportError
        If openpyxl is not installed and Excel format is requested

    Examples
    --------
    >>> cim_to_spreadsheet('model.xml', 'output.xlsx')
    >>> cim_to_spreadsheet('model.zip', 'output.zip', format='csv')
    >>> cim_to_spreadsheet('model.xml', 'output.xlsx', multivalue=True)
    """

    # Format detection
    if format is None:
        if output_path.endswith((".xlsx", ".xls")):
            format = "excel"
        elif output_path.endswith((".csv", ".zip")) or os.path.isdir(output_path):
            format = "csv"
        else:
            format = "excel"

    # Default zip behavior
    if zip_output is None:
        zip_output = (format == "csv")

    # Load data
    data = rdf_parser.load_all_to_dataframe(cim_path)

    # Determine filename for export
    base_name = os.path.basename(output_path).replace('.zip', '').replace('.xlsx', '').replace('.csv', '')
    if not base_name:
        base_name = 'export'

    # Export based on format using core functions
    if format == "excel":
        # Use core export_to_excel with single_file mode
        excel_file = data.export_to_excel(
            export_to_memory=True,
            multivalue=multivalue,
            single_file=True,
            filename=f"{base_name}.xlsx",
            apply_formatting=True
        )

        if zip_output:
            # Zip the Excel file
            zip_path = output_path if output_path.endswith('.zip') else output_path + '.zip'
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(excel_file.name, excel_file.getvalue())
        else:
            # Write directly to file
            with open(output_path, 'wb') as f:
                f.write(excel_file.getvalue())

    elif format == "csv":
        # Use core export_to_csv with single_file mode
        csv_files = data.export_to_csv(
            export_to_memory=True,
            multivalue=multivalue,
            single_file=True,
            base_filename=base_name
        )

        if zip_output:
            # Zip all CSV files
            zip_path = output_path if output_path.endswith('.zip') else output_path + '.zip'
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for csv_file in csv_files:
                    zf.writestr(csv_file.name, csv_file.getvalue())
        else:
            # Write to directory
            os.makedirs(output_path, exist_ok=True)
            for csv_file in csv_files:
                csv_path = os.path.join(output_path, csv_file.name)
                with open(csv_path, 'wb') as f:
                    f.write(csv_file.getvalue())


def spreadsheet_to_cim(input_path, output_path, format=None, rdf_map=None,
                       export_type=None, multivalue=True, zip_output=None,
                       sheets=None, triplets_sheet=None):
    """
    Convert spreadsheet format (Excel or CSV) to CIM XML.

    Handles all orchestration including file I/O, format detection, unzipping,
    sheet selection, raw triplets import, and conversion through core rdf_parser
    functions.

    Parameters
    ----------
    input_path : str
        Path to input Excel file, CSV directory, or ZIP archive
    output_path : str
        Path to output directory where CIM XML files will be written
    format : {'excel', 'csv'}, optional
        Input format. If None, auto-detected from input_path extension.
        Defaults to 'excel' if ambiguous.
    rdf_map : str, optional
        Path to RDF map JSON file for custom mappings during export
    export_type : {'xml_per_instance', 'xml_per_instance_zip_per_all', 'xml_per_instance_zip_per_xml'}, optional
        How to package the CIM XML output. If None, defaults to
        'xml_per_instance_zip_per_all' if zip_output=True, else 'xml_per_instance'
    multivalue : bool, default True
        If True, unpack list values into separate triplets during conversion.
        Use False to keep list values as-is in single triplets.
    zip_output : bool, optional
        Whether to ZIP the output. Defaults to True.
    sheets : list of str, optional
        Specific sheet/file names to convert. For Excel, these are sheet names.
        For CSV, these are base filenames (without .csv extension).
        If None, converts all sheets/files except triplets_sheet.
    triplets_sheet : str, optional
        Name of sheet/file containing raw triplets (ID, KEY, VALUE columns)
        to include in the output. For Excel, this is a sheet name. For CSV,
        this is a base filename (without .csv extension).
        This sheet is not processed as tableview data.

    Returns
    -------
    result
        Export result from rdf_parser.export_to_cimxml()

    Raises
    ------
    ImportError
        If openpyxl is not installed and Excel format is specified
    ValueError
        If CSV format is used with a file (requires directory or ZIP)
        If no Excel file found in ZIP archive
        If triplets sheet is missing required columns

    Examples
    --------
    >>> spreadsheet_to_cim('data.xlsx', 'output/')
    >>> spreadsheet_to_cim('data.zip', 'output/', sheets=['ACLineSegment', 'Substation'])
    >>> spreadsheet_to_cim('data.xlsx', 'output/', triplets_sheet='RawData')
    """

    # Format detection
    if format is None:
        if input_path.endswith((".xlsx", ".xls")):
            format = "excel"
        elif input_path.endswith((".csv", ".zip")) or os.path.isdir(input_path):
            format = "csv"
        else:
            format = "excel"

    # Deserialize to tableviews
    tableviews = {}
    raw_triplets = []

    if format == "excel":
        # Deserialize Excel to tableviews
        try:
            # Check if it's a zipped Excel file (not just an xlsx which is also a zip)
            if input_path.endswith('.zip') and zipfile.is_zipfile(input_path):
                # Read from ZIP in memory
                with zipfile.ZipFile(input_path, 'r') as zf:
                    # Find the Excel file
                    excel_filename = None
                    for filename in zf.namelist():
                        if filename.endswith(('.xlsx', '.xls')):
                            excel_filename = filename
                            break

                    if not excel_filename:
                        raise ValueError("No Excel file found in ZIP archive")

                    # Read Excel file to BytesIO
                    excel_bytes = BytesIO(zf.read(excel_filename))

                    # Read sheets as tableviews
                    excel_obj = pandas.ExcelFile(excel_bytes)
                    _read_excel_sheets(excel_obj, tableviews, raw_triplets, sheets, triplets_sheet)
            else:
                # Read directly from file (including .xlsx files)
                excel_obj = pandas.ExcelFile(input_path)
                _read_excel_sheets(excel_obj, tableviews, raw_triplets, sheets, triplets_sheet)
        except ImportError as e:
            raise ImportError(
                "openpyxl is required for Excel import. "
                "Install it with: pip install openpyxl"
            ) from e

    elif format == "csv":
        # Deserialize CSV to tableviews
        # Use base filenames (without .csv) as sheet names, analogous to Excel sheet names
        csv_dataframes = {}

        if zipfile.is_zipfile(input_path):
            # Read from ZIP
            with zipfile.ZipFile(input_path, 'r') as zf:
                for filename in zf.namelist():
                    if filename.endswith('.csv'):
                        sheet_name = os.path.basename(filename)[:-4]
                        csv_content = zf.read(filename).decode('utf-8')
                        csv_dataframes[sheet_name] = pandas.read_csv(StringIO(csv_content), index_col=0)
        elif os.path.isdir(input_path):
            # Read from directory
            for filename in os.listdir(input_path):
                if filename.endswith('.csv'):
                    sheet_name = filename[:-4]
                    csv_path = os.path.join(input_path, filename)
                    csv_dataframes[sheet_name] = pandas.read_csv(csv_path, index_col=0)
        else:
            raise ValueError(f"CSV format requires a directory or ZIP file, got: {input_path}")

        # Handle triplets sheet (by CSV base filename)
        if triplets_sheet and triplets_sheet in csv_dataframes:
            triplet_df = csv_dataframes.pop(triplets_sheet).reset_index()
            if all(col in triplet_df.columns for col in ["ID", "KEY", "VALUE"]):
                raw_triplets.append(triplet_df[["ID", "KEY", "VALUE"]])
            else:
                logging.warning(f"Triplets CSV '{triplets_sheet}' missing required columns (ID, KEY, VALUE), skipping")

        # Filter by sheet names if specified
        if sheets is not None:
            csv_dataframes = {k: v for k, v in csv_dataframes.items() if k in sheets}
            for sheet_name in sheets:
                if sheet_name not in csv_dataframes:
                    logging.warning(f"CSV '{sheet_name}' not found, skipping")

        tableviews.update(csv_dataframes)

    # Convert tableviews to triplet
    data = rdf_parser.tableviews_to_triplet(tableviews, multivalue=multivalue)

    # Add raw triplets if any
    if raw_triplets:
        data = pandas.concat([data] + raw_triplets, ignore_index=True)

    # Set version metadata
    from triplets._version import get_versions
    version = get_versions()['version']
    data.set_VALUE_at_KEY("Model.applicationSoftware", f"triplets_v{version}")
    # TODO - add support for new header

    # Add INSTANCE_ID if missing
    if "INSTANCE_ID" not in data.columns or data["INSTANCE_ID"].isna().all():
        data["INSTANCE_ID"] = str(uuid4())

    # Export type defaults
    if zip_output is None:
        zip_output = True

    if export_type is None:
        export_type = "xml_per_instance_zip_per_all" if zip_output else "xml_per_instance"

    # Create output directory
    os.makedirs(output_path, exist_ok=True)

    # Export to CIM XML
    return rdf_parser.export_to_cimxml(
        data,
        rdf_map=rdf_map,
        export_undefined=False,
        export_type=export_type,
        export_base_path=output_path,
        debug=False
    )


def _read_excel_sheets(excel_obj, tableviews, raw_triplets, sheets=None, triplets_sheet=None):
    """
    Read Excel sheets into tableviews dictionary and raw triplets list.

    Internal helper function that populates tableviews and raw_triplets
    dictionaries/lists by reading from an Excel file object.

    Parameters
    ----------
    excel_obj : pandas.ExcelFile
        Excel file object to read from
    tableviews : dict
        Dictionary to populate with {sheet_name: DataFrame} tableview data.
        Modified in-place.
    raw_triplets : list
        List to populate with raw triplet DataFrames (ID, KEY, VALUE columns).
        Modified in-place.
    sheets : list of str, optional
        Specific sheet names to read. If None, reads all sheets except
        triplets_sheet.
    triplets_sheet : str, optional
        Name of sheet containing raw triplets (ID, KEY, VALUE columns).
        This sheet is read separately and not included in tableviews.

    Warnings
    --------
    - Logs warning if specified sheet not found in Excel file
    - Logs warning if triplets sheet missing required columns (ID, KEY, VALUE)
    """

    # Determine which sheets to read
    if sheets is None:
        # Read all sheets except the triplets sheet
        sheets_to_read = [s for s in excel_obj.sheet_names if s != triplets_sheet]
    else:
        sheets_to_read = sheets

    # Read tableview sheets
    for sheet_name in sheets_to_read:
        if sheet_name in excel_obj.sheet_names:
            tableviews[sheet_name] = pandas.read_excel(excel_obj, sheet_name=sheet_name, index_col=0)
        else:
            logging.warning(f"Sheet '{sheet_name}' not found in Excel file, skipping")

    # Read raw triplets sheet if specified
    if triplets_sheet and triplets_sheet in excel_obj.sheet_names:
        triplet_df = pandas.read_excel(excel_obj, sheet_name=triplets_sheet)
        # Ensure it has the required columns
        if all(col in triplet_df.columns for col in ["ID", "KEY", "VALUE"]):
            raw_triplets.append(triplet_df[["ID", "KEY", "VALUE"]])
        else:
            logging.warning(f"Triplets sheet '{triplets_sheet}' missing required columns (ID, KEY, VALUE), skipping")


def detect_conversion_direction(input_path, output_path):
    """
    Auto-detect conversion direction from file extensions.

    Examines input and output file paths to determine whether the conversion
    should be CIM-to-spreadsheet or spreadsheet-to-CIM.

    Parameters
    ----------
    input_path : str
        Input file or directory path
    output_path : str
        Output file or directory path

    Returns
    -------
    str
        Either 'to-spreadsheet' or 'to-cim'

    Raises
    ------
    ValueError
        If conversion direction cannot be determined from file extensions

    Notes
    -----
    Detection logic:

    - If input is .xml/.rdf (or ZIP containing such files), direction is 'to-spreadsheet'
    - If input is .xlsx/.xls/.csv (or directory with CSVs), direction is 'to-cim'
    - If ambiguous from input, checks output path for spreadsheet extensions or
      directory-like patterns
    - Raises ValueError if direction cannot be determined

    Examples
    --------
    >>> detect_conversion_direction('model.xml', 'output.xlsx')
    'to-spreadsheet'
    >>> detect_conversion_direction('data.xlsx', 'output/')
    'to-cim'
    """

    # Check if input is CIM XML
    input_is_cim = input_path.endswith(('.xml', '.rdf')) or (
        zipfile.is_zipfile(input_path) and
        any(f.endswith(('.xml', '.rdf')) for f in zipfile.ZipFile(input_path).namelist())
    )

    # Check if input is spreadsheet
    input_is_spreadsheet = input_path.endswith(('.xlsx', '.xls', '.csv')) or (
        os.path.isdir(input_path) and any(f.endswith('.csv') for f in os.listdir(input_path))
    )

    # Determine direction
    if input_is_cim and not input_is_spreadsheet:
        return 'to-spreadsheet'
    elif input_is_spreadsheet and not input_is_cim:
        return 'to-cim'
    else:
        # Ambiguous, check output
        output_is_spreadsheet = output_path.endswith(('.xlsx', '.xls', '.csv'))
        output_is_dir = os.path.isdir(output_path) or '/' in output_path or '\\' in output_path

        if output_is_spreadsheet:
            return 'to-spreadsheet'
        elif output_is_dir:
            return 'to-cim'
        else:
            raise ValueError(
                "Cannot auto-detect conversion direction. "
                "Please specify --direction (to-spreadsheet or to-cim)"
            )


def main():
    """
    CLI entry point for cim-spreadsheet tool.

    Parses command-line arguments and executes the appropriate conversion
    (CIM-to-spreadsheet or spreadsheet-to-CIM) with auto-detection of
    conversion direction.

    Command-Line Usage
    ------------------
    Basic conversion (auto-detect direction)::

        cim-spreadsheet -i input_file -o output_file

    Explicit direction::

        cim-spreadsheet -i model.xml -o output.xlsx -d to-spreadsheet
        cim-spreadsheet -i data.xlsx -o output/ -d to-cim

    Format specification::

        cim-spreadsheet -i model.xml -o output.zip -f csv
        cim-spreadsheet -i data.zip -o output/ -f csv

    Advanced options::

        cim-spreadsheet -i model.xml -o output.xlsx --no-multivalue  # disable multivalue mode
        cim-spreadsheet -i data.xlsx -o output/ --sheets Sheet1 Sheet2
        cim-spreadsheet -i data.xlsx -o output/ --triplets-sheet RawData
        cim-spreadsheet -i model.xml -o output.xlsx -z  # force ZIP

    Exit Codes
    ----------
    0 : Successful conversion
    1 : Error during conversion (see stderr for details)

    See Also
    --------
    cim_to_spreadsheet : Function for CIM to spreadsheet conversion
    spreadsheet_to_cim : Function for spreadsheet to CIM conversion
    detect_conversion_direction : Auto-detection logic
    """
    parser = argparse.ArgumentParser(
        description="Convert between CIM XML and Spreadsheet (Excel/CSV) formats. "
                    "Conversion direction is auto-detected from file extensions."
    )

    # Common arguments
    parser.add_argument("--input", "-i", required=True, help="Input file or directory")
    parser.add_argument("--output", "-o", required=True, help="Output file or directory")
    parser.add_argument("--direction", "-d", choices=["to-spreadsheet", "to-cim"], help="Conversion direction (auto-detected if not specified)")
    parser.add_argument("--format", "-f", choices=["excel", "csv"], help="Spreadsheet format (auto-detected if not specified)")
    parser.add_argument("--no-multivalue", action="store_false", dest="multivalue", help="Disable multivalue mode (keep duplicate (ID, KEY) pairs as separate rows/triplets instead of aggregating into lists)")
    parser.set_defaults(multivalue=True)
    parser.add_argument("--zip", "-z", action="store_true", dest="zip_output", help="Zip output")
    parser.add_argument("--no-zip", action="store_false", dest="zip_output", help="Do not zip output")
    parser.set_defaults(zip_output=None)

    # Spreadsheet to CIM specific arguments
    parser.add_argument("--rdf-map", "-r", help="Path to RDF map JSON (for to-cim conversion)")
    parser.add_argument("--export-type", "-e", choices=["xml_per_instance", "xml_per_instance_zip_per_all", "xml_per_instance_zip_per_xml"], help="How to package CIM XML export (for to-cim conversion)")
    parser.add_argument("--sheets", "-s", nargs='+', help="Specific sheet/file names to convert (Excel sheet names or CSV base filenames)")
    parser.add_argument("--triplets-sheet", "-t", help="Sheet/file name containing raw triplets (ID, KEY, VALUE) to include (Excel sheet name or CSV base filename)")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    # Detect conversion direction if not specified
    direction = args.direction
    if direction is None:
        try:
            direction = detect_conversion_direction(args.input, args.output)
            logging.info(f"Auto-detected conversion direction: {direction}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        if direction == "to-spreadsheet":
            cim_to_spreadsheet(
                args.input,
                args.output,
                format=args.format,
                zip_output=args.zip_output,
                multivalue=args.multivalue
            )
            print(f"Converted {args.input} → {args.output}")

        elif direction == "to-cim":
            spreadsheet_to_cim(
                args.input,
                args.output,
                format=args.format,
                rdf_map=args.rdf_map,
                export_type=args.export_type,
                multivalue=args.multivalue,
                zip_output=args.zip_output,
                sheets=args.sheets,
                triplets_sheet=args.triplets_sheet
            )
            print(f"Converted {args.input} → {args.output}")

    except Exception as e:
        logging.exception("Error during conversion")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
