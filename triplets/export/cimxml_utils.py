# -------------------------------------------------------------------------------
# Name:        export/cimxml_utils.py
# Purpose:     Shared helpers for CIM XML export engines (python_lxml, cython_pugixml)
# -------------------------------------------------------------------------------
import json
import uuid
import logging

from triplets.tools import get_namespace_map

logger = logging.getLogger(__name__)

# Maps a substring of the Model.profile URL to the export schema sub-key.
# Used when the instance header has no Model.messageType (e.g. CGMES 3.0).
# TODO - needs to be extended and made more intelligent, maybe scan profile?
PROFILE_URL_MAP = {
    "EquipmentCore": "EQ",
    "SteadyState": "SSH",
    "StateVariables": "SV",
    "Topology/": "TP",
    "EquipmentBoundary": "EQBD",
    "TopologyBoundary": "TPBD",
}


def load_rdf_map(rdf_map):
    """Return the export schema as a dict; load from JSON file path if needed."""
    if isinstance(rdf_map, dict):
        return rdf_map
    with open(rdf_map, "r") as conf_file:
        return json.load(conf_file)


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
        instance_rdf_map : profile sub-schema (via Model.messageType or
            Model.profile URL) or the schema root when no profile matches
    """
    if not namespace_map:
        namespace_map, xml_base = get_namespace_map(instance_data)

    # Filename is kept under label
    file_name = _first_value(instance_data, "label") or f"{uuid.uuid4()}.xml"

    # Find schema reference to be used for export
    # TODO remove dependency on this header field, which might not be present
    # TODO: Refactor this, if schema is provided, the information how to pick it up should be in the schema
    instance_type = _first_value(instance_data, "Model.messageType")

    if not instance_type:
        profile_url = _first_value(instance_data, "Model.profile")
        if profile_url:
            for url_part, profile in PROFILE_URL_MAP.items():
                if url_part in profile_url:
                    instance_type = profile
                    break

    # If there is sub structure available in schema get it, otherwise use root definitions
    # TODO - needs revision, add support both for md:FullModel, dcat:DataSet and without profile definiton
    instance_rdf_map = rdf_map.get(instance_type, rdf_map)

    # No map in function call, nor in instance data, use profile map
    if not namespace_map and instance_rdf_map:
        namespace_map = instance_rdf_map.get("ProfileNamespaceMap")

    return file_name, namespace_map, instance_rdf_map
