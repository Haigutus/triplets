# -------------------------------------------------------------------------------
# Name:        export/cimxml_utils.py
# Purpose:     Shared helpers for CIM XML export engines (python_lxml, cython_pugixml)
# -------------------------------------------------------------------------------
import uuid
import logging
from itertools import chain, count

from triplets.tools import get_namespace_map
from triplets._engine_detect import flavor
from triplets._header import (  # noqa: F401 — load_rdf_map re-exported for the cimxml engines
    PROFILE_KEYS, PROFILE_URL_MAP, load_rdf_map, _profile_identity_index, merge_sections, profile_sections)
from triplets.iri import RDF_NS, TYPE_KEY, _root_entries, _sections

logger = logging.getLogger(__name__)



HEADER_KEYS = (*PROFILE_KEYS, "label")


def _header_rows(instance_data):
    """``(ID, KEY, VALUE)`` of the rows resolve_instance_config reads — the profile keys and
    ``label`` — in one pass over the instance (pandas or polars frame), nulls dropped."""
    if flavor(instance_data) == "polars":
        import polars
        rows = instance_data.filter(polars.col("KEY").is_in(HEADER_KEYS)).select("ID", "KEY", "VALUE")
        return rows.filter(polars.col("VALUE").is_not_null()).rows()
    rows = instance_data.loc[instance_data["KEY"].isin(HEADER_KEYS), ["ID", "KEY", "VALUE"]]
    return list(rows.loc[rows["VALUE"].notna()].itertuples(index=False, name=None))


def _instance_profile_hints(header_rows):
    """Profile references the instance header may carry, in priority order:
    old header messageType, new dcat:Dataset keyword, then the URI fields
    (both can repeat — e.g. multiple Model.profile rows)."""
    return [str(value) for profile_key in PROFILE_KEYS for _, key, value in header_rows if key == profile_key]


def _distribution_label(instance_data, header_rows):
    """``label`` of the instance's ``Distribution`` object (the source file name the parsers
    write), or None — a ``label`` row of any other object is data, not the file name."""
    labels = [(object_id, value) for object_id, key, value in header_rows if key == "label"]
    if not labels:
        return None
    labelled = [object_id for object_id, _ in labels]      # few objects: one ID pass finds their types
    if flavor(instance_data) == "polars":
        import polars
        distributions = instance_data.filter(polars.col("ID").is_in(labelled) & (polars.col("KEY") == TYPE_KEY)
                                             & (polars.col("VALUE") == "Distribution"))["ID"].to_list()
    else:
        owners = instance_data.loc[instance_data["ID"].isin(labelled)]
        distributions = owners.loc[(owners["KEY"] == TYPE_KEY) & (owners["VALUE"] == "Distribution"), "ID"].tolist()
    return next((value for object_id, value in labels if object_id in distributions), None)


def _root_classes(instance_data, rdf_map):
    """Whether a root Class entry of *rdf_map* defines a ``Type`` of the instance — what the
    export writes for a header that matches no profile section."""
    classes = [name for name, entry in _root_entries(rdf_map).items() if entry.get("type") == "Class"]
    if not classes:
        return False
    if flavor(instance_data) == "polars":
        import polars
        return not instance_data.filter((polars.col("KEY") == TYPE_KEY) & polars.col("VALUE").is_in(classes)).is_empty()
    return bool(((instance_data["KEY"] == TYPE_KEY) & instance_data["VALUE"].isin(classes)).any())


def resolve_instance_config(instance_data, rdf_map, namespace_map=None):
    """Resolve per-instance export config shared by all cimxml engines.

    Returns
    -------
    tuple (file_name, namespace_map, instance_rdf_map)
        - file_name : the ``label`` of the instance's ``Distribution`` object (source filename),
          or a new ``<uuid>.xml``
        - namespace_map : given > instance NamespaceMap > schema ProfileNamespaceMap
        - instance_rdf_map : every profile section the instance header declares, merged
          (:func:`triplets._header.merge_sections`; a class concrete in any of them is
          instantiated, not described) — sections matched by the schema's own
          identity metadata (section key / keyword / versionIRI / profileVersionIRI /
          conformsTo) against the header (Model.messageType, keyword, Model.profile,
          conformsTo); legacy URL-substring fallback for 2.4.15-era URLs; the schema's
          root entries when nothing matches (warns when the schema has sections and its
          root entries define no class of the instance)
    """
    if not namespace_map:
        namespace_map, xml_base = get_namespace_map(instance_data)

    header_rows = _header_rows(instance_data)
    file_name = _distribution_label(instance_data, header_rows) or f"{uuid.uuid4()}.xml"

    hints = _instance_profile_hints(header_rows)
    sections = profile_sections(hints, rdf_map)
    if not sections and _sections(rdf_map) and not _root_classes(instance_data, rdf_map):
        logger.warning("%s: header %s matches none of the schema profiles %s — no class of the instance is defined, "
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


def declared_namespaces(namespace_map, instance_rdf_map, export_undefined, undefined_namespace):
    """The prefix → namespace map an instance is written with (both cimxml engines).

    - ``export_undefined``: *undefined_namespace* is bound to ``triplets`` unless the map
      binds it already (e.g. CIM100 keeps its own prefix).
    - A class without a namespace (a ``Class`` entry with ``"namespace": ""``) is only in
      no namespace while no default namespace is declared: with an entry that has an empty
      namespace the map's default namespace (prefix ``""`` or ``None``) is left out, and
      the engines write its names under a generated prefix.
    - The RDF namespace is bound to a prefix that is not empty: ``rdf`` when the map leaves
      it out or has it only as the default namespace, else the first free ``ns0``, ``ns1``, ….
    """
    namespace_map = dict(namespace_map or {})
    if any(isinstance(entry, dict) and "namespace" in entry and not entry["namespace"]
           for entry in instance_rdf_map.values()):
        namespace_map = {prefix: uri for prefix, uri in namespace_map.items() if prefix}
    if export_undefined and undefined_namespace not in namespace_map.values():
        namespace_map["triplets"] = undefined_namespace
    if not any(prefix and uri == RDF_NS for prefix, uri in namespace_map.items()):
        names = chain(("rdf",), map("ns{}".format, count()))
        namespace_map[next(name for name in names if name not in namespace_map)] = RDF_NS
    return namespace_map
