# -------------------------------------------------------------------------------
# Name:        RDF parser
# Purpose:     Loads RDF XMLs from zip and xml files to pandas DataFrame in a triplestore manner
#
# Author:      kristjan.vilgo
#
# Created:     13.12.2018
# Copyright:   (c) kristjan.vilgo 2018
# Licence:     MIT
#
# NOTE: This file is now a thin deprecation shim.  Canonical implementations
#       live in triplets.tools (query/transform) and triplets.export (export).
#       Parser functions that are still the canonical location are kept as-is.
# -------------------------------------------------------------------------------
import warnings
from enum import StrEnum

from lxml import etree

import pandas
import datetime
import uuid

import logging

logger = logging.getLogger(__name__)

# pandas.set_option("display.height", 1000)
pandas.set_option("display.max_rows", 18)
pandas.set_option("display.max_columns", 8)
pandas.set_option("display.width", 1000)

# FUNCTIONS - go down for sample code


def _print_duration(text, start_time):
    """Print duration between now and start time.

    Parameters
    ----------
    text : str
        Description of the timed operation to include in the log message.
    start_time : datetime.datetime
        Start time of the operation.

    Returns
    -------
    tuple
        A tuple containing:
        - duration (timedelta): Time elapsed since start_time.
        - end_time (datetime.datetime): Current time when the function is called.

    Examples
    --------
    >>> start = datetime.datetime.now()
    >>> duration, end = _print_duration("Operation completed", start)
    """
    end_time = datetime.datetime.now()
    duration = end_time - start_time
    logger.debug(f"{text}  {duration}")

    return duration, end_time


def _remove_prefix(original_string, prefix_string):
    """Remove a specified prefix from a string.

    Parameters
    ----------
    original_string : str
        The input string to process.
    prefix_string : str
        The prefix to remove from the input string.

    Returns
    -------
    str
        The input string with the prefix removed if present; otherwise, the original string.

    Examples
    --------
    >>> _remove_prefix("urn:uuid:1234", "urn:uuid:")
    '1234'
    >>> _remove_prefix("abc", "xyz")
    'abc'
    """
    prefix_length = len(prefix_string)

    if original_string[0:prefix_length] == prefix_string:
        return original_string[prefix_length:]

    return original_string


def get_namespace_map(data: pandas.DataFrame):
    """Extract namespace prefix-to-URI mapping and optional xml:base from a triplet dataset.

    Delegates to triplets.tools.get_namespace_map().
    """
    from .tools import get_namespace_map as _fn
    return _fn(data)


def clean_ID(ID):
    """Remove common CIM ID prefixes from a string.

    Parameters
    ----------
    ID : str
        The input ID string to clean.

    Returns
    -------
    str
        The ID with prefixes ('urn:uuid:', '#_', '_') removed from the start.

    Examples
    --------
    >>> clean_ID("urn:uuid:1234")
    '1234'
    >>> clean_ID("#_abc")
    'abc'
    """
    ID = _remove_prefix(ID, "urn:uuid:")
    ID = _remove_prefix(ID, "#_")
    ID = _remove_prefix(ID, "_")

    return ID


def load_RDF_objects_from_XML(path_or_fileobject, debug=False):
    """Parse an XML file and return an iterator of RDF objects with instance ID and namespace map.

    Parameters
    ----------
    path_or_fileobject : str or file-like object
        Path to the XML file or a file-like object containing RDF XML data.
    debug : bool, optional
        If True, log timing information for debugging (default is False).

    Returns
    -------
    tuple
        A tuple containing:
        - RDF_objects (iterator): Iterator over RDF objects in the XML.
        - instance_id (str): Unique UUID for the loaded instance.
        - namespace_map (dict): Dictionary of namespace prefixes and URIs.

    Examples
    --------
    >>> rdf_objects, instance_id, ns_map = load_RDF_objects_from_XML("file.xml")
    """
    # START TIMER
    if debug:
        start_time = datetime.datetime.now()

    # LOAD XML
    parser = etree.XMLParser(remove_comments=True, collect_ids=False, remove_blank_text=True)
    parsed_xml = etree.parse(path_or_fileobject, parser=parser).getroot()  # TODO - add iterparse for Python3

    # Get namespace map
    namesapce_map = parsed_xml.nsmap
    namesapce_map["xml_base"] = parsed_xml.base

    # Get unique ID for loaded instance
    instance_id = str(uuid.uuid4())

    if debug:
        _, start_time = _print_duration("XML loaded to tree object", start_time)

    # EXTRACT RDF OBJECTS
    RDF_objects = parsed_xml.iterchildren()

    if debug:
        _, start_time = _print_duration("All children put to a generator", start_time)

    return RDF_objects, instance_id, namesapce_map


