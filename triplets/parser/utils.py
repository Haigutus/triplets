"""Shared utilities for CIM/RDF XML parsers (python_lxml and cython_pugixml).

Extracted/adapted from rdf_parser.py and rdf_parser_lxml_arrow.py cues.
"""

from io import BytesIO
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


def find_all_xml(list_of_paths_to_zip_globalzip_xml: Union[str, List, Any], debug: bool = False) -> List:
    """Returns list of XML file objects and/or paths in ZIP file.

    Supports str paths, file-like, .xml/.rdf, .zip (recursive).
    """
    xml_files_list: List = []
    zip_files_list: List = []

    items = list_of_paths_to_zip_globalzip_xml
    if isinstance(items, (str, bytes)) or hasattr(items, "read"):
        items = [items]

    for item in items:
        if isinstance(item, str):
            item_lower = item.lower()
            if ".xml" in item_lower or ".rdf" in item_lower:
                # Keep str path (no open fd here). Engines accept str paths (enables cython mmap).
                xml_files_list.append(item)
                if debug:
                    logger.debug("Added: %s", item)
                continue
            elif ".zip" in item_lower:
                try:
                    item = open(item, "rb")
                except Exception:
                    logger.warning("Could not open zip: %s", item)
                    continue
                zip_files_list.append(item)
                if debug:
                    logger.debug("Added for zip processing: %s", item)
                continue
            else:
                logger.warning("Not supported file: %s", item)
                continue

        # non-str (fileobj or pre-opened): determine lower from .name if present
        if hasattr(item, "name"):
            item_lower = getattr(item, "name", "").lower()
        else:
            item_lower = str(item).lower()

        if ".xml" in item_lower or ".rdf" in item_lower:
            xml_files_list.append(item)
            if debug:
                logger.debug("Added: %s", getattr(item, "name", item))
        elif ".zip" in item_lower:
            zip_files_list.append(item)
            if debug:
                logger.debug("Added for zip processing: %s", getattr(item, "name", item))
        else:
            logger.warning("Not supported file: %s", getattr(item, "name", item))

    for zip_file_path in zip_files_list:
        try:
            zip_container = zipfile.ZipFile(zip_file_path)
        except Exception as e:
            logger.warning("Bad zip %s: %s", zip_file_path, e)
            continue

        with zip_container:
            zipped_files = zip_container.namelist()

            for zipped_file in zipped_files:
                zipped_file_lower = zipped_file.lower()
                if ".xml" in zipped_file_lower or ".rdf" in zipped_file_lower:
                    try:
                        data = zip_container.read(zipped_file)
                        file_object = BytesIO(data)
                        file_object.name = zipped_file
                        xml_files_list.append(file_object)
                        if debug:
                            logger.debug("Added from zip: %s", zipped_file)
                    except Exception as e:
                        logger.warning("Zip member read fail %s: %s", zipped_file, e)
                elif ".zip" in zipped_file_lower:
                    try:
                        data = zip_container.read(zipped_file)
                        zip_files_list.append(BytesIO(data))
                    except Exception:
                        pass
                else:
                    if debug:
                        logger.debug("Skipped in zip: %s", zipped_file)

    return xml_files_list


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
