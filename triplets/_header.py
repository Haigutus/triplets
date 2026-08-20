"""Instance-header vocabulary shared by cgmes_tools, validation and export.

Header handling is key-driven: functions scan for these KEYs wherever they
appear — the header class (FullModel / dcat:Dataset / anything future) is
never matched against a whitelist, only reported verbatim. HEADER_TYPES is
the one exception: selecting whole header *objects* (tableviews) needs type
names.
"""

# Profile identity a header may declare, in priority order: old FullModel
# header messageType, new dcat:Dataset keyword, then the URI fields
# (Model.profile / conformsTo can repeat).
PROFILE_KEYS = ("Model.messageType", "keyword", "Model.profile", "conformsTo")

# Dependency references between model parts: old header, new header.
REFERENCE_KEYS = ("Model.DependentOn", "requires")

# Known header classes — only for selecting whole header objects.
HEADER_TYPES = ("FullModel", "Dataset")
