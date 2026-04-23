# -------------------------------------------------------------------------------
# Name:        RDF parser - pugixml variant
# Purpose:     Uses pugixml Python bindings (direct C++ pugixml wrapper)
#              instead of lxml for XML parsing.
#
# Based on:    rdf_parser.py by kristjan.vilgo
# Variant:     pugixml XML parser (benchmark variant)
# -------------------------------------------------------------------------------
from io import BytesIO
from pugixml import pugi

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
    if not ID:
        return ID
    ID = _remove_prefix(ID, "urn:uuid:")
    ID = _remove_prefix(ID, "#_")
    ID = _remove_prefix(ID, "_")
    return ID


def _split_prefixed_name(name):
    """Split 'prefix:localname' into localname. Returns full name if no prefix."""
    idx = name.find(":")
    if idx >= 0:
        return name[idx + 1:]
    return name


def load_RDF_to_list(path_or_fileobject, debug=False, keep_ns=False):
    """Parse a single RDF XML file into a triplestore list using pugixml."""
    file_name = path_or_fileobject if isinstance(path_or_fileobject, str) else path_or_fileobject.name
    logger.info(f"Loading {file_name}")

    if debug:
        start_time = datetime.datetime.now()

    instance_id = str(uuid.uuid4())

    doc = pugi.XMLDocument()

    if isinstance(path_or_fileobject, str):
        doc.load_file(path_or_fileobject)
    else:
        path_or_fileobject.seek(0)
        content = path_or_fileobject.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        doc.load_string(content)

    if debug:
        _, start_time = _print_duration("pugixml: XML loaded", start_time)

    root = doc.document_element()

    # Metadata
    meta_id = str(uuid.uuid4())
    nsmap_id = str(uuid.uuid4())
    data_list = [
        (meta_id, "Type", "Distribution", instance_id),
        (meta_id, "label", file_name, instance_id),
        (nsmap_id, "Type", "NamespaceMap", instance_id),
    ]

    # Extract namespace map from root attributes
    has_xml_base = False
    for attr in root.attributes():
        aname = attr.name()
        aval = attr.value()
        if aname.startswith("xmlns:"):
            prefix = aname[6:]
            data_list.append((nsmap_id, prefix, aval, instance_id))
        elif aname == "xmlns":
            data_list.append((nsmap_id, "", aval, instance_id))
        elif aname == "xml:base":
            data_list.append((nsmap_id, "xml_base", aval, instance_id))
            has_xml_base = True

    # Fallback: use file path as xml_base (matches lxml behavior)
    if not has_xml_base:
        data_list.append((nsmap_id, "xml_base", file_name, instance_id))

    # Iterate RDF objects
    for rdf_object in root.children():
        # Get ID from rdf:ID, rdf:about, or rdf:nodeID
        # pugixml returns empty string for missing attributes
        raw_id = rdf_object.attribute("rdf:ID").value() or \
                 rdf_object.attribute("rdf:about").value() or \
                 rdf_object.attribute("rdf:nodeID").value()
        ID = clean_ID(raw_id) if raw_id else None

        # Type from tag name
        type_value = _split_prefixed_name(rdf_object.name())
        data_list.append((ID, "Type", type_value, instance_id))

        # Properties
        for element in rdf_object.children():
            KEY = _split_prefixed_name(element.name())
            VALUE = element.child_value()

            if not VALUE:
                # Check for rdf:resource or rdf:nodeID reference
                ref = element.attribute("rdf:resource").value() or \
                      element.attribute("rdf:nodeID").value()
                if ref:
                    VALUE = clean_ID(ref)
                    if VALUE and VALUE.startswith("http"):
                        VALUE = VALUE.split("#")[-1]
                else:
                    VALUE = None

            data_list.append((ID, KEY, VALUE, instance_id))

        rdf_object = rdf_object.next_sibling

    if debug:
        _print_duration("pugixml: All values extracted", start_time)
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
    """Parse a single RDF XML file into a Pandas DataFrame using pugixml."""
    data_list = load_RDF_to_list(path_or_fileobject, debug)

    if debug:
        start_time = datetime.datetime.now()

    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"], dtype=data_type)

    if debug:
        _, start_time = _print_duration("List of data loaded to DataFrame", start_time)

    return data


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None):
    """Parse multiple RDF XML files using pugixml into a single Pandas DataFrame."""
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
