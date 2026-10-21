"""The export-schema scope of a validation run: which profiles of an rdf_map
it uses, merged into one, and the class inheritance they declare.

``rdf_map_scope`` names profiles by section key (``"EQ"``) or by any identifier
the profile declares (keyword / versionIRI / profileVersionIRI / conformsTo),
mixed, in any sequence. ``None`` is the rdf_map's root entries (an unsectioned,
hand-made schema; a sectioned bundle has none). ``"*"`` is every section.

Listed profiles are merged into one: a CGMES 3.0 EQ file is ``["EQ", "OP", "SC"]``.
Same-named entries combine, never stricter than the profiles run one by one:

- Class: ``parameters`` and ``inheritance`` lists union.
- Property cardinality: lowest ``xsd:minOccours``, highest ``xsd:maxOccours``
  (``n`` = unbounded) — a warning per run names the relaxed keys.
- Attribute ``xsd:type``: on a difference the intended type is unknown, so the
  key is left untyped (no datatype check, plain literal) — logged as an error.
- Enumeration ``values``: union, with the same warning.

Every conflict is also returned as text for the run's ``skipped_shapes``. The
merged ProfileMetadata keeps every profile's identity: ``sections`` (the
section keys, in scope order) and each field as a list in the same order.
"""
import logging

from .._header import profile_identifiers
from ..iri import absolute_name, is_iri, local_value, namespaces

logger = logging.getLogger(__name__)

ALL = "*"
SCOPE_KEY = "rdf_map_scope"   # dict key of the merged section — internal, never parsed or shown
_PROPERTY_TYPES = ("Attribute", "Association", "Enumeration")


def _is_section(value):
    """A profile section — a dict of entries; an entry is a dict with a ``type`` string
    (a section may hold an entry *named* ``type``)."""
    return isinstance(value, dict) and not isinstance(value.get("type"), str)


def scope_sections(rdf_map, rdf_map_scope):
    """Section keys an ``rdf_map_scope`` names, in the given order; ``None`` → None (root).

    Unknown identifiers, identifiers several sections declare and ``"*"``
    mixed with other identifiers raise ValueError.
    """
    if rdf_map_scope is None:
        return None
    identifiers = [rdf_map_scope] if isinstance(rdf_map_scope, str) else list(rdf_map_scope)
    if ALL in identifiers:
        if len(identifiers) > 1:
            raise ValueError(f"rdf_map_scope {ALL!r} means every profile — do not combine it with others")
        return [name for name, value in rdf_map.items() if _is_section(value)]
    index = profile_identifiers(rdf_map)
    sections = []
    for identifier in map(str, identifiers):
        found = index.get(identifier)
        if not found:
            raise ValueError(f"unknown rdf_map_scope profile {identifier!r}; available: "
                             f"{', '.join(name for name, value in rdf_map.items() if _is_section(value))}")
        if len(found) > 1:
            raise ValueError(f"rdf_map_scope {identifier!r} is declared by several profiles "
                             f"({', '.join(found)}) — name them by section key")
        if found[0] not in sections:
            sections.append(found[0])
    return sections


def scoped_rdf_map(rdf_map, rdf_map_scope=None):
    """``({SCOPE_KEY: merged entries}, conflicts)`` — the one schema dict a run reads.

    The result keeps the sectioned shape every schema reader expects, with one
    section; its ``ProfileMetadata`` lists the scoped profiles (``sections``,
    and every field as a list — none for the root entries). ``conflicts`` lists
    the merge decisions as text (see module docstring).
    """
    sections, merged, conflicts = _merged_entries(rdf_map, rdf_map_scope)
    for conflict in conflicts:
        if "datatype conflict" in conflict:
            logger.error("merged profiles %s: %s", sections, conflict)
    if conflicts:
        logger.warning("merged profiles %s: %d schema conflicts resolved (see skipped_shapes)",
                       sections, len(conflicts))
    return {SCOPE_KEY: merged}, conflicts


