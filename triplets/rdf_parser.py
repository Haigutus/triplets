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


def print_duration(text, start_time):
    """Print duration between now and start time
    Input: text, start_time
    Output: duration (in seconds), end_time"""

    end_time = datetime.datetime.now()
    duration = end_time - start_time
    logger.debug(f"{text}  {duration}")

    return duration, end_time


def remove_prefix(original_string, prefix_string):
    """Removes prefix from a string"""

    prefix_length = len(prefix_string)

    if original_string[0:prefix_length] == prefix_string:
        return original_string[prefix_length:]

    return original_string


def clean_ID(ID):
    """Removes ID prefixes used in CIM - first occourance of 'urn:uuid:', '#_', '_' is replaced by empty string"""

    # TODO - a lot of replacements have been done using replace function, but is it valid that these charecaters are not present in UUID-s? is replace once sufficent?

    # replace_count = 1 # Remove only once the ID prefix string, otherwise we risk of removing characters from within ID
    # clean_ID      = ID.replace("urn:uuid:", "", replace_count).replace("#_", "", replace_count).replace("_", "", replace_count)
    ID = remove_prefix(ID, "urn:uuid:")
    ID = remove_prefix(ID, "#_")
    ID = remove_prefix(ID, "_")

    return ID


def load_RDF_objects_from_XML(path_or_fileobject, debug=False):
    """Parse XML and return iterator of RDF objects and instance ID"""

    # START TIMER
    if debug:
        start_time = datetime.datetime.now()

    # LOAD XML
    parser = etree.XMLParser(remove_comments=True, collect_ids=False, remove_blank_text=True)
    parsed_xml = etree.parse(path_or_fileobject, parser=parser)  # TODO - add iterparse for Python3

    # Get unique ID for loaded instance
    # instance_id = clean_ID(parsed_xml.find("./").attrib.values()[0]) # Lets asume that the first RDF element describes the whole document - TODO replace it with hash of whole XML
    instance_id = str(uuid.uuid4())  # Guarantees unique ID for each loaded instance of data, in erronous data it happens that same UUID is used for multiple files

    if debug:
        _, start_time = print_duration("XML loaded to tree object", start_time)

    # EXTRACT RDF OBJECTS
    RDF_objects = parsed_xml.getroot().iterchildren()

    if debug:
        _, start_time = print_duration("All children put to a generator", start_time)

    return RDF_objects, instance_id


def find_all_xml(list_of_paths_to_zip_globalzip_xml, debug=False):
    """Returns list of XML file objects and/or paths in ZIP file"""

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
    """Parse single file to triplestore list"""

    file_name = path_or_fileobject

    if type(path_or_fileobject) != str:
        file_name = path_or_fileobject.name

    logger.info("Loading {}".format(file_name))

    RDF_objects, INSTANCE_ID = load_RDF_objects_from_XML(path_or_fileobject, debug)

    if debug:
        start_time = datetime.datetime.now()

    # Lets generate list for RDF data and store the original filename under rdf:label in dcat:Distribution object
    ID = str(uuid.uuid4())
    data_list = [
                    (ID, "Type", "Distribution", INSTANCE_ID),
                    (ID, "label", file_name, INSTANCE_ID)
                ]

    # lets create all variables, so that in loops they are reused, rather than new ones are created, green thinking
    #ID = ""
    KEY = ""
    VALUE = ""
    NS = ""

    for RDF_object in RDF_objects:

        ID = clean_ID(RDF_object.attrib.values()[0])
        # KEY        = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}type' # If we would like to keep all with correct namespace
        KEY = 'Type'
        KEY_NS = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
        VALUE_NS, VALUE = RDF_object.tag.split("}")  #TODO - case where there is no namespace will fail, but is it realistic for RDF file?
        # VALUE       = etree.QName(object).localname
        # ID_TYPE    = object.attrib.keys()[0].split("}")[1] # Adds column to identify "ID" and "about" types of ID

        # data_list.append([ID, ID_TYPE, KEY, VALUE]) # If using ID TYPE, maybe also namespace should be kept?
        data_list.append((ID, KEY, VALUE, INSTANCE_ID))

        for element in RDF_object.iterchildren():

            KEY_NS, KEY = element.tag.split("}")  #TODO - case where there is no namespace will fail, but is it realistic for RDF file?
            # KEY = etree.QName(element).localname
            VALUE = element.text
            VALUE_NS = ""

            if VALUE is None and len(element.attrib.values()) > 0:
                VALUE = clean_ID(element.attrib.values()[0])

            # data_list.append([ID, ID_TYPE, KEY_NAMESPACE, KEY, VALUE]) # If using ID TYPE
            data_list.append((ID, KEY, VALUE, INSTANCE_ID))

    if debug:
        _, start_time = print_duration("All values put to data list", start_time)

    return data_list


def load_RDF_to_dataframe(path_or_fileobject, debug=False, data_type="string"):
    """Parse single file to Pandas DataFrame"""

    data_list = load_RDF_to_list(path_or_fileobject, debug)

    if debug:
        start_time = datetime.datetime.now()

    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"], dtype=data_type)

    if debug:
        _, start_time = print_duration("List of data loaded to DataFrame", start_time)

    return data


