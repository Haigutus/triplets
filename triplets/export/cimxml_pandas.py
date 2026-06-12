# -------------------------------------------------------------------------------
# Name:        export/cimxml_pandas.py
# Purpose:     Export triplet DataFrames to CIM RDF XML format
# -------------------------------------------------------------------------------
import datetime
import logging

from functools import lru_cache

from lxml import etree
from lxml.builder import ElementMaker
from lxml.etree import QName

from .cimxml_utils import load_rdf_map, resolve_instance_config

logger = logging.getLogger(__name__)

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


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
                 debug=False,
                 datatypes=False):
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
        datatypes : bool, default False
            If True, annotate literal elements with rdf:datatype from the schema's
            xsd:type (like the N-Quads export); xsd:string stays unannotated.
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

    rdf_map = load_rdf_map(rdf_map)
    file_name, namespace_map, instance_rdf_map = resolve_instance_config(instance_data, rdf_map, namespace_map)

    key_datatypes = {}
    if datatypes:
        # same KEY → xsd URI mapping the N-Quads export uses (string → None, anyURI excluded)
        from .nquads_utils import build_key_metadata
        _, _, key_datatypes = build_key_metadata(rdf_map)

    if instance_rdf_map is None:
        logger.warning("No rdf mapping available for {}".format(file_name))
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
                    datatype = key_datatypes.get(KEY)
                    if datatype:
                        tag.attrib[_get_qname(RDF_NS, "datatype")] = datatype

                _object.append(tag)

            else:
                logger.debug("Definition missing for tag: " + KEY)

                if export_undefined:
                    tag = E(KEY)
                    tag.text = str(VALUE)
                    # key_datatypes spans all schema profiles, so annotation works
                    # even when instance profile resolution fell through
                    datatype = key_datatypes.get(KEY)
                    if datatype:
                        tag.attrib[_get_qname(RDF_NS, "datatype")] = datatype

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
