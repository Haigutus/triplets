# -------------------------------------------------------------------------------
# Name:        RDF parser - Cython + Arrow variant
# Purpose:     Uses Cython extraction loop with pugixml C++ API that writes
#              directly to Arrow StringBuilders. Returns PyArrow RecordBatch
#              with zero-copy to Polars/pandas.
#
# Based on:    rdf_parser.py by kristjan.vilgo
# Variant:     Cython + pugixml + Arrow direct (benchmark variant)
# -------------------------------------------------------------------------------
from io import BytesIO

from triplets.rdf_extract_cython_arrow import load_rdf_to_arrow_cython

import pyarrow as pa
import polars as pl
import pandas
import datetime
import zipfile
import logging

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


def find_all_xml(list_of_paths_to_zip_globalzip_xml, debug=False):
    """Extract XML files from a list of paths or ZIP archives."""
    xml_files_list = []
    zip_files_list = []

    for item in list_of_paths_to_zip_globalzip_xml:
        if type(item) == str:
            item = open(item, "rb")

        item_lower = item.name.lower()

        if ".xml" in item_lower or ".rdf" in item_lower:
            xml_files_list.append(item)
        elif ".zip" in item_lower:
            zip_files_list.append(item)
        else:
            logger.warning("Not supported file: {}".format(item))

    for zip_file_path in zip_files_list:
        zip_container = zipfile.ZipFile(zip_file_path)
        zipped_files = zip_container.namelist()

        for zipped_file in zipped_files:
            zipped_file_lower = zipped_file.lower()

            if ".xml" in zipped_file_lower or ".rdf" in zipped_file_lower:
                file_object = BytesIO(zip_container.read(zipped_file))
                file_object.name = zipped_file
                xml_files_list.append(file_object)
            elif ".zip" in zipped_file_lower:
                zip_files_list.append(BytesIO(zip_container.read(zipped_file)))
            else:
                logger.warning("Not supported file: {}".format(zipped_file))

    return xml_files_list


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None):
    """Parse RDF XMLs using Cython+Arrow and return pandas DataFrame."""
    if type(list_of_paths_to_zip_globalzip_xml) != list:
        list_of_paths_to_zip_globalzip_xml = [list_of_paths_to_zip_globalzip_xml]

    list_of_xmls = find_all_xml(list_of_paths_to_zip_globalzip_xml, debug)

    batches = []
    for xml in list_of_xmls:
        batches.append(load_rdf_to_arrow_cython(xml, debug))

    table = pa.Table.from_batches(batches)
    return table.to_pandas()


def load_all_to_polars(list_of_paths_to_zip_globalzip_xml, debug=False, max_workers=None):
    """Parse RDF XMLs using Cython+Arrow and return Polars DataFrame (zero-copy)."""
    if type(list_of_paths_to_zip_globalzip_xml) != list:
        list_of_paths_to_zip_globalzip_xml = [list_of_paths_to_zip_globalzip_xml]

    list_of_xmls = find_all_xml(list_of_paths_to_zip_globalzip_xml, debug)

    batches = []
    for xml in list_of_xmls:
        batches.append(load_rdf_to_arrow_cython(xml, debug))

    table = pa.Table.from_batches(batches)
    return pl.from_arrow(table)

if __name__ == '__main__':

    import sys
    logging.basicConfig(stream=sys.stdout,
                        format='%(levelname) -10s %(asctime)s %(name) -30s %(funcName) -35s %(lineno) -5d: %(message)s',
                        level=logging.DEBUG)

    path = "../test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"

    data = load_all_to_dataframe([path], debug=True)
    #data_arrow = pandas.read_RDF([path], debug=True, data_type='string[pyarrow]', max_workers=4)

    # Performance loading TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip
    # 1146191 entries
    # Last took 0:00:07.919968 on python 3.7  and pandas 1.3.5
    # Last took 0:00:04.312218 on python 3.11 and pandas 2.0.2
    # Last took 0:00:01.791312 on python 3.12 and pandas 2.2.3 used 311.6 MB memory and 114.8 MB with arrow backend
    # Last took 0:00:01.486290 on python 3.12 and pandas 2.2.3 [workers=4] used 311.6 MB memory and 114.8 MB with arrow backend

    #data = data.convert_dtypes(dtype_backend='pyarrow')

    #data_arrow = data.convert_dtypes(dtype_backend='pyarrow')
    #arrow_memory = data_arrow.memory_usage(deep=True).sum() / 1024**2
    #pandas_memory = data.memory_usage(deep=True).sum() / 1024**2
    #print(f"p: {pandas_memory}; a: {arrow_memory}; diff {pandas_memory - arrow_memory}")

    print("Loaded types")
    print(data.query("KEY == 'Type'")["VALUE"].value_counts())

    print("Example how to get table view of all objects of specified type")
    print(data.type_tableview("ACLineSegment"))

    print("Example how to get objects referring to specified object")
    print(data.references_to_simple("99722373_VL_TN1"))

    print("Example how to get objects that specified object refers to")
    print(data.references_from_simple("99722373_VL_TN1"))