def load_all_to_dataframe(list_of_paths_to_zip_globalzip_xml, debug=False, data_type="string", max_workers=None):
    """Parse contents of provided list of paths to Pandas DataFrame (zip, global zip or XML)"""

    if debug:
        process_start = datetime.datetime.now()

    list_of_xmls = find_all_xml(list_of_paths_to_zip_globalzip_xml, debug)

    data_list = []

    if max_workers:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Map the function to the XML list and accumulate results
            results = executor.map(lambda xml: load_RDF_to_list(xml, debug), list_of_xmls)
            # Flatten the results since each call to load_RDF_to_list returns a list
            data_list = [item for sublist in results for item in sublist]

    else:
        for xml in list_of_xmls:
            data_list.extend(load_RDF_to_list(xml, debug))

    if debug:
        start_time = datetime.datetime.now()

    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"], dtype=data_type)

    if debug:
        print_duration("Data list loaded to DataFrame", start_time)
        print_duration("All loaded in ", process_start)
        # logger.debug(data.info())

    return data


# Extend this functionality to pandas DataFrame
pandas.read_RDF = load_all_to_dataframe


def type_tableview(data, type_name, string_to_number=True):
    """Creates a table view of all objects of same type, with their parameters in columns"""

    # Get all ID-s of rows where Type == type_name
    type_id = data.query("VALUE == '{}' & KEY == 'Type'".format(type_name))

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
        data_view = data_view.apply(pandas.to_numeric, errors='ignore')

    return data_view

# Extend this functionality to pandas DataFrame
pandas.DataFrame.type_tableview = type_tableview

def key_tableview(data, key_name, string_to_number=True):
    """Creates a table view of all objects of same type, with their parameters in columns"""

    # Get all ID-s of rows where KEY == key_name
    type_id = data.query("KEY == @key_name")

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
        data_view = data_view.apply(pandas.to_numeric, errors='ignore')

    return data_view


# Extend this functionality to pandas DataFrame
pandas.DataFrame.key_tableview = key_tableview


def references_to_simple(data, reference, columns=["Type"]):
    """Creates a table view of all elements that specified element refers to,
    by default returns two columns ID and Type, but this can be extended"""

    reference_data = data.references_to(reference, levels=1).drop_duplicates(["ID_FROM", "KEY"])

    # Convert form triplets to a table view with columns - ID, Type by default
    data_view = reference_data.pivot(index="ID_FROM", columns="KEY")["VALUE"][columns]

    return data_view


# Extend this functionality to pandas DataFrame
pandas.DataFrame.references_to_simple = references_to_simple


def references_to(data, reference, levels=1):
    """Return all object pointing towards reference object"""

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
    """Creates a table view of all elements that specified element refers to,
    by default returns two columns ID and Type, but this can be extended"""

    reference_data = data.references_from(reference, levels=1).drop_duplicates(["ID_TO", "KEY"])

    # Convert form triplets to a table view with columns - ID, Type by default
    data_view = reference_data.pivot(index="ID_TO", columns="KEY")["VALUE"][columns]

    return data_view


# Extend this functionality to pandas DataFrame
pandas.DataFrame.references_from_simple = references_from_simple


def references_from(data, reference, levels=1):
    """Return all triplets that reference object points to"""

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
    """Finds all unique references (links, edges, etc.)
    merges data with itself on ID and VALUE
    INSTANCE ID is not taken into account

    returns DataFrame["ID_FROM", "KEY", "ID_TO"]"""

    return data[["ID", "KEY", "VALUE"]].drop_duplicates().merge(data[["ID"]].drop_duplicates(), left_on="VALUE", right_on="ID", suffixes=("_FROM", "_TO"))[["ID_FROM", "KEY", "ID_TO"]]


# Extend this functionality to pandas DataFrame
pandas.DataFrame.references_all = references_all

def references_simple(data, reference, columns=None, levels=1):
    """Creates a table view of all elements that specified element refers to,
    by default returns two columns ID and Type, but this can be extended"""


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
    """Returns dictionary with all types as keys and number of their occurrences as values"""

    types_dictionary = data[(data.KEY == "Type")]["VALUE"].value_counts().to_dict()

    return types_dictionary


# Extend this functionality to pandas DataFrame
pandas.DataFrame.types_dict = types_dict


def set_VALUE_at_KEY(data, key, value):
    """Set all values of provided key to the given value"""  # TODO add debug, to print key, initial value and new value.
    data.loc[data[data.KEY == key].index, "VALUE"] = value  # TODO add changes to change DataFrame

# Extend this functionality to pandas DataFrame
pandas.DataFrame.set_VALUE_at_KEY = set_VALUE_at_KEY

def set_VALUE_at_KEY_and_ID(data, key, value, id):
    """Set all values of provided key and id to the given value"""
    data.loc[data[(data.ID == id) & (data.KEY == key)].index, "VALUE"] = value

# Extend this functionality to pandas DataFrame
pandas.DataFrame.set_VALUE_at_KEY_and_ID = set_VALUE_at_KEY_and_ID


