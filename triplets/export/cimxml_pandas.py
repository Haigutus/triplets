# -------------------------------------------------------------------------------
# Name:        export/cimxml_pandas.py
# Purpose:     Export triplet DataFrames to CIM RDF XML format
# -------------------------------------------------------------------------------
import os
import json
import datetime
import uuid
import zipfile
import logging

from io import BytesIO
from enum import StrEnum
from functools import lru_cache
from concurrent.futures import ProcessPoolExecutor

import pandas

from lxml import etree
from lxml.builder import ElementMaker
from lxml.etree import QName

from triplets.tools import get_namespace_map

logger = logging.getLogger(__name__)


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
        - end_time (datetime): The current time after the operation.
    """
    end_time = datetime.datetime.now()
    duration = end_time - start_time
    logger.info(f"{text} {duration}")
    return duration, end_time


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
                 namespace_map=None,
                 class_KEY="Type",
                 export_undefined=True,
                 comment=None,
                 debug=False):
    """
        Generate an RDF XML file from a triplet dataset instance.

        This function processes a single instance (grouped by ``INSTANCE_ID``) from a triplet
        dataset and exports it as an RDF/XML document using provided or inferred mapping rules.

        Parameters
        ----------
        instance_data : pandas.DataFrame
            Triplet dataset for a single instance, with columns [''ID', 'KEY', 'VALUE', INSTANCE_ID'].
            Must contain at least one row with ``KEY == class_KEY`` to define object types.
        rdf_map : dict or str, optional
            Dictionary mapping CIM classes and attributes to RDF namespaces and export rules.
            If a string is provided, it is treated as a file path to a JSON configuration.
            If ``None``, attempts to infer from instance data (e.g., profile-based mapping).
        namespace_map : dict, optional
            Mapping of namespace prefixes to URIs (e.g., ``{"cim": "http://iec.ch/TC57/2013/CIM-schema-cim16#"}``).
            Must include ``"rdf"`` namespace. If ``None``, inferred from ``rdf_map`` or instance.
        class_KEY : str, default "Type"
            Column key used to identify object class/type in the triplet data.
        export_undefined : bool, default True
            If True, export classes and attributes without explicit mapping using default RDF settings.
            If False, skip unmapped elements with a warning.
        comment : str, optional
            Optional comment to insert at the top of the XML output (as XML comment).
        debug : bool, default False
            If True, log detailed timing and debug information during processing.

        Returns
        -------
        dict
            Dictionary containing:
            - ``'filename'`` (str): Generated filename (from ``label`` or UUID).
            - ``'file'`` (bytes): UTF-8 encoded XML content.

        Raises
        ------
        KeyError
            If required columns are missing in ``instance_data``.
        ValueError
            If invalid export configuration or mapping is detected.

        Examples
        --------
        >>> instance = data[data["INSTANCE_ID"] == 1]
        >>> result = generate_xml(
        ...     instance,
        ...     rdf_map="config/eq_profile.json",
        ...     comment="Exported on 2025-11-11",
        ...     debug=True
        ... )
        >>> with open(result["filename"], "wb") as f:
        ...     f.write(result["file"])

        Notes
        -----
        - Supports profile-based mapping (e.g., "EQ", "SSH") via ``Model.profile`` or ``Model.messageType``.
        - Uses ``lxml.etree`` with ``ElementMaker`` for XML construction.
        - Undefined classes are exported with ``rdf:about="urn:uuid:<ID>"`` when ``export_undefined=True``.
        """
    # TODO - Use if logger debug
    if debug:
        start_time = datetime.datetime.now()

    # config map, is not given as dict, assume path and load it
    if not isinstance(rdf_map, dict):
        with open(rdf_map, "r") as conf_file:
            rdf_map = json.load(conf_file)

    # No map in function call, use instance map
    if not namespace_map:
        namespace_map, xml_base = get_namespace_map(instance_data)

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

        # TODO - needs to be extended and made more intelligent, maybe scan profile?
        profile_map = {
            "EquipmentCore": "EQ",
            "SteadyState": "SSH",
            "StateVariables": "SV",
            "Topology/": "TP",
            "EquipmentBoundary": "EQBD",
            "TopologyBoundary": "TPBD"
        }

        for key, value in profile_map.items():
            if key in instance_type_url:
                instance_type = value
                continue

    # If there is sub structure available in schema get it, otherwise use root definitions
    # TODO - needs revision, add support both for md:FullModel, dcat:DataSet and without profile definiton
    instance_rdf_map = rdf_map.get(instance_type, rdf_map)

    # No map in function call, nor in instance data, use profile map
    if not namespace_map and instance_rdf_map:
        namespace_map = instance_rdf_map.get("ProfileNamespaceMap")

    if instance_rdf_map is None:
        logger.warning("No rdf mapping available for {}".format(instance_type))
        if not export_undefined:
            logger.warning("File not created for {}".format(file_name))
            return

    # Create element builder
    E = ElementMaker(nsmap=namespace_map)

    # Create xml root element
    RDF = E(QName(namespace_map.get("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"), "RDF"))

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

                    value_prefix = attrib.get("value_prefix", "")

                    # Get namespace for enumerations
                    if not value_prefix:
                        value_prefix = instance_rdf_map.get(VALUE, {}).get("namespace", "")

                    tag.attrib[_get_qname(attrib["attribute"])] = f"{value_prefix}{VALUE}"
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


class ExportType(StrEnum):
    XML_PER_INSTANCE = "xml_per_instance"
    XML_PER_INSTANCE_ZIP_PER_ALL = "xml_per_instance_zip_per_all"
    XML_PER_INSTANCE_ZIP_PER_XML = "xml_per_instance_zip_per_xml"


def export_to_cimxml(data,
                     rdf_map=None,
                     namespace_map=None,
                     class_KEY="Type",
                     export_undefined=True,
                     export_type=ExportType.XML_PER_INSTANCE_ZIP_PER_XML,
                     global_zip_filename="Export.zip",
                     debug=False,
                     export_to_memory=False,
                     export_base_path="",
                     comment=None,
                     max_workers=None):
    """
        Export a full triplet dataset to CIM RDF XML files or ZIP archives.

        Processes all instances (grouped by ``INSTANCE_ID``) and exports them according to the
        specified ``export_type``. Supports parallel processing and in-memory or disk output.

        Parameters
        ----------
        data : pandas.DataFrame
            Full triplet dataset with columns ['INSTANCE_ID', 'ID', 'KEY', 'VALUE'].
        rdf_map : dict or str, optional
            RDF mapping configuration (see :func:`generate_xml`).
        namespace_map : dict, optional
            Namespace prefix-to-URI mapping (see :func:`generate_xml`).
        class_KEY : str, default "Type"
            Key identifying object types in triplet data.
        export_undefined : bool, default True
            Export unmapped classes/attributes with default RDF syntax.
        export_type : ExportType or str, default ExportType.XML_PER_INSTANCE_ZIP_PER_XML
            Export format:
            - ``XML_PER_INSTANCE``: One XML file per instance.
            - ``XML_PER_INSTANCE_ZIP_PER_ALL``: All XMLs in a single ZIP.
            - ``XML_PER_INSTANCE_ZIP_PER_XML``: Each XML in its own ZIP.
        global_zip_filename : str, default "Export.zip"
            Filename for the global ZIP archive (used with ``ZIP_PER_ALL``).
        debug : bool, default False
            Enable detailed timing and debug logging.
        export_to_memory : bool, default False
            If True, return file-like objects (``BytesIO``); if False, save to disk.
        export_base_path : str, default ""
            Directory to save files when ``export_to_memory=False``. Uses current directory if empty.
        comment : str, optional
            Optional XML comment added to each generated file.
        max_workers : int, optional
            Number of parallel workers for XML generation. If ``None``, runs sequentially.

        Returns
        -------
        list
            - If ``export_to_memory=True``: List of ``BytesIO`` objects with ``.name`` attribute.
            - If ``export_to_memory=False``: List of saved filenames (relative to ``export_base_path``).

        Examples
        --------
        >>> files = export_to_cimxml(
        ...     data,
        ...     rdf_map="config/cim_map.json",
        ...     export_type=ExportType.XML_PER_INSTANCE_ZIP_PER_XML,
        ...     export_to_memory=True,
        ...     max_workers=4
        ... )
        >>> for f in files:
        ...     print(f"name:", f.name)

        Notes
        -----
        - Uses ``concurrent.futures.ProcessPoolExecutor`` for parallel XML generation.
        - All XML files are UTF-8 encoded with XML declaration and pretty-printing.
        - ZIP files use DEFLATED compression.
        - Filenames are derived from instance ``label`` or UUID.
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

    if export_type == ExportType.XML_PER_INSTANCE:
        for document in xml_documents:

            file_object = BytesIO(document["file"])
            file_object.name = document["filename"]

            exported_files.append(file_object)

            logger.info(f"Exported {document['filename']} to memory")

    ### Export ZIP containing all xml ###
    elif export_type == ExportType.XML_PER_INSTANCE_ZIP_PER_ALL:

        gloabl_zip_fileobject = BytesIO()
        gloabl_zip_fileobject.name = global_zip_filename

        with zipfile.ZipFile(gloabl_zip_fileobject, "a", zipfile.ZIP_DEFLATED, False) as zip_file:

            for document in xml_documents:
                zip_file.writestr(document["filename"], document["file"])
                logger.info(f'Added {document["filename"]} to ZIP')

        exported_files.append(gloabl_zip_fileobject)
        logger.info(f'Exported ZIP named {global_zip_filename} to memory')


    ### Export each xml in separate zip ###
    elif export_type == ExportType.XML_PER_INSTANCE_ZIP_PER_XML:

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
