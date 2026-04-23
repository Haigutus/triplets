# -------------------------------------------------------------------------------
# Name:        RDF parser - Polars deferred ID cleaning variant
# Purpose:     Loads RDF XMLs without cleaning IDs during parse, then cleans
#              IDs in parallel using Polars string operations.
#              Arrow backend enables zero-cost interop with pandas.
#
# Based on:    rdf_parser.py by kristjan.vilgo
# Variant:     Deferred ID cleaning in Polars (benchmark variant)
# -------------------------------------------------------------------------------
import os
from io import BytesIO

from lxml import etree
import polars as pl
import pandas
import datetime
import zipfile
import uuid

from concurrent.futures import ThreadPoolExecutor

import logging

logger = logging.getLogger(__name__)


def _remove_prefix(original_string, prefix_string):
    prefix_length = len(prefix_string)
    if original_string[0:prefix_length] == prefix_string:
        return original_string[prefix_length:]
    return original_string


def clean_ID(ID):
    ID = _remove_prefix(ID, "urn:uuid:")
    ID = _remove_prefix(ID, "#_")
    ID = _remove_prefix(ID, "_")
    return ID


def _print_duration(text, start_time):
    end_time = datetime.datetime.now()
    duration = end_time - start_time
    logger.debug(f"{text}  {duration}")
    return duration, end_time


def load_RDF_objects_from_XML(path_or_fileobject, debug=False):
    """Parse XML, return RDF objects iterator, instance_id, namespace_map."""
    if debug:
        start_time = datetime.datetime.now()

    parser = etree.XMLParser(remove_comments=True, collect_ids=False, remove_blank_text=True)
    parsed_xml = etree.parse(path_or_fileobject, parser=parser).getroot()

    namespace_map = parsed_xml.nsmap
    namespace_map["xml_base"] = parsed_xml.base

    instance_id = str(uuid.uuid4())

    if debug:
        _, start_time = _print_duration("XML loaded to tree object", start_time)

    RDF_objects = parsed_xml.iterchildren()

    if debug:
        _, start_time = _print_duration("All children put to a generator", start_time)

    return RDF_objects, instance_id, namespace_map


def load_RDF_to_list_raw(path_or_fileobject, debug=False):
    """Parse RDF XML to list of tuples WITHOUT cleaning IDs.

    IDs are stored as-is from the XML (with urn:uuid:, #_, _ prefixes intact).
    Cleaning is deferred to Polars.
    """
    file_name = path_or_fileobject if isinstance(path_or_fileobject, str) else path_or_fileobject.name
    logger.info(f"Loading {file_name}")

    RDF_objects, INSTANCE_ID, namespace_map = load_RDF_objects_from_XML(path_or_fileobject, debug)

    if debug:
        start_time = datetime.datetime.now()

    RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    RDF_ID = f"{{{RDF_NS}}}ID"
    RDF_ABOUT = f"{{{RDF_NS}}}about"
    RDF_NODEID = f"{{{RDF_NS}}}nodeID"
    RDF_RESOURCE = f"{{{RDF_NS}}}resource"

    # Metadata entries (these don't need ID cleaning)
    ID = str(uuid.uuid4())
    ID_NSMAP = str(uuid.uuid4())
    data_list = [
        (ID, "Type", "Distribution", INSTANCE_ID),
        (ID, "label", file_name, INSTANCE_ID),
        (ID_NSMAP, "Type", "NamespaceMap", INSTANCE_ID),
    ]

    for key, value in namespace_map.items():
        data_list.append((ID_NSMAP, key, value, INSTANCE_ID))

    KEY = ""
    VALUE = ""

    for RDF_object in RDF_objects:
        # RAW ID - no clean_ID applied
        ID = RDF_object.attrib.get(RDF_ID) or RDF_object.attrib.get(RDF_ABOUT) or RDF_object.attrib.get(RDF_NODEID)
        KEY = "Type"
        parts = RDF_object.tag.partition("}")
        VALUE = parts[2]
        data_list.append((ID, KEY, VALUE, INSTANCE_ID))

        for element in RDF_object.iterchildren():
            parts = element.tag.partition("}")
            KEY = parts[2]
            VALUE = element.text

            if VALUE is None and element.attrib:
                # Clean reference VALUES inline (they have nuanced prefix logic)
                VALUE = clean_ID(element.attrib.get(RDF_RESOURCE) or element.attrib.get(RDF_NODEID) or "")

                # Enumeration handling
                if VALUE.startswith("http"):
                    VALUE = VALUE.split("#")[-1]

            data_list.append((ID, KEY, VALUE, INSTANCE_ID))

    if debug:
        _print_duration("All values put to data list (raw IDs)", start_time)
    return data_list


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