def export_to_excel(data, path=None):
    """Exports to excel all data with same INSTACE_ID and if label element exists for it. Each Type is put to a sheet"""
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
def get_qname(namespace, tag=None):
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
        _, start_time = print_duration("File root generated", start_time)

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
        rdf_object = E(get_qname(class_namespace, class_name))
        # Add ID attribute
        rdf_object.attrib[get_qname(id_name)] = f"{id_value_prefix}{ID}"
        # Add object to RDF
        RDF.append(rdf_object)
        # Add object with it's ID to dict (later we use it to add attributes to that class)
        objects[ID] = rdf_object

    if debug:
        _, start_time = print_duration("Objects added", start_time)

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
                tag = E(get_qname(tag_def["namespace"], KEY))
                attrib = tag_def.get("attrib", None)
                text_prefix = tag_def.get("text", "")

                if attrib:
                    tag.attrib[get_qname(attrib["attribute"])] = f"{attrib['value_prefix']}{VALUE}"
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
        _, start_time = print_duration("Attributes added", start_time)

    # Convert to XML
    xml = etree.tostring(RDF, pretty_print=True, xml_declaration=True, encoding='UTF-8')
    # TODO - clean namespaces

    logger.info("Exporting RDF to {}".format(file_name))

    if debug:
        _, start_time = print_duration("XML created", start_time)

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

    if debug:
        start_time = datetime.datetime.now()
        init_time = start_time

    instances = data.groupby("INSTANCE_ID")

    #TODO - this needs to be extended and put to a better place
    #TODO - mayve rdfmap should use direct url, instead of short keys "EQ" etc

    # Keep all file names and data to be exported
    xml_documents = []

    if debug:
        _, start_time = print_duration("All file instance ID-s identified", start_time)

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

    _, start_time = print_duration("All XML created in memory ", start_time)
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
        print_duration("Files saved in", start_time)
        print_duration("Whole Export done in", init_time)

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
    return data.query("ID == '{}'".format(object_UUID)).set_index("KEY")["VALUE"]


pandas.DataFrame.get_object_data = get_object_data


def tableview_to_triplet(data):
    """Makes a triplet of dataview"""
    # TODO add only when tableveiw is created
    return data.reset_index().melt(id_vars="ID", value_name="VALUE", var_name="KEY")


pandas.DataFrame.tableview_to_triplet = tableview_to_triplet

# Let's add empty dataframe to keep changes
pandas.DataFrame.changes = pandas.DataFrame()

def update_triplet_from_triplet(data, update_data, update=True, add=True):
    """Update or add data to current triplet from another one
    VALUE at ID and KEY is updated and KEY ID pair does not exist it is added together with VALUE
    you can control if data is added or updated with parameters update and add, by default both are True"""
    # TODO add changes dataframe, where to keep all changes done by this function
    # TODO create function to do also ID and KEY changes

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
    """Update or add data to current triplet from a tableview
    VALUE at ID and KEY is updated and KEY ID pair does not exist it is added together with VALUE
    you can control if data is added or updated with parameters update and add, by default both are True"""

    update_triplet = tableview.tableview_to_triplet()

    if instance_id:
        update_triplet["INSTANCE_ID"] = instance_id

    return update_triplet_from_triplet(data, update_triplet, update, add)


pandas.DataFrame.update_triplet_from_tableview = update_triplet_from_tableview


def remove_triplet_from_triplet(from_triplet, what_triplet, columns=["ID", "KEY", "VALUE"]):
    """Retuns from_triplet - what_triplet"""
    return from_triplet.drop(from_triplet.reset_index().merge(what_triplet[columns], on=columns, how="inner")["index"], axis=0)


def filter_triplet_by_type(triplet, type):
    """Filter out all objects data by rdf:type"""
    return triplet.merge(triplet.query("KEY == 'Type' and VALUE == @type").ID)


def triplet_diff(old_data, new_data):

    return old_data.merge(new_data, on=["ID", "KEY", "VALUE"], how='outer', indicator=True, suffixes=("_OLD", "_NEW"), sort=False).query("_merge != 'both'")


def print_triplet_diff(old_data, new_data, file_id_object="Distribution", file_id_key="label", exclude_objects=None):

    # Get diff between datasets
    diff = triplet_diff(old_data, new_data)
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
    """Converts triplet to networkx graph"""
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
    import json
    import cgmes_tools
    with open(r"../profiles/ENTSOE/CGMES_v2_4_15_2014_08_07.json", "r") as conf_file:
        rdf_map = json.load(conf_file)

    #data = cgmes_tools.update_FullModel_from_filename(data)

    # Export triplet to CGMES
    data.export_to_cimxml(rdf_map=rdf_map,
                          namespace_map=namespace_map,
                          export_undefined=False,
                          export_type=export_type,
                          debug=True,
                          export_to_memory=False,
                          max_workers=None)

# Last took 0:00:21.367552 on python 3.12 and pandas 2.2.3
#0:00:18.645366
#0:00:17.975319 [export_to_memory=True
#0:00:13.102329
#0:00:14.070468
#0:00:10.324683