def find_all_xml(list_of_paths_to_zip_globalzip_xml, debug=False):
    """Delegated to triplets.parser.utils. Deprecated: use triplets.parser.find_all_xml()."""
    warnings.warn(
        "rdf_parser.find_all_xml is deprecated, use triplets.parser.find_all_xml()",
        DeprecationWarning, stacklevel=2,
    )
    from .parser import find_all_xml as _find_all
    return _find_all(list_of_paths_to_zip_globalzip_xml, debug=debug)


def load_RDF_to_list(path_or_fileobject, debug=False, keep_ns=False):
    """Parse a single RDF XML file into a triplestore list.

    Parameters
    ----------
    path_or_fileobject : str or file-like object
        Path to the XML file or a file-like object containing RDF XML data.
    debug : bool, optional
        If True, log timing information for debugging (default is False).
    keep_ns : bool, optional
        If True, retain namespace information in the output (default is False, unused).

    Returns
    -------
    list
        List of tuples in the format (ID, KEY, VALUE, INSTANCE_ID) representing the triplestore.

    Examples
    --------
    >>> triples = load_RDF_to_list("file.xml")
    """
    file_name = path_or_fileobject if isinstance(path_or_fileobject, str) else path_or_fileobject.name
    logger.info(f"Loading {file_name}")

    RDF_objects, INSTANCE_ID, namespace_map = load_RDF_objects_from_XML(path_or_fileobject, debug)

    if debug:
        start_time = datetime.datetime.now()

    RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    RDF_ID = f"{{{RDF_NS}}}ID"
    RDF_ABOUT = f"{{{RDF_NS}}}about"
    RDF_RESOURCE = f"{{{RDF_NS}}}resource"

    # Generate list for RDF data and store the original filename under rdf:label in dcat:Distribution object
    ID = str(uuid.uuid4())
    ID_NSMAP = str(uuid.uuid4())
    data_list = [
        (ID, "Type", "Distribution", INSTANCE_ID),
        (ID, "label", file_name, INSTANCE_ID),
        (ID_NSMAP, "Type", "NamespaceMap", INSTANCE_ID),
    ]

    for key, value in namespace_map.items():
        data_list.append((ID_NSMAP, key, value, INSTANCE_ID))

    # Reuse variables to avoid creating new ones in loops
    KEY = ""
    VALUE = ""
    KEY_NS = ""
    VALUE_NS = ""

    for RDF_object in RDF_objects:
        ID = clean_ID(RDF_object.attrib.get(RDF_ID) or RDF_object.attrib.get(RDF_ABOUT))
        KEY = "Type"
        KEY_NS = RDF_NS
        # Use partition instead of split, with fallback for no "}"
        parts = RDF_object.tag.partition("}")
        VALUE_NS, VALUE = parts[0], parts[2]
        data_list.append((ID, KEY, VALUE, INSTANCE_ID))

        for element in RDF_object.iterchildren():
            parts = element.tag.partition("}")
            KEY_NS, KEY = parts[0], parts[2]
            VALUE = element.text
            VALUE_NS = ""

            if VALUE is None and element.attrib:

                # TODO - NB CIM ID specific, to be skipped for generic parsing
                VALUE = clean_ID(element.attrib.get(RDF_RESOURCE, ""))

                # TODO - NB CIM enumeration specific
                if VALUE.startswith("http"):
                    VALUE = VALUE.split("#")[-1]

            data_list.append((ID, KEY, VALUE, INSTANCE_ID))

    if debug:
        _print_duration("All values put to data list", start_time)
    return data_list


