"""python_lxml_arrow engine: pure Python + lxml + pyarrow streaming to RecordBatch.

Uses lxml for XML parsing but streams to Arrow StringBuilder builders
instead of Python lists, producing pa.RecordBatch. Better for polars interop
and dictionary-encoding (categorical columns). Requires pyarrow.
"""

import uuid
import logging
from typing import Union, IO, Any

from lxml import etree
import pyarrow as pa

from .utils import (
    RDF_NS, RDF_ID, RDF_ABOUT, RDF_NODEID, RDF_RESOURCE,
    clean_ID, _split_prefixed_name,
)

logger = logging.getLogger(__name__)


def load_rdf_to_dataframe(path_or_fileobject: Union[str, IO], debug: bool = False) -> pa.RecordBatch:
    """Parse single RDF/XML (path or fileobj) to pyarrow RecordBatch using lxml + lists.

    Streaming in the sense of column-wise collection then direct Arrow (no 4-tuple list).
    """
    parser = etree.XMLParser(remove_comments=True, collect_ids=False, remove_blank_text=True)
    try:
        if isinstance(path_or_fileobject, str):
            parsed = etree.parse(path_or_fileobject, parser=parser)
        else:
            # ensure seekable for safety
            try:
                path_or_fileobject.seek(0)
            except Exception:
                pass
            parsed = etree.parse(path_or_fileobject, parser=parser)
        root = parsed.getroot()
    except Exception as e:
        logger.error("lxml parse failed for %s: %s", path_or_fileobject, e)
        raise

    namespace_map = dict(root.nsmap or {})
    try:
        if getattr(root, "base", None):
            namespace_map["xml_base"] = root.base
    except Exception:
        pass

    instance_id = str(uuid.uuid4())
    file_name = path_or_fileobject if isinstance(path_or_fileobject, str) else getattr(path_or_fileobject, "name", "")

    # Stream directly to Arrow builders (no intermediate full Python list of strings for the data).
    # This avoids the list-of-N-strings overhead during accumulation.
    # Use lib. for the Python builder classes (available across pyarrow versions).
    id_b = pa.lib.StringBuilder()
    key_b = pa.lib.StringBuilder()
    val_b = pa.lib.StringBuilder()
    inst_b = pa.lib.StringBuilder()

    # Meta: Distribution + NamespaceMap (matches legacy)
    dist_id = str(uuid.uuid4())
    nsmap_id = str(uuid.uuid4())
    id_b.append(dist_id); key_b.append("Type"); val_b.append("Distribution"); inst_b.append(instance_id)
    id_b.append(dist_id); key_b.append("label"); val_b.append(str(file_name)); inst_b.append(instance_id)
    id_b.append(nsmap_id); key_b.append("Type"); val_b.append("NamespaceMap"); inst_b.append(instance_id)

    for k, v in namespace_map.items():
        id_b.append(nsmap_id)
        key_b.append(str(k) if k is not None else "")
        val_b.append(str(v) if v is not None else "")
        inst_b.append(instance_id)

    # RDF objects
    for rdf_object in root.iterchildren():
        attribs = rdf_object.attrib
        obj_id = clean_ID(
            attribs.get(RDF_ID)
            or attribs.get(RDF_ABOUT)
            or attribs.get(RDF_NODEID)
            or ""
        )

        # Type
        tag = rdf_object.tag
        type_value = _split_prefixed_name(tag)
        id_b.append(obj_id); key_b.append("Type"); val_b.append(type_value); inst_b.append(instance_id)

        for element in rdf_object.iterchildren():
            key = _split_prefixed_name(element.tag)
            value = element.text
            if value is None and element.attrib:
                value = clean_ID(
                    element.attrib.get(RDF_RESOURCE)
                    or element.attrib.get(RDF_NODEID)
                    or ""
                )
                if value and value.startswith("http"):
                    value = value.split("#")[-1] if "#" in value else value
            id_b.append(obj_id)
            key_b.append(key)
            val_b.append(value if value is not None else "")
            inst_b.append(instance_id)

    # Finish builders to arrays (direct to Arrow)
    batch = pa.RecordBatch.from_arrays(
        [id_b.finish(), key_b.finish(), val_b.finish(), inst_b.finish()],
        ["ID", "KEY", "VALUE", "INSTANCE_ID"],
    )
    if debug:
        logger.debug("python_lxml produced batch with %d rows for %s", batch.num_rows, file_name)
    return batch
