# -------------------------------------------------------------------------------
# Name:        RDF parser - lxml tuned variant
# Purpose:     Same as rdf_parser.py but with all lxml XMLParser options
#              tuned for maximum CIM/RDF parsing performance.
#
# Based on:    rdf_parser.py by kristjan.vilgo
# Variant:     lxml with fully tuned XMLParser settings (benchmark variant)
# -------------------------------------------------------------------------------
import os
from io import BytesIO

from lxml import etree
import pandas
import datetime
import zipfile
import uuid

from concurrent.futures import ThreadPoolExecutor

import logging

logger = logging.getLogger(__name__)


def _print_duration(text, start_time):
    end_time = datetime.datetime.now()
    duration = end_time - start_time
    logger.debug(f"{text}  {duration}")
    return duration, end_time


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


# Pre-build the tuned parser — reusable across calls (XMLParser is reset on each parse())
_TUNED_PARSER = etree.XMLParser(
    huge_tree=True,             # REQUIRED for 4 GB+ files (disables libxml2 security limits)
    collect_ids=False,          # BIGGEST WIN for RDF/CIM (tons of rdf:ID / rdf:about)
    resolve_entities=False,     # Skip entity expansion (rarely needed in CIM RDF)
    remove_blank_text=True,     # Drop ignorable whitespace → smaller tree
    remove_comments=True,       # Drop comments
    remove_pis=True,            # Drop processing instructions
    compact=True,               # Saves memory on short text nodes
    no_network=True,            # No network DTD fetching
    load_dtd=False,             # Never load external DTDs
    dtd_validation=False,       # No validation
    attribute_defaults=False,   # No DTD-driven default attributes
    ns_clean=False,             # Avoid – adds extra post-processing overhead
    recover=False,              # Strict parsing = faster
    strip_cdata=True,           # Keeps CDATA as normal text
)


def load_RDF_objects_from_XML(path_or_fileobject, debug=False):
    """Parse XML with tuned parser settings, return RDF objects iterator."""
    if debug:
        start_time = datetime.datetime.now()

    parsed_xml = etree.parse(path_or_fileobject, parser=_TUNED_PARSER).getroot()

    namespace_map = parsed_xml.nsmap
    namespace_map["xml_base"] = parsed_xml.base

    instance_id = str(uuid.uuid4())

    if debug:
        _, start_time = _print_duration("XML loaded to tree object (tuned parser)", start_time)

    RDF_objects = parsed_xml.iterchildren()

    if debug:
        _, start_time = _print_duration("All children put to a generator", start_time)

    return RDF_objects, instance_id, namespace_map


def load_RDF_to_list(path_or_fileobject, debug=False, keep_ns=False):
    """Parse a single RDF XML file into a triplestore list using tuned lxml."""
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
    KEY_NS = ""
    VALUE_NS = ""

    for RDF_object in RDF_objects:
        ID = clean_ID(RDF_object.attrib.get(RDF_ID) or RDF_object.attrib.get(RDF_ABOUT) or RDF_object.attrib.get(RDF_NODEID))
        KEY = "Type"
        KEY_NS = RDF_NS
        parts = RDF_object.tag.partition("}")
        VALUE_NS, VALUE = parts[0], parts[2]
        data_list.append((ID, KEY, VALUE, INSTANCE_ID))

        for element in RDF_object.iterchildren():
            parts = element.tag.partition("}")
            KEY_NS, KEY = parts[0], parts[2]
            VALUE = element.text
            VALUE_NS = ""

            if VALUE is None and element.attrib:
                VALUE = clean_ID(element.attrib.get(RDF_RESOURCE) or element.attrib.get(RDF_NODEID) or "")

                if VALUE.startswith("http"):
                    VALUE = VALUE.split("#")[-1]

            data_list.append((ID, KEY, VALUE, INSTANCE_ID))

    if debug:
        _print_duration("All values put to data list", start_time)
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


def load_RDF_to_dataframe(path_or_fileobject, debug=False, data_type="string"):
    """Parse a single RDF XML file into a Pandas DataFrame using tuned lxml."""
    data_list = load_RDF_to_list(path_or_fileobject, debug)

    if debug:
        start_time = datetime.datetime.now()

    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"], dtype=data_type)

    if debug:
        _, start_time = _print_duration("List of data loaded to DataFrame", start_time)

    return data


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None):
    """Parse multiple RDF XML files using tuned lxml into a single Pandas DataFrame."""
    if debug:
        process_start = datetime.datetime.now()

    if type(list_of_paths_to_zip_globalzip_xml) != list:
        list_of_paths_to_zip_globalzip_xml = [list_of_paths_to_zip_globalzip_xml]

    list_of_xmls = find_all_xml(list_of_paths_to_zip_globalzip_xml, debug)

    data_list = []

    if max_workers:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(load_RDF_to_list, xml, debug) for xml in list_of_xmls]
            results = [future.result() for future in futures]
            data_list = [item for sublist in results for item in sublist]
    else:
        for xml in list_of_xmls:
            data_list.extend(load_RDF_to_list(xml, debug))

    if debug:
        start_time = datetime.datetime.now()

    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"], dtype=data_type)

    if debug:
        _print_duration("Data list loaded to DataFrame", start_time)
        _print_duration("All loaded in", process_start)

    return data