def load_RDF_to_dataframe(path_or_fileobject, debug=False, data_type="string"):
    """Parse single file via triplets.parser. Deprecated: use triplets.parser.parse() directly."""
    warnings.warn(
        "load_RDF_to_dataframe is deprecated, use triplets.parser.parse()",
        DeprecationWarning, stacklevel=2,
    )
    from .parser import parse as _parse
    df = _parse(path_or_fileobject, debug=debug, engine="auto", return_type="pandas")
    if data_type and data_type != "string":
        try:
            df = df.astype(data_type)
        except Exception:
            pass
    return df


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None, engine="auto", return_type="pandas", categorical_columns=("INSTANCE_ID", "KEY"), **kw):
    """Parse via triplets.parser. Deprecated: use triplets.parser.parse() directly.

    Supports:
      engine="python_lxml_pandas" | "python_lxml_arrow" | "cython_pugixml_arrow" | "auto"
      return_type="pandas" | "arrow" | "polars"
      max_workers (thread batching over files)
      categorical_columns: tuple of cols to dictionary-encode (default INSTANCE_ID, KEY for memory savings).
    """
    from .parser import parse as _parse
    df = _parse(
        list_of_paths_to_zip_globalzip_xml,
        debug=debug,
        max_workers=max_workers,
        engine=engine,
        return_type=return_type or "pandas",
        categorical_columns=categorical_columns,
        **kw,
    )
    if return_type in (None, "pandas", "df") and data_type and data_type != "string":
        try:
            df = df.astype(data_type)
        except Exception:
            pass
    return df


# Extend this functionality to pandas DataFrame
pandas.read_RDF = load_all_to_dataframe


# =============================================================================
# Tools shims — canonical implementations live in triplets.tools
# =============================================================================

def type_tableview(data, type_name, string_to_number=True, type_key="Type", multivalue=False):
    """Deprecated: use triplets.tools.type_tableview() or data.triplets.type_tableview()"""
    warnings.warn("rdf_parser.type_tableview is deprecated, use triplets.tools.type_tableview()", DeprecationWarning, stacklevel=2)
    from .tools import type_tableview as _fn
    return _fn(data, type_name, string_to_number=string_to_number, type_key=type_key, multivalue=multivalue)


def key_tableview(data, key, string_to_number=True):
    """Deprecated: use triplets.tools.key_tableview() or data.triplets.key_tableview()"""
    warnings.warn("rdf_parser.key_tableview is deprecated, use triplets.tools.key_tableview()", DeprecationWarning, stacklevel=2)
    from .tools import key_tableview as _fn
    return _fn(data, key, string_to_number=string_to_number)


def id_tableview(data, id, string_to_number=True):
    """Deprecated: use triplets.tools.id_tableview() or data.triplets.id_tableview()"""
    warnings.warn("rdf_parser.id_tableview is deprecated, use triplets.tools.id_tableview()", DeprecationWarning, stacklevel=2)
    from .tools import id_tableview as _fn
    return _fn(data, id, string_to_number=string_to_number)


def types_dict(data):
    """Deprecated: use triplets.tools.types_dict() or data.triplets.types_dict()"""
    warnings.warn("rdf_parser.types_dict is deprecated, use triplets.tools.types_dict()", DeprecationWarning, stacklevel=2)
    from .tools import types_dict as _fn
    return _fn(data)


def get_object_data(data, object_UUID):
    """Deprecated: use triplets.tools.get_object_data() or data.triplets.get_object_data()"""
    warnings.warn("rdf_parser.get_object_data is deprecated, use triplets.tools.get_object_data()", DeprecationWarning, stacklevel=2)
    from .tools import get_object_data as _fn
    return _fn(data, object_UUID)


def references_to_simple(data, reference, columns=["Type"]):
    """Deprecated: use triplets.tools.references_to_simple()"""
    warnings.warn("rdf_parser.references_to_simple is deprecated, use triplets.tools.references_to_simple()", DeprecationWarning, stacklevel=2)
    from .tools import references_to_simple as _fn
    return _fn(data, reference, columns=columns)


def references_to(data, reference, levels=1):
    """Deprecated: use triplets.tools.references_to()"""
    warnings.warn("rdf_parser.references_to is deprecated, use triplets.tools.references_to()", DeprecationWarning, stacklevel=2)
    from .tools import references_to as _fn
    return _fn(data, reference, levels=levels)


def references_from_simple(data, reference, columns=["Type"]):
    """Deprecated: use triplets.tools.references_from_simple()"""
    warnings.warn("rdf_parser.references_from_simple is deprecated, use triplets.tools.references_from_simple()", DeprecationWarning, stacklevel=2)
    from .tools import references_from_simple as _fn
    return _fn(data, reference, columns=columns)


