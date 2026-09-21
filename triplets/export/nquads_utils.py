"""N-Quads serialization — term quoting on top of the :mod:`triplets.iri` expand rules.

The IRI rules (what becomes a subject IRI, which namespace a KEY or enum
takes, what is a literal) live in ``triplets.iri``; this module only wraps the
results in ``<>`` / ``"..."^^<datatype>``. ``nquads_pandas`` / ``nquads_polars``
do the same vectorized through ``iri_pandas`` / ``iri_polars``.
"""
import json

from ..iri import expand_id, expand_key, expand_value


def escape_literal(text):
    """N-Triples string escaping (backslash, quote, newline, carriage return)."""
    return text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')


def make_subject(id_val):
    """ID → ``<urn:uuid:…>`` (an absolute IRI passes through)."""
    return f"<{expand_id(id_val)}>"


def make_predicate(key, terms=None):
    """KEY → ``<predicate>``: ``Type`` → rdf:type, else the schema namespace + KEY."""
    return f"<{expand_key(key, terms)}>"


def make_object(key, value, terms=None):
    """VALUE → ``<iri>`` or ``"literal"[^^<datatype>]`` per :func:`triplets.iri.expand_value`."""
    kind, payload = expand_value(key, value, terms)
    if kind == "iri":
        return f"<{payload}>"
    escaped = escape_literal(value)
    return f'"{escaped}"^^<{payload}>' if payload else f'"{escaped}"'


def make_graph(instance_id):
    """INSTANCE_ID → ``<urn:uuid:…>`` graph IRI (an absolute IRI passes through)."""
    return f"<{expand_id(instance_id)}>"


def flatten_schema(rdf_map):
    """Flatten the export schema across profiles for human-context lookups.

    The same class/property key repeats per profile with essentially the same
    definition — the first occurrence wins. Complements ``SchemaTerms`` (the
    machine fields); this pulls the human ones.

    Returns
    -------
    key_info : dict
        "Class.attr" → {"description", "multiplicity"} for property entries
        (Attribute / Association / Enumeration).
    class_info : dict
        "Class" → description for class entries.
    """
    if not isinstance(rdf_map, dict):
        with open(str(rdf_map)) as f:
            rdf_map = json.load(f)

    key_info = {}
    class_info = {}
    for profile_data in rdf_map.values():
        if not isinstance(profile_data, dict):
            continue
        for name, entry in profile_data.items():
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("type")
            if entry_type == "Class":
                class_info.setdefault(name, entry.get("description"))
            elif entry_type in ("Attribute", "Association", "Enumeration"):
                key_info.setdefault(name, {"description": entry.get("description"),
                                           "multiplicity": entry.get("multiplicity")})
    return key_info, class_info