def _merged_entries(rdf_map, rdf_map_scope):
    """(sections, merged entries, conflicts) — the merge without logging."""
    sections = scope_sections(rdf_map, rdf_map_scope)
    if sections is None:
        sources = [(None, {name: entry for name, entry in rdf_map.items()
                           if isinstance(entry, dict) and not _is_section(entry)})]
    else:
        sources = [(section, rdf_map[section]) for section in sections]
    merged, origin, conflicts = {}, {}, []
    if sections is not None:
        metadata = [rdf_map[section].get("ProfileMetadata", {}) for section in sections]
        fields = dict.fromkeys(field for each in metadata for field in each)
        merged["ProfileMetadata"] = {"sections": list(sections),
                                     **{field: [each.get(field) for each in metadata] for field in fields}}
    for section, entries in sources:
        for name, entry in entries.items():
            if name == "ProfileMetadata":      # merged above, field by field
                continue
            if name not in merged:
                merged[name], origin[name] = (dict(entry) if isinstance(entry, dict) else entry), section
            elif isinstance(entry, dict) and isinstance(merged[name], dict):
                _merge_entry(name, merged[name], entry, origin[name], section, conflicts)
    return sections, merged, conflicts


def _merge_entry(name, merged, entry, first, section, conflicts):
    """Combine a later profile's *entry* into *merged* (the rules in the module docstring)."""
    kind = merged.get("type")
    if kind == "Class":
        for field in ("parameters", "inheritance"):
            if entry.get(field):
                merged[field] = list(dict.fromkeys([*merged.get(field, ()), *entry[field]]))
        return
    if kind not in _PROPERTY_TYPES:
        return
    low = [merged.get("xsd:minOccours", ""), entry.get("xsd:minOccours", "")]
    high = [merged.get("xsd:maxOccours", ""), entry.get("xsd:maxOccours", "")]
    if low[0] != low[1] or high[0] != high[1]:
        merged["xsd:minOccours"] = min(low, key=lambda value: int(value) if value.isdigit() else 0)
        merged["xsd:maxOccours"] = ("n" if "n" in high else
                                    max(high, key=lambda value: int(value) if value.isdigit() else -1))
        merged["multiplicity"] = f"{merged['xsd:minOccours']}..{merged['xsd:maxOccours']}"
        conflicts.append(f"{name}: cardinality relaxed to {merged['multiplicity']} "
                         f"({first}: {low[0]}..{high[0]}, {section}: {low[1]}..{high[1]})")
    datatype = merged.get("xsd:type")
    if kind == "Attribute" and datatype and entry.get("xsd:type", datatype) != datatype:
        conflicts.append(f"{name}: datatype conflict ({first}: {merged.pop('xsd:type')}, "
                         f"{section}: {entry['xsd:type']}) — not checked")
    if kind == "Enumeration" and set(entry.get("values", ())) != set(merged.get("values", ())):
        merged["values"] = list(dict.fromkeys([*merged.get("values", ()), *entry.get("values", ())]))
        conflicts.append(f"{name}: enumeration values united ({first} + {section})")


def class_ancestors(rdf_map, rdf_map_scope=None):
    """``{class local name: frozenset(ancestor IRIs as the schema records them)}`` over the scope.

    One map for every engine. Same-named Class entries of the scoped profiles
    union their ``inheritance`` lists; the relation is closed only through
    ancestors that have a Class entry of their own in the scope — another
    class's list is never read as a definition of its parents.
    """
    entries = _merged_entries(rdf_map, rdf_map_scope)[1] if rdf_map else {}
    direct = {name: {step for step in entry.get("inheritance", ()) if local_value(step, "class") != name}
              for name, entry in entries.items()
              if isinstance(entry, dict) and entry.get("type") == "Class"}
    closed = {}

    def walk(name, seen):
        if name not in closed:
            reached = set(direct[name])
            for step in direct[name]:
                parent = local_value(step, "class")
                if parent in direct and parent not in seen:
                    reached |= walk(parent, seen | {name})
            closed[name] = frozenset(reached)
        return closed[name]

    return {name: walk(name, frozenset()) for name in direct}


def ancestor_pairs(ancestors):
    """``(Type, ancestor)`` local-name pairs — the vectorized engines' type widening."""
    return sorted({(name, local_value(step, "class")) for name, steps in (ancestors or {}).items()
                   for step in steps if local_value(step, "class") != name})


def ancestor_triples(ancestors, rdf_map, undefined_namespace):
    """``(child IRI, parent IRI)`` for pyshacl's ``rdfs:subClassOf`` ont_graph — the same
    pairs as :func:`ancestor_pairs`. The child resolves like the data graph's ``rdf:type``
    (schema namespace, else *undefined_namespace*); recorded parent IRIs pass through."""
    names = namespaces(rdf_map)
    return sorted({(absolute_name(name, names, undefined_namespace),
                    step if is_iri(step) else absolute_name(local_value(step, "class"), names, undefined_namespace))
                   for name, steps in (ancestors or {}).items()
                   for step in steps if local_value(step, "class") != name})
