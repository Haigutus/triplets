# -------------------------------------------------------------------------------
# Name:        export/cimxml_utils.py
# Purpose:     Shared helpers for CIM XML export engines (python_lxml, cython_pugixml)
# -------------------------------------------------------------------------------
import json
import uuid
import logging

from triplets.tools import get_namespace_map

logger = logging.getLogger(__name__)

# Legacy fallback only: maps a substring of old-style (CGMES 2.4.15)
# Model.profile URLs to the schema section. Modern schemas resolve via their
# embedded ProfileMetadata (keyword / versionIRI / conformsTo) instead.
PROFILE_URL_MAP = {
    "EquipmentCore": "EQ",
    "SteadyState": "SSH",
    "StateVariables": "SV",
    "Topology/": "TP",
    "EquipmentBoundary": "EQBD",
    "TopologyBoundary": "TPBD",
}

# Namespace for internal/undefined structures when export_undefined=True
TRIPLETS_NS = "http://triplets#"


def load_rdf_map(rdf_map):
    """Return the export schema as a dict; load from JSON file path if needed."""
    if isinstance(rdf_map, dict):
        return rdf_map
    with open(rdf_map, "r") as conf_file:
        return json.load(conf_file)


def _profile_identity_index(rdf_map):
    """Map every identifier a schema section declares to the section name.

    Sections identify themselves via their key ("EQ") and the ProfileMetadata
    entry: keyword ("EQ"), versionIRI (the profile URI, matches old-header
    Model.profile), conformsTo. The schema defines what to match — no
    hardcoded knowledge per CGMES generation.
    """
    index = {}
    for section_name, section in rdf_map.items():
        if not isinstance(section, dict):
            continue
        metadata = section.get("ProfileMetadata", {})
        identifiers = [section_name, metadata.get("keyword"),
                       metadata.get("versionIRI"), metadata.get("conformsTo")]
        for identifier in identifiers:
            if isinstance(identifier, str) and identifier:
                index.setdefault(identifier, section_name)
    return index


def _instance_profile_hints(instance_data):
    """Profile references the instance header may carry, in priority order:
    old header messageType, new dcat:Dataset keyword, then the URI fields
    (both can repeat — e.g. multiple Model.profile rows)."""
    hints = []
    for key in ("Model.messageType", "keyword", "Model.profile", "conformsTo"):
        rows = instance_data[instance_data["KEY"] == key]
        hints.extend(str(value) for value in rows["VALUE"] if value is not None)
    return hints


def _first_value(instance_data, key):
    """VALUE of the first row with the given KEY, or None."""
    rows = instance_data[instance_data["KEY"] == key]
    if rows.empty:
        return None
    return rows.at[rows.index[0], "VALUE"]


def resolve_instance_config(instance_data, rdf_map, namespace_map=None):
    """Resolve per-instance export config shared by all cimxml engines.

    Returns
    -------
    tuple (file_name, namespace_map, instance_rdf_map)
        file_name : from the instance 'label' (source filename) or a new UUID
        namespace_map : given > instance NamespaceMap > schema ProfileNamespaceMap
        instance_rdf_map : profile section matched by the schema's own identity
            metadata (section key / keyword / versionIRI / conformsTo) against
            the instance header (Model.messageType, keyword, Model.profile,
            conformsTo); legacy URL-substring fallback for 2.4.15-era URLs;
            schema root when nothing matches
    """
    if not namespace_map:
        namespace_map, xml_base = get_namespace_map(instance_data)

    # Filename is kept under label
    file_name = _first_value(instance_data, "label") or f"{uuid.uuid4()}.xml"

    identity_index = _profile_identity_index(rdf_map)
    instance_section = None
    hints = _instance_profile_hints(instance_data)
    for hint in hints:
        if hint in identity_index:
            instance_section = identity_index[hint]
            break
    if instance_section is None:
        # legacy 2.4.15 profile URLs carry no exact schema identity — substring map
        for hint in hints:
            for url_part, section in PROFILE_URL_MAP.items():
                if url_part in hint:
                    instance_section = section
                    break
            if instance_section:
                break
    if instance_section is None and hints:
        logger.warning("No schema profile matched instance header hints %s — using schema root", hints[:4])

    instance_rdf_map = rdf_map.get(instance_section, rdf_map)

    # No map in function call, nor in instance data, use profile map
    if not namespace_map and instance_rdf_map:
        namespace_map = instance_rdf_map.get("ProfileNamespaceMap")

    return file_name, namespace_map, instance_rdf_map
