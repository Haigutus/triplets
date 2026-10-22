"""The export-schema scope of a validation run: which profiles of an rdf_map
it uses, merged into one, and the class inheritance they declare.

``rdf_map_scope`` names profiles by section key (``"EQ"``) or by any identifier
the profile declares (keyword / versionIRI / profileVersionIRI / conformsTo),
mixed, in any sequence. ``None`` is the rdf_map's root entries (an unsectioned,
hand-made schema; a sectioned bundle has none). ``"*"`` is every section.

Listed profiles are merged into one, never stricter than the profiles run one
by one — :func:`triplets._header.merge_sections` holds the rules: a CGMES 3.0 EQ
file is ``["EQ", "OP", "SC"]``. A run logs each datatype conflict as an error and
one warning for the relaxed constraints; the notes become the run's
``skipped_shapes`` (not checked) and ``relaxed_constraints`` (checked, more loosely).
"""
import logging

from .._header import merge_sections, profile_identifiers
from ..iri import _sections, absolute_name, is_iri, load_rdf_map, local_value, namespaces

logger = logging.getLogger(__name__)

ALL = "*"
SCOPE_KEY = "rdf_map_scope"   # dict key of the merged section — internal, never parsed or shown


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
        return _sections(rdf_map)
    index, valid = profile_identifiers(rdf_map), set(_sections(rdf_map))
    sections = []
    for identifier in map(str, identifiers):
        found = [name for name in index.get(identifier, ()) if name in valid]
        if not found:
            raise ValueError(f"unknown rdf_map_scope profile {identifier!r}; available: "
                             f"{', '.join(_sections(rdf_map))}")
        if len(found) > 1:
            raise ValueError(f"rdf_map_scope {identifier!r} is declared by several profiles "
                             f"({', '.join(found)}) — name them by section key")
        if found[0] not in sections:
            sections.append(found[0])
    return sections


def scoped_rdf_map(rdf_map, rdf_map_scope=None):
    """``({SCOPE_KEY: merged entries}, notes)`` — the one schema dict a run reads.

    The result keeps the sectioned shape every schema reader expects, with one
    section; its ``ProfileMetadata`` lists the scoped profiles (``sections``,
    and every field as a list — none for the root entries). ``notes`` holds the
    merge decisions as text: ``{"skipped_shapes": [...], "relaxed_constraints": [...]}``.
    """
    sections, merged, notes = _merged_entries(rdf_map, rdf_map_scope)
    for conflict in notes["skipped_shapes"]:
        logger.error("merged profiles %s: %s", sections, conflict)
    if notes["relaxed_constraints"]:
        logger.warning("merged profiles %s: %d constraints relaxed (see relaxed_constraints)",
                       sections, len(notes["relaxed_constraints"]))
    return {SCOPE_KEY: merged}, notes


def _merged_entries(rdf_map, rdf_map_scope):
    """(sections, merged entries, notes) — the merge without logging."""
    sections = scope_sections(rdf_map, rdf_map_scope)
    return (sections, *merge_sections(rdf_map, sections))


def class_ancestors(rdf_map, rdf_map_scope=None):
    """``{class local name: frozenset(ancestor IRIs as the schema records them)}`` over the scope.

    One map for every engine. Same-named Class entries of the scoped profiles
    union their ``inheritance`` lists; the relation is closed only through
    ancestors that have a Class entry of their own in the scope — another
    class's list is never read as a definition of its parents.
    """
    rdf_map = load_rdf_map(rdf_map)
    return _class_ancestors(_merged_entries(rdf_map, rdf_map_scope)[1] if rdf_map else {})


def _class_ancestors(entries):
    """:func:`class_ancestors` over already-merged scope entries."""
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


def require_entries(entries, rdf_map_scope, classes=False):
    """ValueError when the scope leaves nothing to validate against: no entries at
    all, or — *classes* — no Class entries (the schema validations' targets)."""
    found = (any(isinstance(entry, dict) and entry.get("type") == "Class" for entry in entries.values())
             if classes else bool(set(entries) - {"ProfileMetadata"}))
    if not found:
        raise ValueError(f"rdf_map_scope {rdf_map_scope!r} selects no {'Class ' if classes else ''}entries — "
                         "name the profiles (section keys / profile IRIs, or '*' for all)")


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
