"""CIM XML export using lxml with optimized data access.

Key optimizations over original:
- Pre-filter and group data before iteration
- Use numpy array access instead of itertuples()
- Pre-build tag/attrib lookup dicts to avoid repeated map lookups
- Batch element creation
"""
import json
import uuid
import logging

from lxml import etree
from lxml.builder import ElementMaker
from lxml.etree import QName
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=500)
def _get_qname(namespace, tag=None):
    if tag:
        return QName(namespace, tag)
    return QName(namespace)


def generate_xml(instance_data,
                 rdf_map=None,
                 namespace_map=None,
                 class_KEY="Type",
                 export_undefined=True,
                 comment=None,
                 debug=False):
    """Generate CIM RDF/XML with optimized lxml usage."""

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

    # Pre-build tag cache: KEY -> (qname, attrib_qname, value_prefix, text_prefix, is_ref)
    tag_cache = {}
    for key, tag_def in instance_rdf_map.items():
        if not isinstance(tag_def, dict):
            continue
        if tag_def.get("type") == "Class":
            continue
        ns = tag_def.get("namespace")
        if not ns:
            continue
        qn = _get_qname(ns, key)
        attrib = tag_def.get("attrib")
        text_prefix = tag_def.get("text", "")
        if attrib:
            attr_qn = _get_qname(attrib["attribute"])
            vp = attrib.get("value_prefix", "")
            tag_cache[key] = (qn, attr_qn, vp, text_prefix, True)
        else:
            tag_cache[key] = (qn, None, "", text_prefix, False)

    # Create root
    E = ElementMaker(nsmap=namespace_map)
    rdf_ns = namespace_map.get("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    RDF = E(QName(rdf_ns, "RDF"))

    if comment:
        RDF.addprevious(etree.Comment(str(comment)))

    # Extract arrays for fast access
    ids = instance_data["ID"].values
    keys_arr = instance_data["KEY"].values
    values_arr = instance_data["VALUE"].values

    # First pass: create objects
    objects = {}
    for i in range(len(ids)):
        if keys_arr[i] != class_KEY:
            continue

        obj_id = ids[i]
        class_name = values_arr[i]
        class_def = instance_rdf_map.get(class_name)

        if class_def is not None:
            rdf_object = E(_get_qname(class_def["namespace"], class_name))
            rdf_object.attrib[_get_qname(class_def["attrib"]["attribute"])] = \
                f"{class_def['attrib']['value_prefix']}{obj_id}"
        elif export_undefined:
            rdf_object = E(_get_qname(None, class_name))
            rdf_object.attrib[_get_qname("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about")] = \
                f"urn:uuid:{obj_id}"
        else:
            continue

        RDF.append(rdf_object)
        objects[obj_id] = rdf_object

    # Second pass: add attributes using numpy arrays
    for i in range(len(ids)):
        key = keys_arr[i]
        if key == class_KEY:
            continue

        obj_id = ids[i]
        _object = objects.get(obj_id)
        if _object is None:
            continue

        value = values_arr[i]
        if value is None or (isinstance(value, float) and value != value):
            continue

        cached = tag_cache.get(key)
        if cached is not None:
            qn, attr_qn, vp, text_prefix, is_ref = cached
            tag = E(qn)
            if is_ref:
                if not vp:
                    vp = instance_rdf_map.get(value, {}).get("namespace", "")
                tag.attrib[attr_qn] = f"{vp}{value}"
            else:
                tag.text = f"{text_prefix}{value}"
            _object.append(tag)
        elif export_undefined:
            tag = E(key)
            tag.text = str(value)
            _object.append(tag)

    xml = etree.tostring(RDF, pretty_print=True, xml_declaration=True, encoding='UTF-8')
    return {"filename": file_name, "file": xml}
