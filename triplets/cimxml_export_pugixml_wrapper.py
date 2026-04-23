"""Python wrapper for pugixml-based CIM XML export.

Handles profile detection and rdf_map loading, then delegates
the heavy lifting to the Cython/pugixml generate_xml_bytes function.
"""
import json
import uuid
import logging

from triplets.cimxml_export_pugixml import generate_xml_bytes

logger = logging.getLogger(__name__)


def generate_xml(instance_data,
                 rdf_map=None,
                 namespace_map=None,
                 class_KEY="Type",
                 export_undefined=True,
                 comment=None,
                 debug=False):
    """Generate CIM RDF/XML using pugixml C++ DOM."""

    if not isinstance(rdf_map, dict):
        with open(rdf_map, "r") as f:
            rdf_map = json.load(f)

    if not namespace_map:
        from triplets.rdf_parser import get_namespace_map
        namespace_map, xml_base = get_namespace_map(instance_data)

    # Filename
    label_data = instance_data[instance_data["KEY"] == "label"]
    if not label_data.empty:
        file_name = label_data.at[label_data.index[0], 'VALUE']
    else:
        file_name = f"{uuid.uuid4()}.xml"

    # Detect profile type
    instance_type = None
    message_type_data = instance_data[instance_data["KEY"] == "Model.messageType"]
    profile_data = instance_data[instance_data["KEY"] == "Model.profile"]

    if not message_type_data.empty:
        instance_type = message_type_data.at[message_type_data.index[0], 'VALUE']

    if not instance_type and not profile_data.empty:
        instance_type_url = profile_data.at[profile_data.index[0], 'VALUE']
        profile_map = {
            "EquipmentCore": "EQ", "SteadyState": "SSH", "StateVariables": "SV",
            "Topology/": "TP", "EquipmentBoundary": "EQBD", "TopologyBoundary": "TPBD"
        }
        for key, value in profile_map.items():
            if key in instance_type_url:
                instance_type = value
                break

    instance_rdf_map = rdf_map.get(instance_type, rdf_map)
    if not namespace_map and instance_rdf_map:
        namespace_map = instance_rdf_map.get("ProfileNamespaceMap")

    if instance_rdf_map is None:
        if not export_undefined:
            return None
        instance_rdf_map = {}

    xml_bytes = generate_xml_bytes(
        instance_data,
        rdf_map,
        namespace_map,
        instance_rdf_map,
        file_name,
        class_KEY,
        export_undefined,
        comment,
    )

    return {"filename": file_name, "file": bytes(xml_bytes)}