def _clean_id_column_polars(col: pl.Expr) -> pl.Expr:
    """Clean CIM ID prefixes using Polars string operations (parallel).

    Removes prefixes: 'urn:uuid:', '#_', '_' — applied sequentially via strip_prefix.
    """
    col = col.str.strip_prefix("urn:uuid:")
    col = col.str.strip_prefix("#_")
    col = col.str.strip_prefix("_")
    return col


def _clean_value_references_polars(df: pl.LazyFrame) -> pl.LazyFrame:
    """Clean VALUE column where it contains references (same prefixes as ID)."""
    # VALUE column also contains references that need the same cleaning
    return df.with_columns(
        _clean_id_column_polars(pl.col("VALUE")).alias("VALUE")
    )


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None):
    """Parse multiple RDF XMLs into a pandas DataFrame via Polars with parallel ID cleaning.

    Steps:
    1. Parse XML to raw lists (IDs uncleaned) — optionally threaded
    2. Load into Polars LazyFrame
    3. Clean IDs in parallel using Polars string ops
    4. Convert to pandas via Arrow (zero-copy where possible)
    """
    if debug:
        process_start = datetime.datetime.now()

    if type(list_of_paths_to_zip_globalzip_xml) != list:
        list_of_paths_to_zip_globalzip_xml = [list_of_paths_to_zip_globalzip_xml]

    list_of_xmls = find_all_xml(list_of_paths_to_zip_globalzip_xml, debug)

    data_list = []

    if max_workers:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(load_RDF_to_list_raw, xml, debug) for xml in list_of_xmls]
            results = [future.result() for future in futures]
            data_list = [item for sublist in results for item in sublist]
    else:
        for xml in list_of_xmls:
            data_list.extend(load_RDF_to_list_raw(xml, debug))

    if debug:
        start_time = datetime.datetime.now()

    # Step 2: Load into Polars
    df = pl.LazyFrame(
        data=data_list,
        schema={"ID": pl.Utf8, "KEY": pl.Utf8, "VALUE": pl.Utf8, "INSTANCE_ID": pl.Utf8},
        orient="row",
    )

    if debug:
        _, start_time = _print_duration("Data loaded to Polars LazyFrame", start_time)

    # Step 3: Clean IDs in parallel using Polars (only ID column; VALUES cleaned inline)
    df = df.with_columns(
        _clean_id_column_polars(pl.col("ID")).alias("ID"),
    )

    if debug:
        _, start_time = _print_duration("ID cleaning expressions added to LazyFrame", start_time)

    # Step 4: Collect and convert to pandas via Arrow
    polars_df = df.collect()

    if debug:
        _, start_time = _print_duration("Polars LazyFrame collected (IDs cleaned in parallel)", start_time)

    pandas_df = polars_df.to_pandas(use_pyarrow_extension_array=True)

    if debug:
        _print_duration("Converted to pandas via Arrow", start_time)
        _print_duration("Total time", process_start)

    return pandas_df


def load_all_to_polars(list_of_paths_to_zip_globalzip_xml, debug=False, max_workers=None):
    """Parse multiple RDF XMLs into a Polars DataFrame with parallel ID cleaning.

    Same as load_all_to_dataframe but stays in Polars (no pandas conversion).
    """
    if debug:
        process_start = datetime.datetime.now()

    if type(list_of_paths_to_zip_globalzip_xml) != list:
        list_of_paths_to_zip_globalzip_xml = [list_of_paths_to_zip_globalzip_xml]

    list_of_xmls = find_all_xml(list_of_paths_to_zip_globalzip_xml, debug)

    data_list = []

    if max_workers:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(load_RDF_to_list_raw, xml, debug) for xml in list_of_xmls]
            results = [future.result() for future in futures]
            data_list = [item for sublist in results for item in sublist]
    else:
        for xml in list_of_xmls:
            data_list.extend(load_RDF_to_list_raw(xml, debug))

    if debug:
        start_time = datetime.datetime.now()

    df = pl.LazyFrame(
        data=data_list,
        schema={"ID": pl.Utf8, "KEY": pl.Utf8, "VALUE": pl.Utf8, "INSTANCE_ID": pl.Utf8},
        orient="row",
    )

    df = df.with_columns(
        _clean_id_column_polars(pl.col("ID")).alias("ID"),
    )

    result = df.collect()

    if debug:
        _print_duration("Total time (Polars native)", process_start)

    return result
