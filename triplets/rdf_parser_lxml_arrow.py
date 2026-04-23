"""
RDF/XML parser: lxml + PyArrow.

Same parsing logic as rdf_parser.py but uses per-column Python lists
and constructs the DataFrame via PyArrow zero-copy instead of
pd.DataFrame(list_of_tuples). This avoids tuple allocation overhead
and the slow pandas list-of-tuples constructor.

Requires: lxml, pyarrow
"""

import uuid
import logging
from lxml import etree
import pyarrow as pa

logger = logging.getLogger(__name__)

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDF_ID = f"{{{RDF_NS}}}ID"
RDF_ABOUT = f"{{{RDF_NS}}}about"
RDF_NODEID = f"{{{RDF_NS}}}nodeID"
RDF_RESOURCE = f"{{{RDF_NS}}}resource"


def _clean_id(raw_id):
    """Remove common CIM ID prefixes."""
    if raw_id is None:
        return ""
    for prefix in ("urn:uuid:", "#_", "_"):
        if raw_id.startswith(prefix):
            raw_id = raw_id[len(prefix):]
    return raw_id


def load_rdf_to_arrow(path_or_fileobject):
    """Parse a single RDF/XML file into a PyArrow RecordBatch.

    Uses lxml for XML parsing and per-column lists for Arrow array
    construction. Avoids tuple allocation and the slow pandas
    list-of-tuples DataFrame constructor.

    Returns
    -------
    pyarrow.RecordBatch
    """
    parser = etree.XMLParser(remove_comments=True, collect_ids=False, remove_blank_text=True)
    parsed_xml = etree.parse(path_or_fileobject, parser=parser).getroot()

    namespace_map = parsed_xml.nsmap
    namespace_map["xml_base"] = parsed_xml.base

    instance_id = str(uuid.uuid4())

    # Per-column lists (faster than list of tuples — no tuple allocation)
    col_id = []
    col_key = []
    col_value = []
    col_inst = []

    # Use local references for speed in tight loop
    id_append = col_id.append
    key_append = col_key.append
    val_append = col_value.append
    inst_append = col_inst.append

    # Metadata: Distribution + NamespaceMap
    dist_id = str(uuid.uuid4())
    nsmap_id = str(uuid.uuid4())
    file_name = path_or_fileobject if isinstance(path_or_fileobject, str) else getattr(path_or_fileobject, 'name', '')

    id_append(dist_id); key_append("Type"); val_append("Distribution"); inst_append(instance_id)
    id_append(dist_id); key_append("label"); val_append(str(file_name)); inst_append(instance_id)
    id_append(nsmap_id); key_append("Type"); val_append("NamespaceMap"); inst_append(instance_id)

    for k, v in namespace_map.items():
        id_append(nsmap_id)
        key_append(str(k) if k is not None else "None")
        val_append(str(v) if v is not None else "")
        inst_append(instance_id)

    # Parse RDF objects — same logic as rdf_parser.load_RDF_to_list
    attrib_get = dict.get  # avoid method lookup in loop

    for rdf_object in parsed_xml.iterchildren():
        attribs = rdf_object.attrib
        obj_id = _clean_id(
            attribs.get(RDF_ID)
            or attribs.get(RDF_ABOUT)
            or attribs.get(RDF_NODEID)
        )

        # Type triple
        parts = rdf_object.tag.partition("}")
        type_value = parts[2]
        id_append(obj_id); key_append("Type"); val_append(type_value); inst_append(instance_id)

        # Child elements
        for element in rdf_object.iterchildren():
            parts = element.tag.partition("}")
            key = parts[2]
            value = element.text

            if value is None and element.attrib:
                value = _clean_id(
                    element.attrib.get(RDF_RESOURCE)
                    or element.attrib.get(RDF_NODEID)
                    or ""
                )
                if value.startswith("http"):
                    value = value.split("#")[-1]

            id_append(obj_id)
            key_append(key)
            val_append(value if value is not None else "")
            inst_append(instance_id)

    # Build Arrow RecordBatch directly from column lists
    batch = pa.RecordBatch.from_pydict({
        "ID": pa.array(col_id, type=pa.string()),
        "KEY": pa.array(col_key, type=pa.string()),
        "VALUE": pa.array(col_value, type=pa.string()),
        "INSTANCE_ID": pa.array(col_inst, type=pa.string()),
    })
    return batch


def load_rdf_to_dataframe(path_or_fileobject):
    """Parse a single RDF/XML file into a pandas DataFrame via Arrow."""
    batch = load_rdf_to_arrow(path_or_fileobject)
    return batch.to_pandas()


def load_all_to_dataframe(list_of_paths, max_workers=None):
    """Parse multiple RDF/XML files into a single pandas DataFrame via Arrow."""
    from . import rdf_parser

    if isinstance(list_of_paths, str):
        list_of_paths = [list_of_paths]

    xml_files = rdf_parser.find_all_xml(list_of_paths)
    batches = [load_rdf_to_arrow(f) for f in xml_files]

    if not batches:
        import pandas
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])

    table = pa.concat_tables([pa.Table.from_batches([b]) for b in batches])
    return table.to_pandas()
