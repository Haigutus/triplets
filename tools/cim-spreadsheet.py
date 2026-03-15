import sys
import os
import argparse
import logging

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from triplets import cim_spreadsheet
from triplets.export_schema import schemas

def main():
    parser = argparse.ArgumentParser(description="Convert CIM XML to Spreadsheet (Excel/CSV) and vice versa.")
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # To Spreadsheet
    to_sheet = subparsers.add_parser("to-spreadsheet", help="Convert CIM XML to Spreadsheet")
    to_sheet.add_argument("--input", "-i", required=True, help="Input CIM XML file or ZIP")
    to_sheet.add_argument("--output", "-o", required=True, help="Output Excel file or ZIP/directory for CSV")
    to_sheet.add_argument("--format", "-f", choices=["excel", "csv"], default=None, help="Output format (deduced if not provided)")
    to_sheet.add_argument("--zip", "-z", action="store_true", default=None, help="Zip output (default for CSV)")
    to_sheet.add_argument("--no-zip", action="store_false", dest="zip", help="Do not zip output")
    to_sheet.add_argument("--multivalue", "-m", action="store_true", help="Aggregate duplicate (ID, KEY) pairs into lists")
    
    # To CIM
    to_cim = subparsers.add_parser("to-cim", help="Convert Spreadsheet to CIM XML")
    to_cim.add_argument("--input", "-i", required=True, help="Input Excel file or ZIP/directory for CSV")
    to_cim.add_argument("--output", "-o", required=True, help="Output directory for CIM XML files")
    to_cim.add_argument("--format", "-f", choices=["excel", "csv"], default=None, help="Input format (deduced if not provided)")
    to_cim.add_argument("--rdf-map", "-r", help="Path to RDF map JSON (optional)")
    to_cim.add_argument("--export-type", "-e", choices=["xml_per_instance", "xml_per_instance_zip_per_all", "xml_per_instance_zip_per_xml"], 
                        default=None, help="How to package the CIM XML export")
    to_cim.add_argument("--zip", "-z", action="store_true", default=None, help="Zip output (default for RDF/XML)")
    to_cim.add_argument("--no-zip", action="store_false", dest="zip", help="Do not zip output")
    to_cim.add_argument("--multivalue", "-m", action="store_true", help="Unpack list values into separate triplets")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
        
    logging.basicConfig(level=logging.INFO)
    
    try:
        if args.command == "to-spreadsheet":
            cim_spreadsheet.cim_to_spreadsheet(args.input, args.output, format=args.format, zip=args.zip, multivalue=args.multivalue)
            print(f"Successfully converted {args.input} to {args.output}")
            
        elif args.command == "to-cim":
            cim_spreadsheet.spreadsheet_to_cim(args.input, args.output, format=args.format, rdf_map=args.rdf_map, export_type=args.export_type, multivalue=args.multivalue, zip=args.zip)
            print(f"Successfully converted {args.input} to CIM XML in {args.output}")
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