def references_from(data, reference, levels=1):
    """Deprecated: use triplets.tools.references_from()"""
    warnings.warn("rdf_parser.references_from is deprecated, use triplets.tools.references_from()", DeprecationWarning, stacklevel=2)
    from .tools import references_from as _fn
    return _fn(data, reference, levels=levels)


def references_all(data):
    """Deprecated: use triplets.tools.references_all()"""
    warnings.warn("rdf_parser.references_all is deprecated, use triplets.tools.references_all()", DeprecationWarning, stacklevel=2)
    from .tools import references_all as _fn
    return _fn(data)


def references_simple(data, reference, columns=None, levels=1):
    """Deprecated: use triplets.tools.references_simple()"""
    warnings.warn("rdf_parser.references_simple is deprecated, use triplets.tools.references_simple()", DeprecationWarning, stacklevel=2)
    from .tools import references_simple as _fn
    return _fn(data, reference, columns=columns, levels=levels)


def references(data, ID, levels=1):
    """Deprecated: use triplets.tools.references()"""
    warnings.warn("rdf_parser.references is deprecated, use triplets.tools.references()", DeprecationWarning, stacklevel=2)
    from .tools import references as _fn
    return _fn(data, ID, levels=levels)


def filter_by_type(data, type_name, type_key="Type"):
    """Deprecated: use triplets.tools.filter_by_type()"""
    warnings.warn("rdf_parser.filter_by_type is deprecated, use triplets.tools.filter_by_type()", DeprecationWarning, stacklevel=2)
    from .tools import filter_by_type as _fn
    return _fn(data, type_name, type_key=type_key)


def filter_by_triplet(data, filter_triplet):
    """Deprecated: use triplets.tools.filter_by_triplet()"""
    warnings.warn("rdf_parser.filter_by_triplet is deprecated, use triplets.tools.filter_by_triplet()", DeprecationWarning, stacklevel=2)
    from .tools import filter_by_triplet as _fn
    return _fn(data, filter_triplet)


def set_VALUE_at_KEY(data, key, value):
    """Deprecated: use triplets.tools.set_VALUE_at_KEY()"""
    warnings.warn("rdf_parser.set_VALUE_at_KEY is deprecated, use triplets.tools.set_VALUE_at_KEY()", DeprecationWarning, stacklevel=2)
    from .tools import set_VALUE_at_KEY as _fn
    return _fn(data, key, value)


def set_VALUE_at_KEY_and_ID(data, key, value, id):
    """Deprecated: use triplets.tools.set_VALUE_at_KEY_and_ID()"""
    warnings.warn("rdf_parser.set_VALUE_at_KEY_and_ID is deprecated, use triplets.tools.set_VALUE_at_KEY_and_ID()", DeprecationWarning, stacklevel=2)
    from .tools import set_VALUE_at_KEY_and_ID as _fn
    return _fn(data, key, value, id)


def triplet_to_tableviews(triplet_df, multivalue=False):
    """Deprecated: use triplets.tools.triplet_to_tableviews()"""
    warnings.warn("rdf_parser.triplet_to_tableviews is deprecated, use triplets.tools.triplet_to_tableviews()", DeprecationWarning, stacklevel=2)
    from .tools import triplet_to_tableviews as _fn
    return _fn(triplet_df, multivalue=multivalue)


def tableviews_to_triplet(tableviews, multivalue=False):
    """Deprecated: use triplets.tools.tableviews_to_triplet()"""
    warnings.warn("rdf_parser.tableviews_to_triplet is deprecated, use triplets.tools.tableviews_to_triplet()", DeprecationWarning, stacklevel=2)
    from .tools import tableviews_to_triplet as _fn
    return _fn(tableviews, multivalue=multivalue)


def tableview_to_triplet(data, multivalue=False):
    """Deprecated: use triplets.tools.tableview_to_triplet()"""
    warnings.warn("rdf_parser.tableview_to_triplet is deprecated, use triplets.tools.tableview_to_triplet()", DeprecationWarning, stacklevel=2)
    from .tools import tableview_to_triplet as _fn
    return _fn(data, multivalue=multivalue)


def update_triplet_from_triplet(data, update_data, update=True, add=True):
    """Deprecated: use triplets.tools.update_triplet_from_triplet()"""
    warnings.warn("rdf_parser.update_triplet_from_triplet is deprecated, use triplets.tools.update_triplet_from_triplet()", DeprecationWarning, stacklevel=2)
    from .tools import update_triplet_from_triplet as _fn
    return _fn(data, update_data, update=update, add=add)


