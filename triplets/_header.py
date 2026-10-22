"""Instance-header vocabulary and profile identity, shared by cgmes_tools and
export; validation's ``rdf_map_scope`` resolves profiles through the same
schema identity (``profile_identifiers``) and never reads instance headers.

Header handling is key-driven: functions scan for these KEYs wherever they
appear — the header class (FullModel / dcat:Dataset / anything future) is
never matched against a whitelist, only reported verbatim. HEADER_TYPES is
the one exception: selecting whole header *objects* (tableviews) needs type
names.

Profile identity — which schema section a header declaration identifies —
also lives here: the schema's own ProfileMetadata is the authority
(_profile_identity_index), with the legacy 2.4 profile-URL substrings
(PROFILE_URL_MAP) as fallback for old-style headers that carry no exact
identity. So does the merge of several profiles into one schema
(merge_sections), shared by the CIM XML export and validation's
rdf_map_scope. Import-light on purpose: stdlib only.
"""

from .iri import _root_entries, _sections, load_rdf_map  # noqa: F401 — load_rdf_map re-exported for the export engines

# Profile identity a header may declare, in priority order: old FullModel
# header messageType, new dcat:Dataset keyword, then the URI fields
# (Model.profile / conformsTo can repeat).
PROFILE_KEYS = ("Model.messageType", "keyword", "Model.profile", "conformsTo")

# Dependency references between model parts: old header, new header.
REFERENCE_KEYS = ("Model.DependentOn", "requires")

# Known header classes — only for selecting whole header objects.
HEADER_TYPES = ("FullModel", "Dataset")

# Legacy fallback only: maps a substring of old-style (CGMES 2.4.15)
# Model.profile URLs to the schema section. Modern schemas resolve via their
# embedded ProfileMetadata (keyword / versionIRI / profileVersionIRI / conformsTo) instead.
PROFILE_URL_MAP = {
    "EquipmentCore": "EQ",
    "EquipmentOperation": "EQ",
    "EquipmentShortCircuit": "EQ",
    "SteadyState": "SSH",
    "StateVariables": "SV",
    "Topology/": "TP",
    "EquipmentBoundary": "EQBD",
    "TopologyBoundary": "TPBD",
    "DiagramLayout": "DL",
    "Dynamics": "DY",
    "GeographicalLocation": "GL",
    "FileHeader": "FH",
}


def profile_identifiers(rdf_map):
    """Every identifier a schema section declares → the sections declaring it, in schema order.

    Sections identify themselves via their key ("EQ") and the ProfileMetadata
    entry: keyword ("EQ"), versionIRI (the RDFS vocabulary's own IRI),
    profileVersionIRI (the profile IRI headers declare in Model.profile /
    conformsTo: from PROF when the bundle has it, else equal to versionIRI;
    they differ only in NC), conformsTo. The schema defines what to
    match — no hardcoded knowledge per CGMES generation. An identifier several
    sections share (e.g. the IEC document URN every CGMES 3.0 section carries as
    conformsTo) lists all of them.
    """
    identifiers = {}
    for section_name in _sections(rdf_map):
        metadata = rdf_map[section_name].get("ProfileMetadata", {})
        for identifier in (section_name, metadata.get("keyword"), metadata.get("versionIRI"),
                           metadata.get("profileVersionIRI"), metadata.get("conformsTo")):
            if isinstance(identifier, str) and identifier:
                sections = identifiers.setdefault(identifier, [])
                if section_name not in sections:
                    sections.append(section_name)
    return identifiers


def _profile_identity_index(rdf_map):
    """Identifier → section for every identifier exactly one section declares
    (:func:`profile_identifiers`); shared identifiers identify none of them."""
    return {identifier: sections[0] for identifier, sections in profile_identifiers(rdf_map).items()
            if len(sections) == 1}


def profile_sections(hints, rdf_map, identity_index=None):
    """Every schema section an instance's header hints resolve to, in hint-priority order:
    exact schema identities, else the legacy 2.4.15 profile-URL substrings. One rule for
    the CIM XML exporter and ``cgmes_tools.declared_profiles``."""
    identity_index = _profile_identity_index(rdf_map) if identity_index is None else identity_index
    sections = list(dict.fromkeys(identity_index[hint] for hint in hints if hint in identity_index))
    if not sections:
        sections = list(dict.fromkeys(section for hint in hints for url_part, section in PROFILE_URL_MAP.items()
                                      if url_part in hint and section in rdf_map))
    return sections


