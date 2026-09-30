"""python_lxml_pandas engine: pure Python + lxml → list-of-tuples → pandas DataFrame.

The default parser engine. Requires only lxml + pandas (core deps, no pyarrow).
Restored from the original rdf_parser.py load_RDF_to_list logic with fixes:
- rdf:nodeID support (parity with arrow engines)
- Empty string instead of None for missing values (parity with arrow engines)
- Namespace map None key → "" (lxml uses None for default namespace)
"""

import os
import uuid
import logging
from typing import Union, IO

import pandas as pd
from lxml import etree

from .utils import iter_rdf_rows
from ..iri import TYPE_KEY

logger = logging.getLogger(__name__)


def load_rdf_to_dataframe(path_or_fileobject: Union[str, IO], debug: bool = False,
                          local_resources: bool = True) -> pd.DataFrame:
    """Parse single RDF/XML file to pandas DataFrame using lxml + list-of-tuples.

    This is the old proven path: lxml parse → iterate → build Python list → pd.DataFrame.
    No pyarrow dependency.
    """
    parser = etree.XMLParser(remove_comments=True, collect_ids=False, remove_blank_text=True)
    try:
        if isinstance(path_or_fileobject, os.PathLike):
            path_or_fileobject = os.fspath(path_or_fileobject)
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
        (dist_id, TYPE_KEY, "Distribution"),
        (dist_id, "label", str(file_name)),
        (nsmap_id, TYPE_KEY, "NamespaceMap"),
    ]

    for k, v in namespace_map.items():
        data_list.append((nsmap_id, str(k) if k is not None else "", str(v) if v is not None else ""))

    # RDF objects — (ID, KEY, VALUE) rows; one INSTANCE_ID for the whole file
    data_list.extend(iter_rdf_rows(root.iterchildren(), local_resources, root.base))

    df = pd.DataFrame(data_list, columns=["ID", "KEY", "VALUE"])
    df["INSTANCE_ID"] = instance_id

    if debug:
        logger.debug("python_lxml_pandas produced %d rows for %s", len(df), file_name)

    return df