def update_triplet_from_tableview(data, tableview, update=True, add=True, instance_id=None):
    """Deprecated: use triplets.tools.update_triplet_from_tableview()"""
    warnings.warn("rdf_parser.update_triplet_from_tableview is deprecated, use triplets.tools.update_triplet_from_tableview()", DeprecationWarning, stacklevel=2)
    from .tools import update_triplet_from_tableview as _fn
    return _fn(data, tableview, update=update, add=add, instance_id=instance_id)


def remove_triplet_from_triplet(from_triplet, what_triplet, columns=["ID", "KEY", "VALUE"]):
    """Deprecated: use triplets.tools.remove_triplet_from_triplet()"""
    warnings.warn("rdf_parser.remove_triplet_from_triplet is deprecated, use triplets.tools.remove_triplet_from_triplet()", DeprecationWarning, stacklevel=2)
    from .tools import remove_triplet_from_triplet as _fn
    return _fn(from_triplet, what_triplet, columns=columns)


def diff_between_triplet(old_data, new_data):
    """Deprecated: use triplets.tools.diff_between_triplet()"""
    warnings.warn("rdf_parser.diff_between_triplet is deprecated, use triplets.tools.diff_between_triplet()", DeprecationWarning, stacklevel=2)
    from .tools import diff_between_triplet as _fn
    return _fn(old_data, new_data)


def diff_between_INSTANCE(data, INSTANCE_ID_1, INSTANCE_ID_2):
    """Deprecated: use triplets.tools.diff_between_INSTANCE()"""
    warnings.warn("rdf_parser.diff_between_INSTANCE is deprecated, use triplets.tools.diff_between_INSTANCE()", DeprecationWarning, stacklevel=2)
    from .tools import diff_between_INSTANCE as _fn
    return _fn(data, INSTANCE_ID_1, INSTANCE_ID_2)


def print_triplet_diff(old_data, new_data, file_id_object="Distribution", file_id_key="label", exclude_objects=None):
    """Deprecated: use triplets.tools.print_triplet_diff()"""
    warnings.warn("rdf_parser.print_triplet_diff is deprecated, use triplets.tools.print_triplet_diff()", DeprecationWarning, stacklevel=2)
    from .tools import print_triplet_diff as _fn
    return _fn(old_data, new_data, file_id_object=file_id_object, file_id_key=file_id_key, exclude_objects=exclude_objects)


# =============================================================================
# Export shims — canonical implementations live in triplets.export
# =============================================================================

class ExportType(StrEnum):
    XML_PER_INSTANCE = "xml_per_instance"
    XML_PER_INSTANCE_ZIP_PER_ALL = "xml_per_instance_zip_per_all"
    XML_PER_INSTANCE_ZIP_PER_XML = "xml_per_instance_zip_per_xml"


def export_to_excel(data, path=None, multivalue=True, export_to_memory=False, single_file=False, filename=None, apply_formatting=True):
    """Deprecated: use triplets.export.export_to_excel()"""
    warnings.warn("rdf_parser.export_to_excel is deprecated, use triplets.export.export_to_excel()", DeprecationWarning, stacklevel=2)
    from .export import export_to_excel as _fn
    return _fn(data, path=path, multivalue=multivalue, export_to_memory=export_to_memory, single_file=single_file, filename=filename, apply_formatting=apply_formatting)


def export_to_csv(data, path=None, multivalue=True, export_to_memory=False, single_file=False, base_filename=None):
    """Deprecated: use triplets.export.export_to_csv()"""
    warnings.warn("rdf_parser.export_to_csv is deprecated, use triplets.export.export_to_csv()", DeprecationWarning, stacklevel=2)
    from .export import export_to_csv as _fn
    return _fn(data, path=path, multivalue=multivalue, export_to_memory=export_to_memory, single_file=single_file, base_filename=base_filename)


def _get_qname(namespace, tag=None):
    """Deprecated: use triplets.export._get_qname()"""
    warnings.warn("rdf_parser._get_qname is deprecated, use triplets.export._get_qname()", DeprecationWarning, stacklevel=2)
    from .export import _get_qname as _fn
    return _fn(namespace, tag=tag)


