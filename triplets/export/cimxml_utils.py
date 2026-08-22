# -------------------------------------------------------------------------------
# Name:        export/cimxml_utils.py
# Purpose:     Shared helpers for CIM XML export engines (python_lxml, cython_pugixml)
# -------------------------------------------------------------------------------
import uuid
import logging

from triplets.tools import get_namespace_map
from triplets._engine_detect import flavor
from triplets._header import (  # noqa: F401 — load_rdf_map re-exported for the cimxml engines
    PROFILE_KEYS, PROFILE_URL_MAP, load_rdf_map, _profile_identity_index)

logger = logging.getLogger(__name__)

# Namespace for internal/undefined structures when export_undefined=True
TRIPLETS_NS = "http://triplets#"


def _values_for_key(instance_data, key):
    """VALUEs of rows with the given KEY (pandas or polars frame), nulls dropped."""
    if flavor(instance_data) == "polars":
        import polars
        values = instance_data.filter(polars.col("KEY") == key)["VALUE"].to_list()
    else:
        values = instance_data.loc[instance_data["KEY"] == key, "VALUE"].tolist()
    return [value for value in values if value is not None]


def _instance_profile_hints(instance_data):
    """Profile references the instance header may carry, in priority order:
    old header messageType, new dcat:Dataset keyword, then the URI fields
    (both can repeat — e.g. multiple Model.profile rows)."""
    hints = []
    for key in PROFILE_KEYS:
        hints.extend(str(value) for value in _values_for_key(instance_data, key))
    return hints


def _first_value(instance_data, key):
    """VALUE of the first row with the given KEY, or None."""
    values = _values_for_key(instance_data, key)
    return values[0] if values else None


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
