# -------------------------------------------------------------------------------
# Name:        RDF parser
# Purpose:     Loads RDF XMLs from zip and xml files to pandas DataFrame in a triplestore manner
#
# Author:      kristjan.vilgo
#
# Created:     13.12.2018
# Copyright:   (c) kristjan.vilgo 2018
# Licence:     MIT
# -------------------------------------------------------------------------------
from io import BytesIO
import os

from lxml import etree
from lxml.builder import ElementMaker
from lxml.etree import QName

import pandas
import datetime
import zipfile
import uuid

from collections import OrderedDict
from functools import lru_cache

from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# from collections import deque

# from multiprocessing import Pool - TODO add parallel loading for import ALL DASK, SPARK, MODIN, VAEX

import logging

logger = logging.getLogger(__name__)


# pandas.set_option("display.height", 1000)
pandas.set_option("display.max_rows", 18)
pandas.set_option("display.max_columns", 8)
pandas.set_option("display.width", 1000)


# pandas.set_option('display.max_colwidth', -1)

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

    Notes
    -----
    - Sequentially removes 'urn:uuid:', '#_', and '_' prefixes using removeprefix.
    - TODO: Verify if these characters are absent in UUIDs to ensure safe removal.

    Examples
    --------
    >>> clean_ID("urn:uuid:1234")
    '1234'
    >>> clean_ID("#_abc")
    'abc'
    """
    # TODO - a lot of replacements have been done using replace function, but is it valid that these characters are not present in UUID-s? is replace once sufficent?

    # replace_count = 1 # Remove only once the ID prefix string, otherwise we risk of removing characters from within ID
    # clean_ID      = ID.replace("urn:uuid:", "", replace_count).replace("#_", "", replace_count).replace("_", "", replace_count)
    ID = ID.removeprefix("urn:uuid:")
    ID = ID.removeprefix("#_")
    ID = ID.removeprefix("_")

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
    # instance_id = clean_ID(parsed_xml.find("./").attrib.values()[0]) # Lets asume that the first RDF element describes the whole document - TODO replace it with hash of whole XML
    instance_id = str(uuid.uuid4())  # Guarantees unique ID for each loaded instance of data, in erronous data it happens that same UUID is used for multiple files

    if debug:
        _, start_time = _print_duration("XML loaded to tree object", start_time)

    # EXTRACT RDF OBJECTS
    RDF_objects = parsed_xml.iterchildren()

    if debug:
        _, start_time = _print_duration("All children put to a generator", start_time)

    return RDF_objects, instance_id, namesapce_map


def find_all_xml(list_of_paths_to_zip_globalzip_xml, debug=False):
    """Extract XML files from a list of paths or ZIP archives.

    Parameters
    ----------
    list_of_paths_to_zip_globalzip_xml : list
        List of paths to XML files, ZIP archives, or file-like objects.
    debug : bool, optional
        If True, log file processing details for debugging (default is False).

    Returns
    -------
    list
        List of file-like objects for XML files found in the input paths or ZIPs.

    Notes
    -----
    - Supports XML, RDF, and ZIP files; other file types are logged as unsupported.
    - TODO: Add support for random folders.

    Examples
    --------
    >>> xml_files = find_all_xml(["data.zip", "file.xml"])
    """
    xml_files_list = []
    zip_files_list = []  # TODO - add support random folders as well

    for item in list_of_paths_to_zip_globalzip_xml:

        if type(item) == str:
            item = open(item, "rb")

        item_lower = item.name.lower()

        if ".xml" in item_lower or ".rdf" in item_lower:
            xml_files_list.append(item)

            if debug:
                logger.debug("Added: {}".format(item))

        elif ".zip" in item_lower:
            zip_files_list.append(item)

            if debug:
                logger.debug("Added for further processing: {}".format(item))

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

                if debug:
                    logger.debug("Added: {}".format(zipped_file))

            elif ".zip" in zipped_file_lower:
                zip_files_list.append(BytesIO(zip_container.read(zipped_file)))

                if debug:
                    logger.debug("Added for further processing: {}".format(zipped_file))

            else:
                logger.warning("Not supported file: {}".format(zipped_file))

    return xml_files_list


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
                VALUE = clean_ID(element.attrib[RDF_RESOURCE])

                # TODO - NB CIM enumeration specific
                # TODO - Fix export conf, to include the namespace and enum prefix
                if VALUE.startswith("http"):
                    VALUE = VALUE.split(".")[-1]

            data_list.append((ID, KEY, VALUE, INSTANCE_ID))

    if debug:
        _print_duration("All values put to data list", start_time)
    return data_list


def load_RDF_to_dataframe(path_or_fileobject, debug=False, data_type="string"):
    """Parse a single RDF XML file into a Pandas DataFrame.

    Parameters
    ----------
    path_or_fileobject : str or file-like object
        Path to the XML file or a file-like object containing RDF XML data.
    debug : bool, optional
        If True, log timing information for debugging (default is False).
    data_type : str, optional
        Data type for DataFrame columns (default is 'string').

    Returns
    -------
    pandas.DataFrame
        DataFrame with columns ['ID', 'KEY', 'VALUE', 'INSTANCE_ID'] representing the triplestore.

    Examples
    --------
    >>> df = load_RDF_to_dataframe("file.xml")
    """
    data_list = load_RDF_to_list(path_or_fileobject, debug)

    if debug:
        start_time = datetime.datetime.now()

    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"], dtype=data_type)

    if debug:
        _, start_time = _print_duration("List of data loaded to DataFrame", start_time)

    return data


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None):
    """Parse multiple RDF XML files or ZIP archives into a single Pandas DataFrame.

    Parameters
    ----------
    list_of_paths_to_zip_globalzip_xml : list or str
        List of paths to XML files, ZIP archives, or a single path.
    debug : bool, optional
        If True, log timing information for debugging (default is False).
    data_type : str, optional
        Data type for DataFrame columns (default is 'string').
    max_workers : int, optional
        Number of worker threads for parallel processing (default is None).

    Returns
    -------
    pandas.DataFrame
        DataFrame with columns ['ID', 'KEY', 'VALUE', 'INSTANCE_ID'] containing all parsed data.

    Examples
    --------
    >>> df = load_all_to_dataframe(["data.zip", "file.xml"], max_workers=4)
    """

    # TODO - Support folder

    if debug:
        process_start = datetime.datetime.now()

    # List is expected, but if no list lets assume it is single element list
    if type(list_of_paths_to_zip_globalzip_xml) != list:
        list_of_paths_to_zip_globalzip_xml = [list_of_paths_to_zip_globalzip_xml]

    list_of_xmls = find_all_xml(list_of_paths_to_zip_globalzip_xml, debug)

    data_list = []

    if max_workers:

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit each task individually
            futures = [executor.submit(load_RDF_to_list, xml, debug) for xml in list_of_xmls]
            # Collect results as they complete
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
        _print_duration("All loaded in ", process_start)
        # logger.debug(data.info())

    return data


# Extend this functionality to pandas DataFrame
pandas.read_RDF = load_all_to_dataframe


def type_tableview(data, type_name, string_to_number=True, type_key="Type"):
    """Create a table view of all objects of a specified type.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    type_name : str
        The type of objects to filter (e.g., 'ACLineSegment').
    string_to_number : bool, optional
        If True, convert columns containing numbers to numeric types (default is True).
    type_key : str, optional
        Key used to identify object types in the dataset (default is 'Type').

    Returns
    -------
    pandas.DataFrame or None
        Pivoted DataFrame with IDs as index and keys as columns, or None if no data is found.

    Examples
    --------
    >>> table = data.type_tableview("ACLineSegment")
    """
    # Get all ID-s of rows where Type == type_name
    type_id = data[(data["VALUE"] == type_name) & (data["KEY"] == type_key)]

    if type_id.empty:
        logger.warning('No data available for {}'.format(type_name))
        return None

    # Filter original data by found type_id data
    # There can't be duplicate ID and KEY pairs for pivot, but this will lose data on full model DependantOn and other info, solution would be to use pivot table function.
    type_data = pandas.merge(type_id[["ID"]], data, right_on="ID", left_on="ID").drop_duplicates(["ID", "KEY"])

    # Convert form triplets to a table view all objects of same type
    data_view = type_data.pivot(index="ID", columns="KEY")["VALUE"]

    if string_to_number:
        # Convert to data type to numeric in columns that contain only numbers (for easier data usage later on)
        for column in data_view.columns:
            try:
                data_view[column] = pandas.to_numeric(data_view[column], errors="raise")
            except (ValueError, TypeError):
                pass

    return data_view


# Extend this functionality to pandas DataFrame
pandas.DataFrame.type_tableview = type_tableview


def key_tableview(data, key_name, string_to_number=True):
    """Create a table view of all objects with a specified key.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    key_name : str
        The key to filter objects by (e.g., 'GeneratingUnit.maxOperatingP').
    string_to_number : bool, optional
        If True, convert columns containing numbers to numeric types (default is True).

    Returns
    -------
    pandas.DataFrame or None
        Pivoted DataFrame with IDs as index and keys as columns, or None if no data is found.

    Examples
    --------
    >>> table = data.key_tableview("GeneratingUnit.maxOperatingP")
    """
    # Get all ID-s of rows where KEY == key_name
    type_id = data[data["KEY"] == key_name]

    if type_id.empty:
        logger.warning('No data available for {}'.format(key_name))
        return None

    # Filter original data by found type_id data
    # There can't be duplicate ID and KEY pairs for pivot, but this will lose data on full model DependantOn and other info, solution would be to use pivot table function.
    type_data = pandas.merge(type_id[["ID"]], data, right_on="ID", left_on="ID").drop_duplicates(["ID", "KEY"])

    # Convert form triplets to a table view all objects of same type
    data_view = type_data.pivot(index="ID", columns="KEY")["VALUE"]

    if string_to_number:
        # Convert to data type to numeric in columns that contain only numbers (for easier data usage later on)
        for column in data_view.columns:
            try:
                data_view[column] = pandas.to_numeric(data_view[column], errors="raise")
            except (ValueError, TypeError):
                pass

    return data_view



def id_tableview(data, id, string_to_number=True):
    """Create a tabular view of a CGMES triplet dataset filtered by ID-s.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    id : str or list or pandas.DataFrame
        DataFrame containing IDs to filter by.
    string_to_number : bool, optional
        If True, convert columns containing numbers to numeric types (default is True).


    Returns
    -------
    pandas.DataFrame
        Pivoted DataFrame with IDs as index and KEYs as columns.

    Examples
    --------
    >>> table = id_tableview(data, 'UUID')
    >>> table = id_tableview(data, ['UUID_1', 'UUID_2'])
    >>> table = id_tableview(data, pandas.DataFrame({"ID":['UUID_1', 'UUID_2']})
    """
    if isinstance(id, str):
        id = [id]

    if isinstance(id, list):
        id = pandas.DataFrame({"ID": id})

    id_data = filter_by_triplet(data, id)

    if id_data.empty:
        logger.warning(f'No data available for {id}')
        return None

    data_view = id_data.drop_duplicates(["ID", "KEY"]).pivot(index="ID", columns ="KEY")["VALUE"]

    if string_to_number:
        # Convert to data type to numeric in columns that contain only numbers (for easier data usage later on)
        for column in data_view.columns:
            try:
                data_view[column] = pandas.to_numeric(data_view[column], errors="raise")
            except (ValueError, TypeError):
                pass

    return data_view


# Extend this functionality to pandas DataFrame
pandas.DataFrame.key_tableview = key_tableview


def references_to_simple(data, reference, columns=["Type"]):
    """Create a simplified table view of objects referencing a specified object.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the object to find references to.
    columns : list, optional
        Columns to include in the output table (default is ['Type']).

    Returns
    -------
    pandas.DataFrame
        Pivoted DataFrame with IDs of referencing objects and specified columns.

    Examples
    --------
    >>> table = data.references_to_simple("99722373_VL_TN1")
    """
    reference_data = data.references_to(reference, levels=1).drop_duplicates(["ID_FROM", "KEY"])

    # Convert form triplets to a table view with columns - ID, Type by default
    data_view = reference_data.pivot(index="ID_FROM", columns="KEY")["VALUE"][columns]

    return data_view


# Extend this functionality to pandas DataFrame
pandas.DataFrame.references_to_simple = references_to_simple


def references_to(data, reference, levels=1):
    """Retrieve all objects pointing to a specified reference object.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the reference object.
    levels : int, optional
        Number of reference levels to traverse (default is 1).

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets of objects pointing to the reference, with a 'level' column.

    Notes
    -----
    - TODO: Add the key on which the connection was made.

    Examples
    --------
    >>> refs = data.references_to("99722373_VL_TN1", levels=2)
    """
    # TODO - add the key on which connection was made
    level = 0

    # Get the object itself
    object_data = data.query(f"ID == '{reference}'").copy()
    object_data["level"] = level
    # object_data["ID_TO"] = reference
    # object_data["ID_FROM"] = reference

    # Add object to processing list
    objects_list = [object_data]

    for object_data in objects_list:

        level += 1

        # End loop if we have reached desired level
        if level > levels:
            break

        # Get column where possible reference to other objects reside
        reference_column = object_data[["ID"]]

        # Filter original data VALUE-s by found references ID-s
        reference_data = pandas.merge(reference_column, data,
                                      left_on="ID",
                                      right_on="VALUE",
                                      suffixes=("_TO", "_FROM"))[["ID_TO", "ID_FROM"]].drop_duplicates("ID_FROM")

        if not reference_data.empty:
            referring_objects = pandas.merge(reference_data, data,
                                             left_on="ID_FROM",
                                             right_on="ID")  # .drop(columns=["ID_FROM"])

            # Set object level
            referring_objects["level"] = level

            # Add data for future processing
            objects_list.append(referring_objects)

    return pandas.concat(objects_list)


# Extend this functionality to pandas DataFrame
pandas.DataFrame.references_to = references_to


def references_from_simple(data, reference, columns=["Type"]):
    """Create a simplified table view of objects a specified object refers to.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the object to find references from.
    columns : list, optional
        Columns to include in the output table (default is ['Type']).

    Returns
    -------
    pandas.DataFrame
        Pivoted DataFrame with IDs of referenced objects and specified columns.

    Examples
    --------
    >>> table = data.references_from_simple("99722373_VL_TN1")
    """
    reference_data = data.references_from(reference, levels=1).drop_duplicates(["ID_TO", "KEY"])

    # Convert form triplets to a table view with columns - ID, Type by default
    data_view = reference_data.pivot(index="ID_TO", columns="KEY")["VALUE"][columns]

    return data_view


# Extend this functionality to pandas DataFrame
pandas.DataFrame.references_from_simple = references_from_simple


def references_from(data, reference, levels=1):
    """Retrieve all objects a specified object points to.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the reference object.
    levels : int, optional
        Number of reference levels to traverse (default is 1).

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets of objects referenced by the input, with a 'level' column.

    Notes
    -----
    - TODO: Add the key on which the connection was made.

    Examples
    --------
    >>> refs = data.references_from("99722373_VL_TN1", levels=2)
    """
    # TODO - add the key on which connection was made
    level = 0

    # Get the object itself
    object_data = data.query(f"ID == '{reference}'").copy()
    object_data["level"] = level
    #object_data["ID_TO"] = reference
    #object_data["ID_FROM"] = reference

    # Add object to processing list
    objects_list = [object_data]

    for object_data in objects_list:

        level += 1

        # End loop if we have reached desired level
        if level > levels:
            break

        # Get column where possible reference to other objects reside
        reference_column = object_data[["ID", "VALUE"]]

        # Filter original data ID-s by values form reference object
        reference_data = pandas.merge(reference_column, data,
                                      left_on="VALUE",
                                      right_on="ID",
                                      suffixes=("_FROM", "")).rename(columns={"VALUE_FROM": "ID_TO"})

        if not reference_data.empty:

            # Set object level
            reference_data["level"] = level

            # Add data for future processing
            objects_list.append(reference_data)

    return pandas.concat(objects_list)


# Extend this functionality to pandas DataFrame
pandas.DataFrame.references_from = references_from


def references_all(data):
    """Find all unique references (links) in the dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.

    Returns
    -------
    pandas.DataFrame
        DataFrame with columns ['ID_FROM', 'KEY', 'ID_TO'] representing all references.

    Notes
    -----
    - Does not consider INSTANCE_ID in reference matching.

    Examples
    --------
    >>> refs = data.references_all()
    """
    return data[["ID", "KEY", "VALUE"]].drop_duplicates().merge(data[["ID"]].drop_duplicates(), left_on="VALUE", right_on="ID", suffixes=("_FROM", "_TO"))[["ID_FROM", "KEY", "ID_TO"]]


# Extend this functionality to pandas DataFrame
pandas.DataFrame.references_all = references_all


def references_simple(data, reference, columns=None, levels=1):
    """Create a simplified table view of all references to and from a specified object.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the object to find references for.
    columns : list, optional
        Columns to include in the output table (default is ['Type', 'IdentifiedObject.name'] if available).
    levels : int, optional
        Number of reference levels to traverse (default is 1).

    Returns
    -------
    pandas.DataFrame
        Pivoted DataFrame with IDs, specified columns, and reference levels.

    Examples
    --------
    >>> table = data.references_simple("99722373_VL_TN1", columns=["Type"])
    """
    reference_data = data.references(reference, levels=levels).drop_duplicates(["ID", "KEY"])

    # Convert form triplets to a table view with columns - ID, Type by default
    data_view = reference_data[["ID", "KEY", "VALUE"]].pivot(index="ID", columns="KEY")["VALUE"]

    if not columns:
        columns = []
        available_columns = data_view.columns
        if "Type" in available_columns:
            columns.append("Type")

        if "IdentifiedObject.name" in available_columns:
            columns.append("IdentifiedObject.name")

    return data_view[columns].merge(reference_data[["ID", "level", "ID_FROM", "ID_TO"]], on="ID", how="left").drop_duplicates("ID").sort_values("level")


pandas.DataFrame.references_simple = references_simple


def references(data, ID, levels=1):
    """Retrieve all references (to and from) a specified object.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    ID : str
        ID of the object to find references for.
    levels : int, optional
        Number of reference levels to traverse (default is 1).

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets of all references to and from the object.

    Examples
    --------
    >>> refs = data.references("99722373_VL_TN1", levels=2)
    """
    FROM = data.references_from(ID, levels)
    TO = data.references_to(ID, levels)
    return pandas.concat([FROM, TO]).drop_duplicates(["ID", "KEY", "VALUE", "INSTANCE_ID"])


pandas.DataFrame.references = references


# def references(data, ID, levels=1):
#     all_references = data.references_all()
#     refrences_list = []
#
#     references = all_references.query("ID_FROM == @ID or ID_TO==@ID").copy()
#     references["level"] = 0
#     refrences_list.append(references)
#
#     level = 1
#
#     while levels > level:
#
#         previous_references = refrences_list[level-1][['ID_FROM', 'KEY', 'ID_TO']]
#
#         FROM = all_references.merge(previous_references["ID_FROM"]).drop_duplicates()
#         TO = all_references.merge(previous_references["ID_TO"]).drop_duplicates()
#         FROM_and_TO = pandas.concat([FROM, TO]).drop_duplicates()
#
#         new_references = pandas.concat([FROM_and_TO, previous_references]).drop_duplicates(keep=False)
#         new_references["level"] = level
#         refrences_list.append(new_references)
#
#         level += 1
#
#     return pandas.concat(refrences_list, ignore_index=True)
# pandas.DataFrame.references = references()


def types_dict(data):
    """Return a dictionary of object types and their occurrence counts.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.

    Returns
    -------
    dict
        Dictionary with object types as keys and their counts as values.

    Examples
    --------
    >>> types = data.types_dict()
    >>> print(types)
    {'ACLineSegment': 10, 'PowerTransformer': 5, ...}
    """
    types_dictionary = data[(data.KEY == "Type")]["VALUE"].value_counts().to_dict()

    return types_dictionary


# Extend this functionality to pandas DataFrame
pandas.DataFrame.types_dict = types_dict


def set_VALUE_at_KEY(data, key, value):
    """Set the value for all instances of a specified key.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    key : str
        The key to update.
    value : str
        The new value to set for the specified key.

    Notes
    -----
    - TODO: Add debug logging for key, initial value, and new value.
    - TODO: Store changes in a changes DataFrame.

    Examples
    --------
    >>> data.set_VALUE_at_KEY("label", "new_label")
    """
    data.loc[data[data.KEY == key].index, "VALUE"] = value  # TODO add changes to change DataFrame


# Extend this functionality to pandas DataFrame
pandas.DataFrame.set_VALUE_at_KEY = set_VALUE_at_KEY


def set_VALUE_at_KEY_and_ID(data, key, value, id):
    """Set the value for a specific key and ID.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    key : str
        The key to update.
    value : str
        The new value to set.
    id : str
        The ID of the object to update.

    Examples
    --------
    >>> data.set_VALUE_at_KEY_and_ID("label", "new_label", "uuid1")
    """
    data.loc[data[(data.ID == id) & (data.KEY == key)].index, "VALUE"] = value


# Extend this functionality to pandas DataFrame
pandas.DataFrame.set_VALUE_at_KEY_and_ID = set_VALUE_at_KEY_and_ID

# TODO - add CSV export
def export_to_excel(data, path=None):
    """Export triplet data to an Excel file, with each type on a separate sheet.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    path : str, optional
        Directory path to save the Excel file (default is current working directory).

    Notes
    -----
    - Uses 'label' key to determine the filename for each INSTANCE_ID.
    - Each object type is exported to a separate sheet.
    - TODO: Add support for XlsxWriter properties for better formatting.

    Examples
    --------
    >>> data.export_to_excel("output_dir")
    """
    # TODO set some nice properties - https://xlsxwriter.readthedocs.io/workbook.html#workbook-set-properties

    labels = data.query("KEY == 'label'").iterrows()
    # TODO dont use iterrows
    # TODO instead of label use Distribution
    for _, label in labels:
        instance_data = data[data.INSTANCE_ID == label.INSTANCE_ID]

        types = instance_data.types_dict()

        file_name = '{}.xlsx'.format(label.VALUE.split(".")[0])

        if path is None:
            path = os.getcwd()

        file_path = os.path.join(path, file_name)

        logger.info("Exporting excel: {}".format(file_path))
        writer = pandas.ExcelWriter(file_path)

        for class_type in types:
            class_data = instance_data.type_tableview(class_type)
            class_data.to_excel(writer, class_type)

            # Get sheet to do some formatting
            sheet = writer.sheets[class_type]

            # Set default column size, if this does not work you are missing XslxWriter module
            first_col = 0
            last_col = len(class_data.columns)
            width = 38
            sheet.set_column(first_col, last_col, width)

            # freeze column names and ID column
            sheet.freeze_panes(1, 1)

        writer.save()


# Extend this functionality to pandas DataFrame
pandas.DataFrame.export_to_excel = export_to_excel


@lru_cache(maxsize=250)  # Adjust maxsize based on the number of unique QName combinations
def _get_qname(namespace, tag=None):
    """Generate a QName for a given namespace and tag, with caching.

    Parameters
    ----------
    namespace : str
        The namespace URI.
    tag : str, optional
        The tag name (default is None).

    Returns
    -------
    lxml.etree.QName
        The qualified name object for the namespace and tag.

    Examples
    --------
    >>> qname = _get_qname("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "RDF")
    """
    qname = QName(namespace, tag)
    #logger.debug(f"Cache info: {get_qname.cache_info()}")
    return qname


def generate_xml(instance_data,
                 rdf_map=None,
                 namespace_map={"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"},
                 class_KEY="Type",
                 export_undefined=True,
                 comment=None,
                 debug=False):
    """Generate an RDF XML file from a triplet dataset instance.

    Parameters
    ----------
    instance_data : pandas.DataFrame
        Triplet dataset for a single instance.
    rdf_map : dict, optional
        Dictionary mapping classes and keys to RDF namespaces and attributes.
    namespace_map : dict, optional
        Dictionary of namespace prefixes and URIs (default includes RDF namespace).
    class_KEY : str, optional
        Key used to identify object types (default is 'Type').
    export_undefined : bool, optional
        If True, export undefined classes and tags with default settings (default is True).
    comment : str, optional
        Comment to include in the XML output (default is None).
    debug : bool, optional
        If True, log timing information for debugging (default is False).

    Returns
    -------
    dict
        Dictionary with 'filename' (str) and 'file' (bytes) containing the XML output.

    Examples
    --------
    >>> xml_data = generate_xml(instance_data, rdf_map, namespace_map)
    """
    # TODO - Use if logger debug
    if debug:
        start_time = datetime.datetime.now()

    # Filename is kept under label
    label_data = instance_data[instance_data["KEY"] == "label"]

    if not label_data.empty:
        file_name = label_data.at[label_data.index[0], 'VALUE']

    else:
        file_name = f"{uuid.uuid4()}.xml"

    # Find schema reference to be used for export
    # TODO remove dependency on this header field, which might not be present
    # TODO: Refactor this, if schema is provided, the information how to pick it up should be in the schema

    instance_type = None

    message_type_data = instance_data[instance_data["KEY"] == "Model.messageType"]
    profile_data = instance_data[instance_data["KEY"] == "Model.profile"]

    if not message_type_data.empty:
        instance_type = message_type_data.at[message_type_data.index[0], 'VALUE']

    if not instance_type and not profile_data.empty:
        instance_type_url = profile_data.at[profile_data.index[0], 'VALUE']

        # TODO - needs to be extended
        profile_map = {
            "Equipment": "EQ",
            "SteadyState": "SSH",
            "StateVariables": "SV",
            "Topology": "TP"
        }

        for key, value in profile_map.items():
            if key in instance_type_url:
                instance_type = value
                continue

    # If there is sub structure available in schema get it, otherwise use root definitions
    # TODO - needs revision, add support both for md:FullModel, dcat:DataSet and without profile definiton
    instance_rdf_map = rdf_map.get(instance_type, rdf_map)

    if instance_rdf_map is None:
        logger.warning("No rdf mapping available for {}".format(instance_type))
        if not export_undefined:
            logger.warning("File not created for {}".format(file_name))
            return

    # Create element builder
    E = ElementMaker(nsmap=namespace_map)

    # Create xml root element
    RDF = E(QName(namespace_map["rdf"], "RDF"))

    # Add comment
    if comment:
        RDF.addprevious(etree.Comment(str(comment)))

    # Store created xml rdf class elements
    objects = {}
    # TODO ensure that the Header class is serialised first

    if debug:
        _, start_time = _print_duration("File root generated", start_time)

    # Generate objects (Class instance)
    # TODO: Maybe group by class name: less lookups
    for class_data in (instance_data[instance_data["KEY"] == class_KEY]).itertuples():

        ID = class_data.ID
        class_name = class_data.VALUE

        # Get class export definition
        class_def = instance_rdf_map.get(class_name, None)

        if class_def is not None:

            class_namespace = class_def["namespace"]
            id_name = class_def["attrib"]["attribute"]
            id_value_prefix = class_def["attrib"]["value_prefix"]

        else:
            logger.debug("Definition missing for class: {} with {}: ".format(class_name, ID))

            if export_undefined:
                class_namespace = None
                id_name = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"
                id_value_prefix = "urn:uuid:"
            else:
                logger.debug(f"{class_name} not Exported")
                continue

        # Create class element
        # logger.debug(class_namespace, class_name) # DEBUG
        rdf_object = E(_get_qname(class_namespace, class_name))
        # Add ID attribute
        rdf_object.attrib[_get_qname(id_name)] = f"{id_value_prefix}{ID}"
        # Add object to RDF
        RDF.append(rdf_object)
        # Add object with it's ID to dict (later we use it to add attributes to that class)
        objects[ID] = rdf_object

    if debug:
        _, start_time = _print_duration("Objects added", start_time)

    # Add attribute to objects
    # TODO - maybe make work que here, all Objects are generated, we now need to just add attributes to them, but we are going object by object
    # TODO - maybe group by KEY: avoid all prefix building lookups
    # TODO - maybe filter out all rows without Type before: avoid checking in the loop
    for attribute_data in instance_data[instance_data["KEY"] != class_KEY].dropna(subset=["VALUE"]).itertuples():
    #for attribute_data in instance_data[instance_data["KEY"] != class_KEY].itertuples():

        ID = attribute_data.ID
        KEY = attribute_data.KEY
        VALUE = attribute_data.VALUE

        _object = objects.get(ID, None)

        if _object is not None:

        #if not pandas.isna(VALUE):

            tag_def = instance_rdf_map.get(KEY, None)

            if tag_def is not None:
                tag = E(_get_qname(tag_def["namespace"], KEY))
                attrib = tag_def.get("attrib", None)
                text_prefix = tag_def.get("text", "")

                if attrib:
                    tag.attrib[_get_qname(attrib["attribute"])] = f"{attrib['value_prefix']}{VALUE}"
                else:
                    tag.text = f"{text_prefix}{VALUE}"

                _object.append(tag)

            else:
                logger.debug("Definition missing for tag: " + KEY)

                if export_undefined:
                    tag = E(KEY)
                    tag.text = str(VALUE)

                    _object.append(tag)


        #else:
        #    logger.debug("Attribute VALUE is None, thus not exported: ID: {} KEY: {}".format(ID, KEY))
        #    pass

        else:
            logger.debug("No Object with ID: {}".format(ID))
            pass

    # etree.tostring(RDF, pretty_print=True, xml_declaration=True, encoding='UTF-8')
    # logger.debug(etree.tostring(RDF, pretty_print=True).decode())
    if debug:
        _, start_time = _print_duration("Attributes added", start_time)

    # Convert to XML
    xml = etree.tostring(RDF, pretty_print=True, xml_declaration=True, encoding='UTF-8')
    # TODO - clean namespaces

    logger.info("Exporting RDF to {}".format(file_name))

    if debug:
        _, start_time = _print_duration("XML created", start_time)

    return {"filename": file_name, "file": xml}


def export_to_cimxml(data, rdf_map=None, namespace_map={"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"},
                     class_KEY="Type",
                     export_undefined=True,
                     export_type="xml_per_instance_zip_per_xml",
                     global_zip_filename="Export.zip",
                     debug=False,
                     export_to_memory=False,
                     export_base_path="",
                     comment=None,
                     max_workers=None):
    """Export a triplet dataset to CIM RDF XML or ZIP files.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    rdf_map : dict, optional
        Dictionary mapping classes and keys to RDF namespaces and attributes.
    namespace_map : dict, optional
        Dictionary of namespace prefixes and URIs (default includes RDF namespace).
    class_KEY : str, optional
        Key used to identify object types (default is 'Type').
    export_undefined : bool, optional
        If True, export undefined classes and tags with default settings (default is True).
    export_type : str, optional
        Export format: 'xml_per_instance', 'xml_per_instance_zip_per_all', or
        'xml_per_instance_zip_per_xml' (default is 'xml_per_instance_zip_per_xml').
    global_zip_filename : str, optional
        Filename for the global ZIP archive (default is 'Export.zip').
    debug : bool, optional
        If True, log timing information for debugging (default is False).
    export_to_memory : bool, optional
        If True, return file objects in memory; otherwise, save to disk (default is False).
    export_base_path : str, optional
        Directory path to save exported files (default is empty, uses current directory).
    comment : str, optional
        Comment to include in the XML output (default is None).
    max_workers : int, optional
        Number of worker processes for parallel processing (default is None).

    Returns
    -------
    list
        List of file-like objects (if export_to_memory=True) or filenames (if export_to_memory=False).

    Examples
    --------
    >>> files = data.export_to_cimxml(rdf_map, export_type="xml_per_instance")
    """
    if debug:
        start_time = datetime.datetime.now()
        init_time = start_time

    instances = data.groupby("INSTANCE_ID")

    #TODO - this needs to be extended and put to a better place
    #TODO - maybe rdfmap should use direct url, instead of short keys "EQ" etc

    # Keep all file names and data to be exported
    xml_documents = []

    if debug:
        _, start_time = _print_duration("All file instance ID-s identified", start_time)

    # if max_workers:
    #     with ProcessPoolExecutor(max_workers=max_workers) as executor:
    #         # Map the function to the XML list and accumulate results
    #         xml_documents = executor.map(lambda _, instance: generate_xml(   instance,
    #                                                                          rdf_map,
    #                                                                          namespace_map,
    #                                                                          class_KEY,
    #                                                                          export_undefined,
    #                                                                          comment,
    #                                                                          debug), instances)
    if max_workers:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(generate_xml, instance, rdf_map, namespace_map, class_KEY, export_undefined,debug) for _, instance in instances]
            xml_documents = [future.result() for future in futures if future.result() is not None]

    else:
        for _, instance in instances:
            xml_documents.append(generate_xml(instance,
                                             rdf_map,
                                             namespace_map,
                                             class_KEY,
                                             export_undefined,
                                             comment,
                                             debug))
    if debug:
        _, start_time = _print_duration("All XML created in memory ", start_time)
    ### Export XML ###
    exported_files = []

    if export_type == "xml_per_instance":
        for document in xml_documents:

            file_object = BytesIO(document["file"])
            file_object.name = document["filename"]

            exported_files.append(file_object)

            logger.info(f"Exported {document['filename']} to memory")

    ### Export ZIP containing all xml ###
    elif export_type == "xml_per_instance_zip_per_all":

        gloabl_zip_fileobject = BytesIO()
        gloabl_zip_fileobject.name = global_zip_filename

        with zipfile.ZipFile(gloabl_zip_fileobject, "a", zipfile.ZIP_DEFLATED, False) as zip_file:

            for document in xml_documents:
                zip_file.writestr(document["filename"], document["file"])
                logger.info(f'Added {document["filename"]} to ZIP')

        exported_files.append(gloabl_zip_fileobject)
        logger.info(f'Exported ZIP named {global_zip_filename} to memory')


    ### Export each xml in separate zip ###
    elif export_type == "xml_per_instance_zip_per_xml":

        for document in xml_documents:

            zip_file_object = BytesIO()
            zip_file_object.name = document["filename"].replace('.xml', '.zip').replace('.XML', '.zip')

            with zipfile.ZipFile(zip_file_object, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.writestr(document["filename"], document["file"])

            exported_files.append(zip_file_object)
            logger.info(f'Exported {zip_file_object.name} to memory')

    else:
        logger.info("Not supported option")
        logger.info("Supported options are: xml_per_instance, xml_per_instance_zip_per_all, xml_per_instance_zip_per_xml")

    if debug:
        _print_duration("Files saved in", start_time)
        _print_duration("Whole Export done in", init_time)

    # Save files to disk
    if export_to_memory:
        return exported_files

    else:
        exported_file_names = []

        for file_object in exported_files:
            export_path = os.path.join(export_base_path, file_object.name)
            with open(export_path, 'wb') as export_file_object:

                # Ensure that the read pointer is at the start of the file
                file_object.seek(0)
                export_file_object.write(file_object.read())

            exported_file_names.append(file_object.name)
            logger.info(f'Saved {export_path}')

        return exported_file_names


# Extend this functionality to pandas DataFrame
pandas.DataFrame.export_to_cimxml = export_to_cimxml


def get_object_data(data, object_UUID):
    """Retrieve data for a specific object by its UUID.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    object_UUID : str
        UUID of the object to retrieve.

    Returns
    -------
    pandas.Series
        Series with keys as index and values for the specified object.

    Examples
    --------
    >>> obj_data = data.get_object_data("uuid1")
    """
    return data.query("ID == '{}'".format(object_UUID)).set_index("KEY")["VALUE"]


pandas.DataFrame.get_object_data = get_object_data


def tableview_to_triplet(data):
    """Convert a table view back to a triplet format.

    Parameters
    ----------
    data : pandas.DataFrame
        Pivoted DataFrame (table view) to convert.

    Returns
    -------
    pandas.DataFrame
        Triplet DataFrame with columns ['ID', 'KEY', 'VALUE'].

    Notes
    -----
    - TODO: Ensure this is only used on valid table views.

    Examples
    --------
    >>> triplet = tableview_to_triplet(table_view)
    """
    # TODO add only when tableveiw is created
    return data.reset_index().melt(id_vars="ID", value_name="VALUE", var_name="KEY")


pandas.DataFrame.tableview_to_triplet = tableview_to_triplet


# Let's add empty dataframe to keep changes
pandas.DataFrame.changes = pandas.DataFrame()


def update_triplet_from_triplet(data, update_data, update=True, add=True):
    """Update or add triplets from another triplet dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Original triplet dataset to update.
    update_data : pandas.DataFrame
        Triplet dataset containing updates or new data.
    update : bool, optional
        If True, update existing ID-KEY pairs (default is True).
    add : bool, optional
        If True, add new ID-KEY pairs (default is True).

    Returns
    -------
    pandas.DataFrame
        Updated triplet dataset.

    Notes
    -----
    - TODO: Add a changes DataFrame to track modifications.
    - TODO: Support updating ID and KEY fields.

    Examples
    --------
    >>> updated_data = data.update_triplet_from_triplet(update_data)
    """
    write_columns = ["ID", "KEY", "VALUE", "INSTANCE_ID"]

    # Choose what columns to use for final merge
    merge_columns = ["ID", "KEY"]
    if "INSTANCE_ID" in update_data.columns:
        merge_columns = ["ID", "KEY", "INSTANCE_ID"]

    # First reset index to be sure that data does not have duplicated keys
    data = data.reset_index(drop=True)

    # Make merge to see what updated data already exists in old and what needs to be added
    changes = data.reset_index(names="original_index").merge(update_data, on=merge_columns, how='right', indicator=True, suffixes=("_OLD", ""), sort=False)

    if update:
        # Filter data that needs to be updated
        data_to_update = changes.query("_merge == 'both'").drop_duplicates(subset="original_index")
        data.iloc[data_to_update["original_index"].astype(int)] = data_to_update[write_columns]

    if add:
        # Filter data that needs to be added
        data_to_add = changes.query("_merge == 'right_only'")[write_columns]
        data = pandas.concat([data, data_to_add]).drop_duplicates(keep='last', ignore_index=True)

    return data


pandas.DataFrame.update_triplet_from_triplet = update_triplet_from_triplet


def update_triplet_from_tableview(data, tableview, update=True, add=True, instance_id=None):
    """Update or add triplets from a table view.

    Parameters
    ----------
    data : pandas.DataFrame
        Original triplet dataset to update.
    tableview : pandas.DataFrame
        Table view containing updates or new data.
    update : bool, optional
        If True, update existing ID-KEY pairs (default is True).
    add : bool, optional
        If True, add new ID-KEY pairs (default is True).
    instance_id : str, optional
        Instance ID to assign to new triplets (default is None).

    Returns
    -------
    pandas.DataFrame
        Updated triplet dataset.

    Examples
    --------
    >>> updated_data = data.update_triplet_from_tableview(table_view, instance_id="uuid1")
    """
    update_triplet = tableview.tableview_to_triplet()

    if instance_id:
        update_triplet["INSTANCE_ID"] = instance_id

    return update_triplet_from_triplet(data, update_triplet, update, add)


pandas.DataFrame.update_triplet_from_tableview = update_triplet_from_tableview


def remove_triplet_from_triplet(from_triplet, what_triplet, columns=["ID", "KEY", "VALUE"]):
    """Remove triplets from one dataset that match another.

    Parameters
    ----------
    from_triplet : pandas.DataFrame
        Original triplet dataset.
    what_triplet : pandas.DataFrame
        Triplet dataset to remove from the original.
    columns : list, optional
        Columns to match for removal (default is ['ID', 'KEY', 'VALUE']).

    Returns
    -------
    pandas.DataFrame
        Dataset with matching triplets removed.

    Examples
    --------
    >>> result = remove_triplet_from_triplet(data, to_remove)
    """
    return from_triplet.drop(from_triplet.reset_index().merge(what_triplet[columns], on=columns, how="inner")["index"], axis=0)


def filter_by_triplet(data, filter_triplet):
    """Filter riplet DataFrame using IDs from another DataFrame.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    filter_triplet : pandas.DataFrame
        DataFrame containing atleast colum ID to filter by.

    Returns
    -------
    pandas.DataFrame
        Filtered DataFrame with columns ['ID, 'KEY', 'VALUE', 'INSTANCE_ID'].

    Examples
    --------
    >>> filtered = filter_by_triplet(data, filter_triplet)
    """

    return data.merge(filter_triplet[["ID"]], on="ID", how="inner")

pandas.filter_triplet_by_triplet = filter_by_triplet


def filter_by_type(data, type_name, type_key="Type"):
    """Filter triplet dataset by objects of a specific type.

    Parameters
    ----------
    triplet : pandas.DataFrame
        Triplet dataset containing RDF data.
    type : str
        Object type to filter by (e.g., 'ACLineSegment').

    Returns
    -------
    pandas.DataFrame
        Filtered triplet dataset containing only objects of the specified type.

    Examples
    --------
    >>> filtered = filter_triplet_by_type(data, "ACLineSegment")
    """
    return triplet.merge(triplet.query("KEY == 'Type' and VALUE == @type").ID)


def diff_between_triplet(old_data, new_data):
    """Compute the difference between two Triplet DataFrames.

    Parameters
    ----------
    old_data : pandas.DataFrame
        Original triplet dataset.
    new_data : pandas.DataFrame
        New triplet dataset to compare against.

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets unique to old_data or new_data, with an '_merge' column
        indicating 'left_only' (in old_data) or 'right_only' (in new_data).

    Examples
    --------
    >>> diff = diff_between_triplet(old_data, new_data)
    """
    return old_data.merge(new_data, on=["ID", "KEY", "VALUE"], how='outer', indicator=True, suffixes=("_OLD", "_NEW"), sort=False).query("_merge != 'both'")

def diff_between_INSTANCE(data, INSTANCE_ID_1, INSTANCE_ID_2):
    """Identify differences between two loaded INSTANCES, by thier INSTACE_ID in the same Triplet DataFrame.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing two or more INSTANCE.
    INSTANCE_ID_1 : str
        UUID of the first INSTANCE.
    INSTANCE_ID_2 : str
        UUID of the second INSTANCE.

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets that differ between the two model parts.

    Examples
    --------
    >>> diff = diff_between_INSTANCE('uuid1', 'uuid2')
    """
    diff = data.query("INSTANCE_ID == '{}' or INSTANCE_ID == '{}'".format(INSTANCE_ID_1, INSTANCE_ID_2)).drop_duplicates(["ID", "KEY", "VALUE"], keep=False)

    return diff

pandas.DataFrame.diff_between_INSTANCE = diff_between_INSTANCE

def print_triplet_diff(old_data, new_data, file_id_object="Distribution", file_id_key="label", exclude_objects=None):
    """Print a human-readable diff of two triplet datasets.

    Parameters
    ----------
    old_data : pandas.DataFrame
        Original triplet dataset.
    new_data : pandas.DataFrame
        New triplet dataset to compare against.
    file_id_object : str, optional
        Object type containing file identifiers (default is 'Distribution').
    file_id_key : str, optional
        Key containing file identifiers (default is 'label').
    exclude_objects : list, optional
        List of object types to exclude from the diff (default is None).

    Notes
    -----
    - Outputs a diff format showing removed, added, and changed objects.
    - Nice diff viewer https://diffy.org/
    - TODO: Add name field for better reporting with Type.

    Examples
    --------
    >>> print_triplet_diff(old_data, new_data, exclude_objects=["NamespaceMap"])
    """
    # Get diff between datasets
    diff = diff_between_triplet(old_data, new_data)
    diff = diff.replace({'_merge': {"left_only": "-", "right_only": "+"}}).sort_values(by=['ID', 'KEY'])

    # Extract internal structures keeping file name information
    file_id_data = filter_triplet_by_type(diff, file_id_object)
    diff = remove_triplet_from_triplet(diff, file_id_data)
    logger.info(f"INFO - removed {file_id_object} from diff")

    # Exclude defined types form export
    if exclude_objects:
        for object_name in exclude_objects:
            excluded_data = filter_triplet_by_type(diff, object_name)
            diff = remove_triplet_from_triplet(diff, excluded_data)
            print(f"INFO - removed {object_name} from diff")

    # Extract types on left and right to get changed/modified types
    removed_added_modified_types = pandas.concat([
        old_data.merge(diff["ID"]).query("KEY == 'Type'").drop_duplicates(),
        new_data.merge(diff["ID"]).query("KEY == 'Type'").drop_duplicates()
        ])[["ID", "KEY", "VALUE"]].drop_duplicates()

    # Print old file name
    for _, file_id in file_id_data.query(f"KEY == '{file_id_key}' and _merge == '-'").VALUE.items():
        print(f"--- {file_id}")# from-file-modification-time")

    # Print new file name
    for _, file_id in file_id_data.query(f"KEY == '{file_id_key}' and _merge == '+'").VALUE.items():
        print(f"+++ {file_id}")# to-file-modification-time")

    # Print changes

    print("")
    print(f"@@ -1,0 +1,0 @@ Removed:")

    for key, value in diff.query("KEY == 'Type' and _merge == '-'").VALUE.value_counts().items():
        print(" ", key, value)

    print("")
    print(f"@@ -1,0 +1,0 @@ Added:")
    for key, value in diff.query("KEY == 'Type' and _merge == '+'").VALUE.value_counts().items():
        print(" ", key, value)

    print("")
    print(f"@@ -1,0 +1,0 @@ Changed:")
    for key, value in pandas.concat([removed_added_modified_types, diff.query("KEY == 'Type'")])[["ID", "KEY", "VALUE"]].drop_duplicates(keep=False).VALUE.value_counts().items():
        print(" ", key, value)

    # Types changed
    # TODO add name field to be used with Type for better reporting

    for group_name, group in removed_added_modified_types.groupby("VALUE"):
        #print(f"Types - {group_name}")
        for objec_type in group.itertuples():

            current_diff = diff.query("ID == @objec_type.ID")

            changes_on_left = len(current_diff.query("_merge == '-'"))
            changes_on_right = len(current_diff.query("_merge == '+'"))
            print("")
            print(f"@@ -1,{changes_on_left} +1,{changes_on_right} @@ {objec_type.VALUE} {objec_type.ID}")

            for _, change in (current_diff._merge.astype(str) + current_diff.KEY.astype(str) + " -> " + current_diff.VALUE.astype(str)).items():
                print(change)

    # Nice diff viewer https://diffy.org/

# changes = changes.replace({'_merge': {"left_only": "-", "right_only": "+"}})


def export_to_networkx(data):
    """Convert a triplet dataset to a NetworkX graph.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.

    Returns
    -------
    networkx.Graph
        A NetworkX graph with nodes (IDs with Type attributes) and edges (references).

    Notes
    -----
    - TODO: Add all node data and support additional graph export formats.

    Examples
    --------
    >>> graph = data.to_networkx()
    """
    import networkx

    #  TODO - Add all node data
    #  TODO - Add all supported graph export formats

    edges = data.references_all()
    nodes = data[["ID", "KEY", "VALUE"]].drop_duplicates().query("KEY == 'Type'")[["ID", "VALUE"]]

    graph = networkx.Graph()

    graph.add_nodes_from([(ID, {"Type": VALUE}) for ID, VALUE in nodes.values])
    graph.add_edges_from([(FROM, TO, {"Type": KEY}) for FROM, KEY, TO in edges.values])

    return graph


pandas.DataFrame.to_networkx = export_to_networkx


# END OF FUNCTIONS


# TEST AND EXAMPLES
if __name__ == '__main__':

    import sys
    logging.basicConfig(stream=sys.stdout,
                        format='%(levelname) -10s %(asctime)s %(name) -30s %(funcName) -35s %(lineno) -5d: %(message)s',
                        level=logging.DEBUG)

    path = "../test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"

    data = pandas.read_RDF([path], debug=True)
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

    # model = "FlowExample.zip"
    #
    # rdfs = "C:\Users\kristjan.vilgo\Downloads\ENTSOE_CGMES_v2.4.15_04Jul2016_RDFS\EquipmentProfileCoreOperationRDFSAugmented-v2_4_15-4Jul2016.rdf"
    #
    # data = load_all_to_dataframe([model, rdfs])
    #
    #
    # values_in_linesegment = data.query("VALUE == '#ACLineSegment'")
    # values_in_powertransform_end = data.query("VALUE == '#PowerTransformerEnd'")
    # data.query("ID == '#PowerTransformerEnd.r'")
    # data.query("ID == '#Resistance'")
    #
    # data_types = data.query("KEY == 'dataType'")["VALUE"].drop_duplicates()

    # for quick export of data use data[data.VALUE == "PowerTransformer"].to_csv(export.csv) or data[data.VALUE == "PowerTransformer"].to_clipboard() and  paste to excel, for other method refer to pandas manual


    # SvPowerFlow = data.type_tableview("SvPowerFlow").reset_index()
    # TieFlow = data.type_tableview('TieFlow').reset_index()
    # tieflow_data = TieFlow.merge(SvPowerFlow, right_on='SvPowerFlow.Terminal', left_on="TieFlow.Terminal", how="left", suffixes=("", "_SvPowerFlow"))
    #
    # print(f"""
    # NP (tieflow):   {tieflow_data["SvPowerFlow.p"].sum()} MW
    # NP (CA.nI)  :   {data.type_tableview("ControlArea").iloc[0]["ControlArea.netInterchange"]} MW
    # """)

    namespace_map = dict(cim="http://iec.ch/TC57/2013/CIM-schema-cim16#",
                         entsoe="http://entsoe.eu/CIM/SchemaExtension/3/1#",
                         md="http://iec.ch/TC57/61970-552/ModelDescription/1#",
                         rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#")

    #Export Config


    #xml_per_instance, xml_per_instance_zip_per_all, xml_per_instance_zip_per_xml

    export_type = "xml_per_instance_zip_per_xml"

    # Load export format configuration
    #import json
    #with open(r"../profiles/ENTSOE/CGMES_v2_4_15_2014_08_07.json", "r") as conf_file:
    #    rdf_map = json.load(conf_file)

    # Export triplet to CGMES
    #data.export_to_cimxml(rdf_map=rdf_map,
    #                      namespace_map=namespace_map,
    #                      export_undefined=False,
    #                      export_type=export_type,
    #                      debug=True,
    #                      export_to_memory=False,
    #                      max_workers=None)

    import cProfile

    profiler = cProfile.Profile()
    profiler.enable()
    data = pandas.read_RDF([path], debug=False, max_workers=4)
    profiler.disable()
    profiler.print_stats(sort="cumulative")

# Last took 0:00:21.367552 on python 3.12 and pandas 2.2.3
#0:00:18.645366
#0:00:17.975319 [export_to_memory=True
#0:00:13.102329
#0:00:14.070468
#0:00:10.324683