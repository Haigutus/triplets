# -------------------------------------------------------------------------------
# Name:        export/cimxml_utils.py
# Purpose:     Shared helpers for CIM XML export engines (python_lxml, cython_pugixml)
# -------------------------------------------------------------------------------
import uuid
import logging

from triplets.tools import get_namespace_map
from triplets._engine_detect import flavor
from triplets.iri import split_iri
from triplets._header import (  # noqa: F401 — load_rdf_map re-exported for the cimxml engines
    PROFILE_KEYS, PROFILE_URL_MAP, load_rdf_map, _profile_identity_index, profile_section)

logger = logging.getLogger(__name__)



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

    hints = _instance_profile_hints(instance_data)
    instance_section = profile_section(hints, rdf_map)
    if instance_section is None and hints:
        logger.warning("No schema profile matched instance header hints %s — using schema root", hints[:4])

    instance_rdf_map = rdf_map.get(instance_section, rdf_map)

    # No map in function call, nor in instance data, use profile map
    if not namespace_map and instance_rdf_map:
        namespace_map = instance_rdf_map.get("ProfileNamespaceMap")

    return file_name, namespace_map, instance_rdf_map


def undefined_name(name, undefined_namespace):
    """(namespace, local name) an undefined class / KEY is written under: an absolute name
    (``iri_form="absolute"`` / an unbound prefix) keeps its own namespace, a local name takes
    *undefined_namespace*."""
    if str(name).startswith(("http://", "https://")):
        return split_iri(name)
    return undefined_namespace, name


def with_name_namespaces(namespace_map, instance_data, class_KEY):
    """*namespace_map* plus a generated prefix (``ns1``, …) for every namespace an absolute
    KEY / class name in *instance_data* uses that the map does not bind — both engines then
    write it as a prefixed element."""
    names = set(instance_data["KEY"].astype(str)) | set(
        instance_data.loc[instance_data["KEY"] == class_KEY, "VALUE"].astype(str))
    missing = sorted({split_iri(name)[0] for name in names if name.startswith(("http://", "https://"))}
                     - set(namespace_map.values()))
    extra, counter = {}, 1
    for namespace in missing:
        while f"ns{counter}" in namespace_map:
            counter += 1
        extra[f"ns{counter}"] = namespace
        counter += 1
    return {**namespace_map, **extra} if extra else namespace_map
