"""CIM XML export using Polars for data access + string building.

Converts the pandas DataFrame to Polars first, then uses Polars' fast
groupby and filter operations to build XML strings.
"""
import json
import uuid
import logging

import polars as pl

logger = logging.getLogger(__name__)


def generate_xml(instance_data,
                 rdf_map=None,
                 namespace_map=None,
                 class_KEY="Type",
                 export_undefined=True,
                 comment=None,
                 debug=False):
    """Generate CIM RDF/XML using Polars for data access."""

    if not isinstance(rdf_map, dict):
        with open(rdf_map, "r") as f:
            rdf_map = json.load(f)

    # Convert to polars if needed
    if not isinstance(instance_data, pl.DataFrame):
        df = pl.from_pandas(instance_data)
    else:
        df = instance_data

    if not namespace_map:
        # Extract namespace map from data
        ns_rows = df.filter(pl.col("KEY") == "NamespaceMap")
        if not ns_rows.is_empty():
            namespace_map = {}
            for row in ns_rows.iter_rows(named=True):
                namespace_map[row["ID"]] = row["VALUE"]

    # Filename
    label_rows = df.filter(pl.col("KEY") == "label")
    if not label_rows.is_empty():
        file_name = label_rows.row(0, named=True)["VALUE"]
    else:
        file_name = f"{uuid.uuid4()}.xml"

    # Detect profile type
    instance_type = None
    mt_rows = df.filter(pl.col("KEY") == "Model.messageType")
    if not mt_rows.is_empty():
        instance_type = mt_rows.row(0, named=True)["VALUE"]

    if not instance_type:
        prof_rows = df.filter(pl.col("KEY") == "Model.profile")
        if not prof_rows.is_empty():
            instance_type_url = prof_rows.row(0, named=True)["VALUE"]
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

    # Build reverse namespace map
    uri_to_prefix = {}
    for prefix, uri in namespace_map.items():
        uri_to_prefix[uri] = prefix

    def _tag_for(ns, local_name):
        if ns is None:
            return local_name
        p = uri_to_prefix.get(ns)
        return f"{p}:{local_name}" if p else local_name

    def _parse_attrib(full_attrib):
        if full_attrib.startswith("{"):
            ns_end = full_attrib.index("}")
            ns = full_attrib[1:ns_end]
            local = full_attrib[ns_end + 1:]
            p = uri_to_prefix.get(ns)
            return f"{p}:{local}" if p else local
        return full_attrib

    # Build XML
    parts = []
    parts.append("<?xml version='1.0' encoding='UTF-8'?>\n")

    if comment:
        parts.append(f"<!--{comment}-->\n")

    parts.append("<rdf:RDF")
    for prefix, uri in namespace_map.items():
        parts.append(f'\n  xmlns:{prefix}="{uri}"')
    parts.append(">\n")

    # Get class rows and attribute rows
    class_df = df.filter(pl.col("KEY") == class_KEY)
    attr_df = df.filter((pl.col("KEY") != class_KEY) & pl.col("VALUE").is_not_null())

    # Group attributes by ID for fast lookup
    attr_groups = {}
    for row in attr_df.iter_rows():
        obj_id = row[0]  # ID
        if obj_id not in attr_groups:
            attr_groups[obj_id] = []
        attr_groups[obj_id].append((row[1], row[2]))  # KEY, VALUE

    # Build objects
    for row in class_df.iter_rows():
        obj_id = row[0]   # ID
        class_name = row[2]  # VALUE

        class_def = instance_rdf_map.get(class_name)
        if class_def is not None:
            tag = _tag_for(class_def["namespace"], class_name)
            id_attr = _parse_attrib(class_def["attrib"]["attribute"])
            id_prefix = class_def["attrib"]["value_prefix"]
        elif export_undefined:
            tag = class_name
            id_attr = "rdf:about"
            id_prefix = "urn:uuid:"
        else:
            continue

        parts.append(f'  <{tag} {id_attr}="{id_prefix}{obj_id}">\n')

        # Add attributes for this object
        attrs = attr_groups.get(obj_id, [])
        for attr_key, attr_value in attrs:
            tag_def = instance_rdf_map.get(attr_key)
            if tag_def is not None:
                ns = tag_def["namespace"]
                attr_tag = _tag_for(ns, attr_key)
                attrib = tag_def.get("attrib")
                text_prefix = tag_def.get("text", "")

                if attrib:
                    a_name = _parse_attrib(attrib["attribute"])
                    vp = attrib.get("value_prefix", "")
                    if not vp:
                        vp = instance_rdf_map.get(attr_value, {}).get("namespace", "")
                    parts.append(f'    <{attr_tag} {a_name}="{vp}{attr_value}"/>\n')
                else:
                    parts.append(f'    <{attr_tag}>{text_prefix}{attr_value}</{attr_tag}>\n')
            elif export_undefined:
                parts.append(f'    <{attr_key}>{attr_value}</{attr_key}>\n')

        parts.append(f'  </{tag}>\n')

    parts.append("</rdf:RDF>\n")

    xml_bytes = ''.join(parts).encode('utf-8')
    return {"filename": file_name, "file": xml_bytes}
