# -------------------------------------------------------------------------------
# Name:        export/cimxml_utils.py
# Purpose:     Shared helpers for CIM XML export engines (python_lxml, cython_pugixml)
# -------------------------------------------------------------------------------
import uuid
import logging

from triplets.tools import get_namespace_map
from triplets._engine_detect import flavor
from triplets._header import (  # noqa: F401 — load_rdf_map re-exported for the cimxml engines
    PROFILE_KEYS, PROFILE_URL_MAP, load_rdf_map, _profile_identity_index, merge_sections, profile_sections)
from triplets.iri import _sections

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
        instance_rdf_map : every profile section the instance header declares, merged
            (:func:`triplets._header.merge_sections`; a class concrete in any of them is
            instantiated, not described) — sections matched by the schema's own
            identity metadata (section key / keyword / versionIRI / profileVersionIRI /
            conformsTo) against the header (Model.messageType, keyword, Model.profile,
            conformsTo); legacy URL-substring fallback for 2.4.15-era URLs; the schema's
            root entries when nothing matches (warns on a sectioned schema)
    """
    if not namespace_map:
        namespace_map, xml_base = get_namespace_map(instance_data)

    # Filename is kept under label
    file_name = _first_value(instance_data, "label") or f"{uuid.uuid4()}.xml"

    hints = _instance_profile_hints(instance_data)
    sections = profile_sections(hints, rdf_map)
    if not sections and _sections(rdf_map):
        logger.warning("%s: header %s matches none of the schema profiles %s — no class or key is defined, "
                       "nothing is exported unless export_undefined=True", file_name, hints[:4], _sections(rdf_map))
    instance_rdf_map = merge_sections(rdf_map, sections or None)[0]

    # A class concrete in any declared profile is instantiated (its rdf:ID / rdf:about form), whatever
    # the order of the header's profile rows; a Description only when every declared profile describes it.
    for section in sections:
        for name, entry in rdf_map[section].items():
            if (isinstance(entry, dict) and entry.get("type") == "Class" and not entry.get("stereotyped")
                    and instance_rdf_map[name].get("stereotyped")):
                instance_rdf_map[name].update(attrib=entry["attrib"], stereotyped=False)

    # No map in function call, nor in instance data: the first declared profile's map
    if not namespace_map:
        namespace_map = (rdf_map[sections[0]] if sections else rdf_map).get("ProfileNamespaceMap")

    return file_name, namespace_map, instance_rdf_map
