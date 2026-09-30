"""python_lxml_arrow engine: pure Python + lxml + pyarrow streaming to RecordBatch.

Uses lxml for XML parsing but streams to Arrow StringBuilder builders
instead of Python lists, producing pa.RecordBatch. Better for polars interop
and dictionary-encoding (categorical columns). Requires pyarrow.
"""

import os
import uuid
import logging
from typing import IO, Optional, Union, Any

from lxml import etree
import pyarrow as pa

from .utils import document_base, iter_rdf_rows, prefix_map
from ..iri import TRIPLETS_NS, TYPE_KEY

logger = logging.getLogger(__name__)


def load_rdf_to_dataframe(path_or_fileobject: Union[str, IO], debug: bool = False,
                          iri_form: str = "local", default_base: str = TRIPLETS_NS,
                          prefixes: Optional[dict] = None) -> pa.RecordBatch:
    """Parse single RDF/XML (path or fileobj) to pyarrow RecordBatch using lxml + lists.

    Streaming in the sense of column-wise collection then direct Arrow (no 4-tuple list).
    """
    parser = etree.XMLParser(remove_comments=True, collect_ids=False, remove_blank_text=True)
    try:
        if isinstance(path_or_fileobject, os.PathLike):
            path_or_fileobject = os.fspath(path_or_fileobject)
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

    # the document's own prefixes, overridden by the caller's — recorded in the NamespaceMap rows
    # so a prefixed frame carries the map that expands it
    namespace_map = {**dict(root.nsmap or {}), **(prefixes or {})}
    prefix_of = {namespace: prefix for prefix, namespace in prefix_map(root, prefixes).items()}
    declared_base = document_base(root, None)   # a declared absolute xml:base only — never the file location
    if declared_base:
        namespace_map["xml_base"] = declared_base

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
    id_b.append(dist_id); key_b.append(TYPE_KEY); val_b.append("Distribution"); inst_b.append(instance_id)
    id_b.append(dist_id); key_b.append("label"); val_b.append(str(file_name)); inst_b.append(instance_id)
    id_b.append(nsmap_id); key_b.append(TYPE_KEY); val_b.append("NamespaceMap"); inst_b.append(instance_id)

    for k, v in namespace_map.items():
        id_b.append(nsmap_id)
        key_b.append(str(k) if k is not None else "")
        val_b.append(str(v) if v is not None else "")
        inst_b.append(instance_id)

    # RDF objects
    for obj_id, key, value in iter_rdf_rows(root.iterchildren(), iri_form, document_base(root, default_base), prefix_of):
        id_b.append(obj_id); key_b.append(key); val_b.append(value); inst_b.append(instance_id)

    # Finish builders to arrays (direct to Arrow)
    batch = pa.RecordBatch.from_arrays(
        [id_b.finish(), key_b.finish(), val_b.finish(), inst_b.finish()],
        ["ID", "KEY", "VALUE", "INSTANCE_ID"],
    )
    if debug:
        logger.debug("python_lxml produced batch with %d rows for %s", batch.num_rows, file_name)
    return batch
