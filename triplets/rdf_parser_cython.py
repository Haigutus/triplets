# -------------------------------------------------------------------------------
# Name:        RDF parser - Cython variant
# Purpose:     Uses Cython extraction loop with pugixml C++ API directly.
#              XML parsing and element iteration happen at C++ speed,
#              bypassing Python wrapper overhead entirely.
#
# Based on:    rdf_parser.py by kristjan.vilgo
# Variant:     Cython + pugixml direct (benchmark variant)
# -------------------------------------------------------------------------------
from io import BytesIO

from triplets.rdf_extract_cython import load_rdf_to_list_cython

import pandas
import datetime
import zipfile
import logging

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


def _print_duration(text, start_time):
    end_time = datetime.datetime.now()
    duration = end_time - start_time
    logger.debug(f"{text}  {duration}")
    return duration, end_time


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
    """Parse multiple RDF XML files using Cython+pugixml into a single Pandas DataFrame."""
    if debug:
        process_start = datetime.datetime.now()

    if type(list_of_paths_to_zip_globalzip_xml) != list:
        list_of_paths_to_zip_globalzip_xml = [list_of_paths_to_zip_globalzip_xml]

    list_of_xmls = find_all_xml(list_of_paths_to_zip_globalzip_xml, debug)

    data_list = []

    if max_workers:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(load_rdf_to_list_cython, xml, debug) for xml in list_of_xmls]
            results = [future.result() for future in futures]
            data_list = [item for sublist in results for item in sublist]
    else:
        for xml in list_of_xmls:
            data_list.extend(load_rdf_to_list_cython(xml, debug))

    if debug:
        start_time = datetime.datetime.now()

    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"], dtype=data_type)

    if debug:
        _print_duration("Data list loaded to DataFrame", start_time)
        _print_duration("All loaded in", process_start)

    return data
