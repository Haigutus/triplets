"""CIM XML export using direct string concatenation.

Bypasses lxml entirely - builds XML as a list of strings and joins at the end.
This avoids all Element/QName object overhead.
"""
import json
import uuid
import logging

logger = logging.getLogger(__name__)


def _build_qname(namespace, tag):
    """Build a qualified XML tag string like <ns:Tag> from namespace URI and tag name."""
    # This is resolved at export time via the prefix map
    return namespace, tag


def generate_xml(instance_data,
                 rdf_map=None,
                 namespace_map=None,
                 class_KEY="Type",
                 export_undefined=True,
                 comment=None,
                 debug=False):
    """Generate CIM RDF/XML using direct string building."""

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

    # Build reverse namespace map: URI -> prefix
    uri_to_prefix = {}
    for prefix, uri in namespace_map.items():
        uri_to_prefix[uri] = prefix

    def _tag_for(namespace, local_name):
        """Return 'prefix:local_name' string."""
        if namespace is None:
            return local_name
        prefix = uri_to_prefix.get(namespace)
        if prefix:
            return f"{prefix}:{local_name}"
        return local_name

    def _attrib_tag(full_attrib):
        """Parse '{namespace}local' -> 'prefix:local'."""
        if full_attrib.startswith("{"):
            ns_end = full_attrib.index("}")
            ns = full_attrib[1:ns_end]
            local = full_attrib[ns_end + 1:]
            prefix = uri_to_prefix.get(ns)
            if prefix:
                return f"{prefix}:{local}"
            return local
        return full_attrib

    # Build XML as string parts
    parts = []
    parts.append('<?xml version=\'1.0\' encoding=\'UTF-8\'?>\n')

    if comment:
        parts.append(f'<!--{comment}-->\n')

    # RDF root with namespaces
    parts.append('<rdf:RDF')
    for prefix, uri in namespace_map.items():
        parts.append(f'\n  xmlns:{prefix}="{uri}"')
    parts.append('>\n')

    # Pre-extract numpy arrays for fast iteration
    ids = instance_data["ID"].values
    keys = instance_data["KEY"].values
    values = instance_data["VALUE"].values

    # First pass: create objects (class definitions)
    object_ids = set()
    class_lines = {}  # ID -> list of attribute XML strings

    for i in range(len(ids)):
        if keys[i] == class_KEY:
            obj_id = ids[i]
            class_name = values[i]
            object_ids.add(obj_id)

            class_def = instance_rdf_map.get(class_name)
            if class_def is not None:
                ns = class_def["namespace"]
                id_attr = _attrib_tag(class_def["attrib"]["attribute"])
                id_prefix = class_def["attrib"]["value_prefix"]
                tag = _tag_for(ns, class_name)
            elif export_undefined:
                id_attr = "rdf:about"
                id_prefix = "urn:uuid:"
                tag = class_name
            else:
                continue

            class_lines[obj_id] = {
                "open": f'  <{tag} {id_attr}="{id_prefix}{obj_id}">',
                "close": f'  </{tag}>',
                "attrs": [],
            }

    # Second pass: add attributes
    for i in range(len(ids)):
        key = keys[i]
        if key == class_KEY:
            continue

        obj_id = ids[i]
        value = values[i]

        obj = class_lines.get(obj_id)
        if obj is None:
            continue

        # Skip NaN/None
        if value is None or (isinstance(value, float) and value != value):
            continue

        tag_def = instance_rdf_map.get(key)
        if tag_def is not None:
            ns = tag_def["namespace"]
            tag = _tag_for(ns, key)
            attrib = tag_def.get("attrib")
            text_prefix = tag_def.get("text", "")

            if attrib:
                attr_name = _attrib_tag(attrib["attribute"])
                value_prefix = attrib.get("value_prefix", "")
                if not value_prefix:
                    value_prefix = instance_rdf_map.get(value, {}).get("namespace", "")
                obj["attrs"].append(f'    <{tag} {attr_name}="{value_prefix}{value}"/>')
            else:
                obj["attrs"].append(f'    <{tag}>{text_prefix}{value}</{tag}>')
        elif export_undefined:
            obj["attrs"].append(f'    <{key}>{value}</{key}>')

    # Assemble XML
    for obj_id, obj in class_lines.items():
        parts.append(obj["open"])
        parts.append('\n')
        for attr in obj["attrs"]:
            parts.append(attr)
            parts.append('\n')
        parts.append(obj["close"])
        parts.append('\n')

    parts.append('</rdf:RDF>\n')

    xml_bytes = ''.join(parts).encode('utf-8')
    return {"filename": file_name, "file": xml_bytes}
