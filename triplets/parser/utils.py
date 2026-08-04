"""Shared utilities for CIM/RDF XML parsers (python_lxml and cython_pugixml).

Extracted/adapted from rdf_parser.py and rdf_parser_lxml_arrow.py cues.
"""

from io import BytesIO
import os
import uuid
import logging
import zipfile
from typing import List, Union, IO, Any

logger = logging.getLogger(__name__)

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDF_ID = f"{{{RDF_NS}}}ID"
RDF_ABOUT = f"{{{RDF_NS}}}about"
RDF_NODEID = f"{{{RDF_NS}}}nodeID"
RDF_RESOURCE = f"{{{RDF_NS}}}resource"


# The CIM parser's cleaning chain, expressed as data (parser_rdfxml records
# what each rule strips, making the cleaning reversible: PREFIX + local = term).
DEFAULT_CLEAN_RULES = {"ID": ("urn:uuid:", "#_", "_"), "VALUE": ("urn:uuid:", "#_", "_")}


def clean_with_prefix(term: str, rules) -> tuple:
    """clean_ID's chained strip, recording what was removed.

    Returns ``(local, prefix)`` with ``prefix + local == term`` byte-exact.
    Rules apply in order, each against what the previous ones left.
    """
    cut = 0
    for rule in rules:
        if term.startswith(rule, cut):
            cut += len(rule)
    return term[cut:], term[:cut]


def clean_ID(ID: Any) -> str:
    """Removes ID prefixes used in CIM - urn:uuid:, #_, _ ."""
    if not ID:
        return ""
    ID = str(ID)
    for prefix in ("urn:uuid:", "#_", "_"):
        if ID.startswith(prefix):
            ID = ID[len(prefix):]
    return ID


def _split_prefixed_name(name: str) -> str:
    """Split 'prefix:localname' or {ns}local -> localname."""
    if not name:
        return ""
    if name.startswith("{"):
        idx = name.find("}")
        if idx >= 0:
            return name[idx + 1:]
    idx = name.find(":")
    if idx >= 0:
        return name[idx + 1:]
    return name


def iter_all_xml(list_of_paths_to_zip_globalzip_xml: Union[str, List, Any], debug: bool = False):
    """Yield XML file objects and/or str paths, one at a time (lazy).

    Supports str paths, file-like, .xml/.rdf, .zip (recursive). Same items and
    order as :func:`find_all_xml`: direct .xml/.rdf items first in input order,
    then zip members in zip order. Zip members are read into memory only when
    yielded, so a consumer that processes-and-drops each file keeps at most one
    member in RAM (out-of-core ingest). Zip handles this function opens are
    closed after their archive is exhausted.
    """
    items = list_of_paths_to_zip_globalzip_xml
    if isinstance(items, (str, bytes, os.PathLike)) or hasattr(items, "read"):
        items = [items]

    pending_zips: List = []   # str paths (we open+close) or file-likes (caller owns)

    for item in items:
        if isinstance(item, os.PathLike):
            item = os.fspath(item)  # Path takes the str branch (keeps the cython mmap fast path)
        if isinstance(item, str):
            item_lower = item.lower()
        elif hasattr(item, "name"):
            item_lower = getattr(item, "name", "").lower()
        else:
            item_lower = str(item).lower()

        if ".xml" in item_lower or ".rdf" in item_lower:
            # str paths stay str (no open fd; enables the cython mmap fast path)
            yield item
            if debug:
                logger.debug("Added: %s", getattr(item, "name", item))
        elif ".zip" in item_lower:
            pending_zips.append(item)
            if debug:
                logger.debug("Added for zip processing: %s", getattr(item, "name", item))
        else:
            logger.warning("Not supported file: %s", getattr(item, "name", item))

    for zip_source in pending_zips:   # appends during iteration handle nested zips
        opened = None
        if isinstance(zip_source, str):
            try:
                opened = open(zip_source, "rb")
            except Exception:
                logger.warning("Could not open zip: %s", zip_source)
                continue
        try:
            try:
                zip_container = zipfile.ZipFile(opened if opened is not None else zip_source)
            except Exception as e:
                logger.warning("Bad zip %s: %s", getattr(zip_source, "name", zip_source), e)
                continue
            with zip_container:
                for zipped_file in zip_container.namelist():
                    zipped_file_lower = zipped_file.lower()
                    if ".xml" in zipped_file_lower or ".rdf" in zipped_file_lower:
                        try:
                            file_object = BytesIO(zip_container.read(zipped_file))
                            file_object.name = zipped_file
                        except Exception as e:
                            logger.warning("Zip member read fail %s: %s", zipped_file, e)
                            continue
                        if debug:
                            logger.debug("Added from zip: %s", zipped_file)
                        yield file_object
                    elif ".zip" in zipped_file_lower:
                        try:
                            pending_zips.append(BytesIO(zip_container.read(zipped_file)))
                        except Exception:
                            pass
                    elif debug:
                        logger.debug("Skipped in zip: %s", zipped_file)
        finally:
            if opened is not None:
                opened.close()


def find_all_xml(list_of_paths_to_zip_globalzip_xml: Union[str, List, Any], debug: bool = False) -> List:
    """Returns list of XML file objects and/or paths in ZIP file.

    Supports str paths, file-like, .xml/.rdf, .zip (recursive). Eager form of
    :func:`iter_all_xml` — every zip member is read into memory up front.
    """
    return list(iter_all_xml(list_of_paths_to_zip_globalzip_xml, debug=debug))


def get_namespace_map_from_root(root: Any) -> dict:
    """Best effort ns map + xml_base from element (lxml or pygixml style)."""
    nsmap = {}
    try:
        # lxml style
        if hasattr(root, "nsmap"):
            for k, v in (root.nsmap or {}).items():
                nsmap[k or ""] = v
        if hasattr(root, "base") and root.base:
            nsmap["xml_base"] = root.base
    except Exception:
        pass
    return nsmap
