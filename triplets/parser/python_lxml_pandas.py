"""python_lxml_pandas engine: pure Python + lxml → list-of-tuples → pandas DataFrame.

The default parser engine. Requires only lxml + pandas (core deps, no pyarrow).
Restored from the original rdf_parser.py load_RDF_to_list logic with fixes:
- rdf:nodeID support (parity with arrow engines)
- Empty string instead of None for missing values (parity with arrow engines)
- Namespace map None key → "" (lxml uses None for default namespace)
"""

import uuid
import logging
from typing import Union, IO

import pandas as pd
from lxml import etree

from .utils import (
    RDF_NS, RDF_ID, RDF_ABOUT, RDF_NODEID, RDF_RESOURCE,
    clean_ID, _split_prefixed_name,
)

logger = logging.getLogger(__name__)


def load_rdf_to_dataframe(path_or_fileobject: Union[str, IO], debug: bool = False) -> pd.DataFrame:
    """Parse single RDF/XML file to pandas DataFrame using lxml + list-of-tuples.

    This is the old proven path: lxml parse → iterate → build Python list → pd.DataFrame.
    No pyarrow dependency.
    """
    parser = etree.XMLParser(remove_comments=True, collect_ids=False, remove_blank_text=True)
    try:
        if isinstance(path_or_fileobject, str):
            parsed = etree.parse(path_or_fileobject, parser=parser)
        else:
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

    # Meta rows: Distribution + NamespaceMap
    dist_id = str(uuid.uuid4())
    nsmap_id = str(uuid.uuid4())
    data_list = [
        (dist_id, "Type", "Distribution", instance_id),
        (dist_id, "label", str(file_name), instance_id),
        (nsmap_id, "Type", "NamespaceMap", instance_id),
    ]

    for k, v in namespace_map.items():
        data_list.append((nsmap_id, str(k) if k is not None else "", str(v) if v is not None else "", instance_id))

    # RDF objects
    for rdf_object in root.iterchildren():
        attribs = rdf_object.attrib
        obj_id = clean_ID(
            attribs.get(RDF_ID)
            or attribs.get(RDF_ABOUT)
            or attribs.get(RDF_NODEID)
            or ""
        )

        # Type row
        type_value = _split_prefixed_name(rdf_object.tag)
        data_list.append((obj_id, "Type", type_value, instance_id))

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
            # Use empty string instead of None for parity with arrow engines
            if value is None:
                value = ""
            data_list.append((obj_id, key, value, instance_id))

    df = pd.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])

    if debug:
        logger.debug("python_lxml_pandas produced %d rows for %s", len(df), file_name)

    return df
