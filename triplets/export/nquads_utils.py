"""Shared N-Quads logic — schema parsing and value classification.

Used by nquads_pandas.py and nquads_polars.py.
"""

import re
import json

CIM_NS = "http://iec.ch/TC57/CIM100#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def build_key_metadata(rdf_map):
    """Extract enum keys and key→namespace mapping from export schema.

    Parameters
    ----------
    rdf_map : dict or str
        Export schema (loaded JSON dict or path to JSON file).

    Returns
    -------
    enum_keys : set
        KEY names whose values are enumerations (need namespace on VALUE).
    key_namespaces : dict
        KEY name → namespace URI for predicate construction.
    """
    if not isinstance(rdf_map, dict):
        with open(str(rdf_map)) as f:
            rdf_map = json.load(f)

    enum_keys = set()
    key_namespaces = {}

    for profile_name, profile_data in rdf_map.items():
        if not isinstance(profile_data, dict):
            continue
        for prop_name, prop_data in profile_data.items():
            if not isinstance(prop_data, dict):
                continue
            prop_type = prop_data.get("type")
            namespace = prop_data.get("namespace", CIM_NS)

            if prop_type == "Enumeration":
                enum_keys.add(prop_name)
            if namespace:
                key_namespaces[prop_name] = namespace

    return enum_keys, key_namespaces


def make_subject(id_val):
    """Convert ID to subject URI."""
    if id_val.startswith("http://") or id_val.startswith("https://") or id_val.startswith("urn:"):
        return f"<{id_val}>"
    return f"<urn:uuid:{id_val}>"


def make_predicate(key, key_namespaces=None):
    """Convert KEY to predicate URI."""
    if key == "Type":
        return f"<{RDF_TYPE}>"
    if key.startswith("http://") or key.startswith("https://"):
        return f"<{key}>"
    ns = key_namespaces.get(key, CIM_NS) if key_namespaces else CIM_NS
    return f"<{ns}{key}>"


def make_object(key, value, enum_keys=None):
    """Convert VALUE to object (URI or literal).

    Rules:
    - Type row → <namespace#ClassName>
    - Already starts with http/https/urn → <value> (pass through)
    - Enum KEY → <namespace#EnumValue>
    - UUID pattern → <urn:uuid:value>
    - Everything else → "literal" (with escaping)
    """
    if key == "Type":
        if value.startswith("http://") or value.startswith("urn:"):
            return f"<{value}>"
        return f"<{CIM_NS}{value}>"

    # Already a full URI
    if value.startswith("http://") or value.startswith("https://") or value.startswith("urn:"):
        return f"<{value}>"

    # Enumeration value — add namespace
    if enum_keys and key in enum_keys:
        return f"<{CIM_NS}{value}>"

    # UUID reference
    if UUID_RE.match(value):
        return f"<urn:uuid:{value}>"

    # Literal — escape for N-Triples
    escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return f'"{escaped}"'


def make_graph(instance_id):
    """Convert INSTANCE_ID to graph URI."""
    if instance_id.startswith("http://") or instance_id.startswith("urn:"):
        return f"<{instance_id}>"
    return f"<urn:uuid:{instance_id}>"
