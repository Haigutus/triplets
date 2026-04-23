# -------------------------------------------------------------------------------
# Name:        RDF parser - expat variant
# Purpose:     Uses Python's built-in xml.parsers.expat (C-based SAX parser)
#              instead of lxml for XML parsing. Expat is event-driven and
#              avoids building a full DOM tree in memory.
#
# Based on:    rdf_parser.py by kristjan.vilgo
# Variant:     expat XML parser (benchmark variant)
# -------------------------------------------------------------------------------
import os
from io import BytesIO
import xml.parsers.expat as expat

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


class RDFExpatHandler:
    """SAX-style handler for RDF XML parsing using expat.

    Builds the same (ID, KEY, VALUE, INSTANCE_ID) tuple list as lxml variant
    but without constructing a DOM tree.
    """

    def __init__(self, instance_id, file_name):
        self.instance_id = instance_id
        self.file_name = file_name
        self.data_list = []
        self.namespace_map = {}
        self._has_xml_base = False

        # State tracking
        self._depth = 0  # 0=document, 1=rdf:RDF, 2=RDF objects, 3=properties
        self._current_object_id = None
        self._current_property_key = None
        self._current_text = ""
        self._current_property_has_resource = False
        self._rdf_ns = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

        # Add metadata
        meta_id = str(uuid.uuid4())
        nsmap_id = str(uuid.uuid4())
        self.data_list.append((meta_id, "Type", "Distribution", instance_id))
        self.data_list.append((meta_id, "label", file_name, instance_id))
        self._nsmap_id = nsmap_id
        self.data_list.append((nsmap_id, "Type", "NamespaceMap", instance_id))

    def start_namespace(self, prefix, uri):
        """Called for each xmlns declaration."""
        if prefix is None:
            prefix = ""
        self.namespace_map[prefix if prefix else "xml_base"] = uri
        self.data_list.append((self._nsmap_id, prefix if prefix else "xml_base", uri, self.instance_id))

    def start_element(self, name, attrs):
        """Called for each opening tag. name is 'ns_uri localname' if namespace-aware."""
        self._depth += 1

        if self._depth == 1:
            # rdf:RDF root element — capture xml:base if present
            base = attrs.get("http://www.w3.org/XML/1998/namespace base")
            if base:
                self.namespace_map["xml_base"] = base
                self.data_list.append((self._nsmap_id, "xml_base", base, self.instance_id))
                self._has_xml_base = True
            else:
                # Fallback: use file path as xml_base (matches lxml behavior)
                self.data_list.append((self._nsmap_id, "xml_base", self.file_name, self.instance_id))
            return

        if self._depth == 2:
            # RDF object level
            rdf_id = attrs.get(f"{self._rdf_ns} ID")
            rdf_about = attrs.get(f"{self._rdf_ns} about")
            rdf_nodeid = attrs.get(f"{self._rdf_ns} nodeID")

            raw_id = rdf_id or rdf_about or rdf_nodeid
            self._current_object_id = clean_ID(raw_id) if raw_id else None

            # Extract type from tag (namespace localname)
            # expat with namespace gives "ns_uri localname" separated by space
            parts = name.split(" ")
            if len(parts) == 2:
                type_value = parts[1]
            else:
                type_value = name

            self.data_list.append((self._current_object_id, "Type", type_value, self.instance_id))
            return

        if self._depth == 3:
            # Property level
            parts = name.split(" ")
            if len(parts) == 2:
                self._current_property_key = parts[1]
            else:
                self._current_property_key = name

            self._current_text = ""
            self._current_property_has_resource = False

            # Check for rdf:resource or rdf:nodeID attribute
            rdf_resource = attrs.get(f"{self._rdf_ns} resource")
            rdf_nodeid = attrs.get(f"{self._rdf_ns} nodeID")
            ref = rdf_resource or rdf_nodeid

            if ref:
                value = clean_ID(ref)
                # Enumeration handling
                if value.startswith("http"):
                    value = value.split("#")[-1]
                self.data_list.append((self._current_object_id, self._current_property_key, value, self.instance_id))
                self._current_property_has_resource = True

    def char_data(self, data):
        """Called for text content between tags."""
        if self._depth == 3:
            self._current_text += data

    def end_element(self, name):
        """Called for each closing tag."""
        if self._depth == 3 and not self._current_property_has_resource:
            value = self._current_text if self._current_text else None
            self.data_list.append((self._current_object_id, self._current_property_key, value, self.instance_id))

        self._depth -= 1


def load_RDF_to_list(path_or_fileobject, debug=False, keep_ns=False):
    """Parse a single RDF XML file into a triplestore list using expat."""
    file_name = path_or_fileobject if isinstance(path_or_fileobject, str) else path_or_fileobject.name
    logger.info(f"Loading {file_name}")

    if debug:
        start_time = datetime.datetime.now()

    instance_id = str(uuid.uuid4())
    handler = RDFExpatHandler(instance_id, file_name)

    # Create namespace-aware expat parser
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler = handler.start_element
    parser.EndElementHandler = handler.end_element
    parser.CharacterDataHandler = handler.char_data
    parser.StartNamespaceDeclHandler = handler.start_namespace

    # Parse
    if isinstance(path_or_fileobject, str):
        with open(path_or_fileobject, "rb") as f:
            parser.ParseFile(f)
    else:
        path_or_fileobject.seek(0)
        parser.ParseFile(path_or_fileobject)

    if debug:
        _print_duration("expat parsing complete", start_time)

    return handler.data_list


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
    """Parse a single RDF XML file into a Pandas DataFrame using expat."""
    data_list = load_RDF_to_list(path_or_fileobject, debug)

    if debug:
        start_time = datetime.datetime.now()

    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"], dtype=data_type)

    if debug:
        _, start_time = _print_duration("List of data loaded to DataFrame", start_time)

    return data


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None):
    """Parse multiple RDF XML files using expat into a single Pandas DataFrame."""
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
