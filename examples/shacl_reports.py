# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "triplets[polars,oxigraph,validation]",
# ]
#
# [tool.uv.sources]
# triplets = { path = "..", editable = true }
# ///
"""Official ENTSO-E SHACL validation with report exports — run with:

    uv run examples/shacl_reports.py

Validates the Svedala EQ instance (test_data/relicapgrid submodule) against the
official ENTSO-E CGMES Equipment SHACL (Simple = cardinality/datatype/valueType,
Complex = cross-object sh:sparql rules) using the performance modules: the
pyarrow-backed parser, the polars SHACL executor (engine="auto") and the
oxigraph SPARQL engine for the sh:sparql rules.

Pristine Svedala conforms, so three issues are introduced deliberately and the
validation must find exactly those. Violations stay in the console; the full
report is exported as a standard sh:ValidationReport (turtle) and as SARIF 2.1.0
carrying exact source locations (file + line of the violated element).

The ENTSO-E shapes are taken from $TRIPLETS_CGMES_SHACL (a local clone of
github.com/entsoe/application-profiles-library) or downloaded once next to
this script.
"""
import logging
import os
import urllib.request
from pathlib import Path

import pandas
import triplets
from triplets.export_schema import schemas

# The vectorized engines skip the ~60 valueType shapes whose sh:path is a
# sequence path (association -> rdf:type) and warn per shape — documented
# coverage caveat (use engine="pyshacl" for those); silenced here for readability.
logging.getLogger("triplets.validation").setLevel(logging.ERROR)

HERE = Path(__file__).resolve().parent
EQ = str(HERE.parent / "test_data/relicapgrid/Instance/Grid/IGM_Svedala/20220615T2230Z__Svedala_EQ_1.xml")

UPSTREAM_RAW = "https://raw.githubusercontent.com/entsoe/application-profiles-library/main/CGMES/SHACL"
SHACL_FILES = [
    "61970-600-2_Equipment-AP-Con-Simple-SHACL.ttl",   # cardinality, datatype, valueType
    "61970-301_Equipment-AP-Con-Complex-SHACL.ttl",    # cross-object sh:sparql rules
]


def entsoe_shapes():
    """Local application-profiles-library clone if configured, else download once."""
    shacl_dir = Path(os.environ.get("TRIPLETS_CGMES_SHACL", HERE / "entsoe_shacl"))
    shacl_dir.mkdir(exist_ok=True)
    for name in SHACL_FILES:
        if not (shacl_dir / name).exists():
            print(f"downloading {name} from entsoe/application-profiles-library@main")
            urllib.request.urlretrieve(f"{UPSTREAM_RAW}/{name}", shacl_dir / name)
    return [str(shacl_dir / name) for name in SHACL_FILES]


shapes = entsoe_shapes()

# >>> parse (pyarrow-backed compiled engine when available)
data = pandas.read_RDF([EQ])
print(f"parsed {len(data):,} triples from {Path(EQ).name}")

# >>> pristine data conforms: no Violations, only the lexical-form style
# >>> Warnings of the documented datatype deviation (integer-form floats)
pristine = data.shacl.validate(shapes, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
print(f"\npristine: {pristine['SEVERITY'].value_counts().to_dict()}")

# >>> introduce three issues deliberately
line = sorted(data.loc[(data["KEY"] == "Type") & (data["VALUE"] == "ACLineSegment"), "ID"])[0]
winding = sorted(data.loc[(data["KEY"] == "Type") & (data["VALUE"] == "PowerTransformerEnd"), "ID"])[0]
terminal = sorted(data.loc[(data["KEY"] == "Type") & (data["VALUE"] == "Terminal"), "ID"])[0]
substation = sorted(data.loc[(data["KEY"] == "Type") & (data["VALUE"] == "Substation"), "ID"])[0]

# 1: a line loses its name              -> Simple cardinality rule (vectorized)
broken = data[~((data["ID"] == line) & (data["KEY"] == "IdentifiedObject.name"))].copy()
# 2: a winding gets a negative ratedU   -> Complex valueRange sh:sparql rule
broken.loc[(broken["ID"] == winding) & (broken["KEY"] == "PowerTransformerEnd.ratedU"), "VALUE"] = "-400"
# 3: a terminal points at a Substation  -> Complex consistency sh:sparql rules
broken.loc[(broken["ID"] == terminal) & (broken["KEY"] == "Terminal.ConductingEquipment"), "VALUE"] = substation

# >>> validate again — engine="auto" is the polars executor, the Complex
# >>> sh:sparql rules are delegated to the SPARQL engine (oxigraph)
violations = broken.shacl.validate(shapes, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
found = violations[violations["SEVERITY"] == "Violation"]
print(f"\nafter breaking 3 things: {len(found)} Violations")
print(found[["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"]].to_string(index=False, max_colwidth=60))

# >>> enrich with context: object type/name, source file, shape + schema descriptions
enriched = violations.shacl.enrich(data=broken, shapes=shapes,
                                   rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
print("\nenriched Violations (what/where):")
print(enriched.loc[enriched["SEVERITY"] == "Violation",
                   ["ID", "OBJECT_TYPE", "OBJECT_NAME", "SHAPE_NAME"]].to_string(index=False))

# >>> export 1: standard sh:ValidationReport (turtle)
report_ttl = enriched.shacl.to_shacl_report(path=HERE / "shacl_reports.ttl")
print(f"\nwrote {report_ttl}")

# >>> export 2: SARIF 2.1.0 — grouped (one result per rule + occurrenceCount);
# >>> sources= locates the reported instances in the original XML, so results
# >>> carry file + startLine of the violated element
report_sarif = enriched.shacl.to_sarif(sources=[EQ], path=HERE / "shacl_reports.sarif")
print(f"wrote {report_sarif}")

# >>> peek at the SARIF locations of the introduced issues
import json
sarif = json.loads(Path(report_sarif).read_text())
for result in sarif["runs"][0]["results"]:
    if result["level"] != "error":
        continue
    location = result["locations"][0]["physicalLocation"]
    print(f"\n{result['ruleId']}  x{result.get('occurrenceCount', 1)}")
    print(f"  {Path(location['artifactLocation']['uri']).name}:{location['region']['startLine']}")