def generate_xml(instance_data, rdf_map=None, namespace_map=None, class_KEY="Type", export_undefined=True, comment=None, debug=False):
    """Deprecated: use triplets.export.generate_xml()"""
    warnings.warn("rdf_parser.generate_xml is deprecated, use triplets.export.generate_xml()", DeprecationWarning, stacklevel=2)
    from .export import generate_xml as _fn
    return _fn(instance_data, rdf_map=rdf_map, namespace_map=namespace_map, class_KEY=class_KEY, export_undefined=export_undefined, comment=comment, debug=debug)


def export_to_cimxml(data, rdf_map=None, namespace_map=None, class_KEY="Type", export_undefined=True, export_type=ExportType.XML_PER_INSTANCE_ZIP_PER_XML, global_zip_filename="Export.zip", debug=False, export_to_memory=False, export_base_path="", comment=None, max_workers=None):
    """Deprecated: use triplets.export.export_to_cimxml()"""
    warnings.warn("rdf_parser.export_to_cimxml is deprecated, use triplets.export.export_to_cimxml()", DeprecationWarning, stacklevel=2)
    from .export import export_to_cimxml as _fn
    return _fn(data, rdf_map=rdf_map, namespace_map=namespace_map, class_KEY=class_KEY, export_undefined=export_undefined, export_type=export_type, global_zip_filename=global_zip_filename, debug=debug, export_to_memory=export_to_memory, export_base_path=export_base_path, comment=comment, max_workers=max_workers)


def export_to_networkx(data):
    """Deprecated: use triplets.export.export_to_networkx()"""
    warnings.warn("rdf_parser.export_to_networkx is deprecated, use triplets.export.export_to_networkx()", DeprecationWarning, stacklevel=2)
    from .export import export_to_networkx as _fn
    return _fn(data)


# =============================================================================
# Monkey-patching — backwards compat during transition
# =============================================================================

pandas.DataFrame.type_tableview = type_tableview
pandas.DataFrame.key_tableview = key_tableview
pandas.DataFrame.id_tableview = id_tableview
pandas.DataFrame.types_dict = types_dict
pandas.DataFrame.get_object_data = get_object_data
pandas.DataFrame.references_to_simple = references_to_simple
pandas.DataFrame.references_to = references_to
pandas.DataFrame.references_from_simple = references_from_simple
pandas.DataFrame.references_from = references_from
pandas.DataFrame.references_all = references_all
pandas.DataFrame.references_simple = references_simple
pandas.DataFrame.references = references
pandas.DataFrame.set_VALUE_at_KEY = set_VALUE_at_KEY
pandas.DataFrame.set_VALUE_at_KEY_and_ID = set_VALUE_at_KEY_and_ID
pandas.DataFrame.tableview_to_triplet = tableview_to_triplet
pandas.DataFrame.update_triplet_from_triplet = update_triplet_from_triplet
pandas.DataFrame.update_triplet_from_tableview = update_triplet_from_tableview
pandas.DataFrame.diff_between_INSTANCE = diff_between_INSTANCE
pandas.DataFrame.export_to_excel = export_to_excel
pandas.DataFrame.export_to_csv = export_to_csv
pandas.DataFrame.export_to_cimxml = export_to_cimxml
pandas.DataFrame.export_to_networkx = export_to_networkx
pandas.filter_triplet_by_triplet = filter_by_triplet

# Let's add empty dataframe to keep changes
pandas.DataFrame.changes = pandas.DataFrame()


# TEST AND EXAMPLES
if __name__ == '__main__':

    import sys
    logging.basicConfig(stream=sys.stdout,
                        format='%(levelname) -10s %(asctime)s %(name) -30s %(funcName) -35s %(lineno) -5d: %(message)s',
                        level=logging.DEBUG)

    # When run directly, use absolute imports instead of relative
    from triplets.parser import parse
    path = "../test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"

    # logger is in DEBUG state above; the debug= param is no longer required (auto-detected from logger level)
    data = parse([path])  # exercises new auto debug behavior (no explicit debug=True)

    print("Loaded types")
    print(data.query("KEY == 'Type'")["VALUE"].value_counts())

    print("Example how to get table view of all objects of specified type")
    print(data.type_tableview("ACLineSegment"))

    print("Example how to get objects referring to specified object")
    print(data.references_to_simple("99722373_VL_TN1"))

    print("Example how to get objects that specified object refers to")
    print(data.references_from_simple("99722373_VL_TN1"))
