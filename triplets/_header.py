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
identity. Import-light on purpose: stdlib only.
"""

from .iri import load_rdf_map  # noqa: F401 — re-exported for the export engines

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
    for section_name, section in rdf_map.items():
        if not isinstance(section, dict):
            continue
        metadata = section.get("ProfileMetadata", {})
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
    the CIM XML exporter (first section) and ``cgmes_tools.declared_profiles`` (all of them)."""
    identity_index = _profile_identity_index(rdf_map) if identity_index is None else identity_index
    sections = list(dict.fromkeys(identity_index[hint] for hint in hints if hint in identity_index))
    if not sections:
        sections = list(dict.fromkeys(section for hint in hints for url_part, section in PROFILE_URL_MAP.items()
                                      if url_part in hint and section in rdf_map))
    return sections


def profile_section(hints, rdf_map, identity_index=None):
    """The first of :func:`profile_sections`, or None."""
    return next(iter(profile_sections(hints, rdf_map, identity_index)), None)