_PROPERTY_TYPES = ("Attribute", "Association", "Enumeration")


def merge_sections(rdf_map, sections):
    """(merged entries, notes) — the profile *sections* (section keys, in order) as one schema;
    ``None`` → the root entries. Same-named entries combine, never stricter than each profile
    alone:

    - Class: ``parameters`` and ``inheritance`` lists union.
    - Property cardinality: lowest ``xsd:minOccours``, highest ``xsd:maxOccours``
      (``n`` = unbounded; a missing bound is no bound).
    - Attribute ``xsd:type``: on a difference (a definition without a type counts) the
      intended type is unknown, so the key is left untyped (no datatype check, plain literal).
    - Enumeration ``values``: union.

    Every other field comes from the first definition. ``notes`` holds each decision as text,
    quoting every profile's own value: ``skipped_shapes`` (datatype conflicts) and
    ``relaxed_constraints`` (cardinalities, enumerations). The merged ProfileMetadata keeps
    every profile's identity: ``sections`` and each field as a list, in the same order.
    No logging: the caller decides what to report."""
    if sections is None:
        sources = [(None, _root_entries(rdf_map))]
    else:
        sources = [(section, rdf_map[section]) for section in sections]
    merged, notes = {}, {"skipped_shapes": [], "relaxed_constraints": []}
    if sections is not None:
        metadata = [rdf_map[section].get("ProfileMetadata", {}) for section in sections]
        fields = dict.fromkeys(field for each in metadata for field in each)
        merged["ProfileMetadata"] = {"sections": list(sections),
                                     **{field: [each.get(field) for each in metadata] for field in fields}}
    definitions = {}                       # name → [(section, entry)] in scope order
    for section, entries in sources:
        for name, entry in entries.items():
            if name != "ProfileMetadata":  # merged above, field by field
                definitions.setdefault(name, []).append((section, entry))
    for name, found in definitions.items():
        first = found[0][1]
        merged[name] = dict(first) if isinstance(first, dict) else first
        if len(found) > 1 and isinstance(first, dict):
            _merge_entry(name, merged[name], [(section, entry) for section, entry in found
                                              if isinstance(entry, dict)], notes)
    return merged, notes


def _merge_entry(name, merged, found, notes):
    """Combine every profile's definition of *name* (``found``: ``[(section, entry)]``)
    into *merged*; notes quote each profile's own value."""
    kind = merged.get("type")
    if kind == "Class":
        for field in ("parameters", "inheritance"):
            values = [value for _, entry in found for value in entry.get(field, ())]
            if values:
                merged[field] = list(dict.fromkeys(values))
        return
    if kind not in _PROPERTY_TYPES:
        return
    low = [entry.get("xsd:minOccours", "") for _, entry in found]
    high = [entry.get("xsd:maxOccours", "") for _, entry in found]
    if len(set(low)) > 1 or len(set(high)) > 1:
        # a missing / non-numeric bound is no bound: min 0, max unbounded
        merged["xsd:minOccours"] = min(low, key=int) if all(value.isdigit() for value in low) else "0"
        merged["xsd:maxOccours"] = max(high, key=int) if all(value.isdigit() for value in high) else "n"
        merged["multiplicity"] = f"{merged['xsd:minOccours']}..{merged['xsd:maxOccours']}"
        each = ", ".join(f"{section}: {lower}..{upper}" for (section, _), lower, upper in zip(found, low, high))
        notes["relaxed_constraints"].append(f"{name}: cardinality relaxed to {merged['multiplicity']} ({each})")
    types = [entry.get("xsd:type") for _, entry in found]
    if kind == "Attribute" and len(set(types)) > 1:
        merged.pop("xsd:type", None)
        each = ", ".join(f"{section}: {datatype or 'untyped'}" for (section, _), datatype in zip(found, types))
        notes["skipped_shapes"].append(f"{name}: datatype conflict ({each}) — not typed or checked by the schema")
    values = [entry.get("values", ()) for _, entry in found]
    if kind == "Enumeration" and len({frozenset(value) for value in values}) > 1:
        merged["values"] = list(dict.fromkeys(value for each in values for value in each))
        notes["relaxed_constraints"].append(
            f"{name}: enumeration values united ({' + '.join(section for section, _ in found)})")
