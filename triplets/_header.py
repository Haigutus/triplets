"""Instance-header vocabulary and profile identity, shared by cgmes_tools,
validation and export.

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
import json

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
# embedded ProfileMetadata (keyword / versionIRI / conformsTo) instead.
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


def load_rdf_map(rdf_map):
    """Return the export schema as a dict; load from JSON file path if needed."""
    if isinstance(rdf_map, dict):
        return rdf_map
    with open(rdf_map, "r") as conf_file:
        return json.load(conf_file)


def _profile_identity_index(rdf_map):
    """Map every identifier a schema section declares to the section name.

    Sections identify themselves via their key ("EQ") and the ProfileMetadata
    entry: keyword ("EQ"), versionIRI (the profile URI, matches old-header
    Model.profile), conformsTo. The schema defines what to match — no
    hardcoded knowledge per CGMES generation. An identifier several sections
    share (e.g. the IEC document URN every CGMES 3.0 section carries as
    conformsTo) identifies none of them and is dropped from the index.
    """
    index, ambiguous = {}, set()
    for section_name, section in rdf_map.items():
        if not isinstance(section, dict):
            continue
        metadata = section.get("ProfileMetadata", {})
        identifiers = [section_name, metadata.get("keyword"),
                       metadata.get("versionIRI"), metadata.get("conformsTo")]
        for identifier in identifiers:
            if isinstance(identifier, str) and identifier:
                if index.setdefault(identifier, section_name) != section_name:
                    ambiguous.add(identifier)
    for identifier in ambiguous:
        del index[identifier]
    return index
