"""
Run all ReliCapGrid test use cases through import / validate / export cycle.

For each use case:
1. Import instance files with pandas.read_RDF()
2. Export to Excel (one xlsx per file, typed sheets)
3. SHACL validate against matching profile rules
4. Re-export to RDF/XML
5. Generate SHACL validation report (RDF/XML)
"""
import pathlib
import time
import shutil
import pandas
import triplets
from triplets.rdf_parser import ExportType
from triplets.validation import validate_shacl
from triplets.validation.rdflib_shacl_parser import parse_shacl
from triplets.validation.shacl_report import write_shacl_report

base_path = pathlib.Path(__file__).parent.absolute()
instance_path = base_path / "test_data/relicapgrid/Instance"
profiles_path = base_path / "test_data/entsoe-profiles"
results_path = base_path / "test_results"

# Export schemas
NC_SCHEMA = base_path / "triplets/export_schema/ENTSOE_NC_552_ED2.json"
CGMES_SCHEMA = base_path / "triplets/export_schema/ENTSOE_CGMES_3.0.0_552_ED2.json"

# Load SHACL mapping
import triplets.shacl.collect_available_rules as car

version_to_base = {}
for _, row in car.rdfs_data.query("KEY == 'versionIRI'").iterrows():
    version_to_base[row["VALUE"]] = row["ID"]

profile_to_shacl = {}
resolved = car.mapping[car.mapping["shacl_file"] == "both"]
for _, row in resolved.iterrows():
    uri = row["profile_uri"]
    if uri not in profile_to_shacl:
        profile_to_shacl[uri] = []
    profile_to_shacl[uri].append(profiles_path / row["shacl_path"])

rules_cache = {}

# Use case definitions
USE_CASES = {
    "PF_UC1": {
        "title": "Power Flow (solved case)",
        "description": "Import and export a solved CGMES power flow case (EQ+SSH+SV+TP).",
        "files": [
            "Belgovia/Grid/cimxml/20220615T2230Z__Belgovia_EQ_1.xml",
            "Belgovia/Grid/cimxml/20220615T2230Z_2D_Belgovia_SSH_1.xml",
            "Belgovia/Grid/cimxml/20220615T2230Z_2D_Belgovia_SV_1.xml",
            "Belgovia/Grid/cimxml/20220615T2230Z_2D_Belgovia_TP_1.xml",
        ],
        "schema": "cgmes",
        "doc": None,
    },
    "BDS_UC1": {
        "title": "Boundary Data Set",
        "description": "Import and export boundary data for inter-TSO connections.",
        "files": [
            "boundaryData/Grid/cimxml/Boundary_Border-Svedala-Belgovia.xml",
            "boundaryData/Grid/cimxml/Boundary_Border-Svedala-Espheim.xml",
            "boundaryData/Grid/cimxml/Boundary_Border-Galia-Belgovia.xml",
        ],
        "schema": "cgmes",
        "doc": None,
    },
    "CD_UC1": {
        "title": "Common Data",
        "description": "Import and export common reference data shared across TSOs.",
        "files": [
            "commonData/NetworkCode/cimxml/Org-NineRealms_CD.xml",
        ],
        "schema": "nc",
        "doc": None,
    },
    "RD_UC1": {
        "title": "Reference Data",
        "description": "Import and export reference data (object registries, schemes).",
        "files": [
            "commonData/NetworkCode/cimxml/Org-NineRealms_CD_OR.xml",
            "referenceData/NetworkCode/cimxml/ActivityScheme-NCP_RD.xml",
            "referenceData/NetworkCode/cimxml/Test-PartyScheme-NCP_RD.xml",
        ],
        "schema": "nc",
        "doc": None,
    },
    "CO_UC1": {
        "title": "Importing, executing, updating and exporting contingencies",
        "description": "Validates handling of contingency data including import, execution, update, and export of contingencies against a CGM/IGM network model.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_CO.xml",
            "Espheim/NetworkCode/cimxml/Espheim_CO.xml",
            "Galia/NetworkCode/cimxml/Galia_CO.xml",
            "Svedala/NetworkCode/cimxml/Svedala_CO.xml",
            "Svedala/NetworkCode/cimxml/Svedala-Belgovia_CO.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Contingencies_use_cases/Contingencies_use_cases.md",
    },
    "ER_UC1": {
        "title": "Equipment Reliability (single TSO)",
        "description": "Import and validate equipment reliability data for a single TSO including BiddingZones, SchedulingAreas, and OperationalLimits.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_ER.xml",
            "Espheim/NetworkCode/cimxml/Espheim_ER.xml",
            "Galia/NetworkCode/cimxml/Galia_ER.xml",
            "Svedala/NetworkCode/cimxml/Svedala_ER.xml",
        ],
        "schema": "nc",
        "doc": None,
    },
    "ER_UC2": {
        "title": "Equipment Reliability (common data)",
        "description": "Import and validate the shared common ER dataset covering all Nine Realms.",
        "files": [
            "commonData/NetworkCode/cimxml/Org-NineRealms_CD.xml",
        ],
        "schema": "nc",
        "doc": None,
    },
    "RA_UC1": {
        "title": "Topology RA with one topology action",
        "description": "Basic topology remedial action with a single switch opening operation, no dependencies.",
        "files": [
            "Svedala/NetworkCode/cimxml/Svedala_RA.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Remedial_Action_use_cases/Remedial_Action_use_cases.md",
    },
    "RA_UC2": {
        "title": "Dependent topology RAs with multiple actions",
        "description": "Two topology remedial actions with exclusive dependencies affecting the same substation switches.",
        "files": [
            "Svedala/NetworkCode/cimxml/Svedala_RA.xml",
            "Svedala/NetworkCode/cimxml/Svedala_RAS_Jotunheim_ordered.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Remedial_Action_use_cases/Remedial_Action_use_cases.md",
    },
    "RA_UC3": {
        "title": "Modification prior to RAO (SIS/SSI)",
        "description": "TSO modifies topology action availability before RAO using SIS/SSI schedules.",
        "files": [
            "Espheim/NetworkCode/cimxml/Espheim_RA.xml",
            "Espheim/NetworkCode/cimxml/Espheim_SIS.xml",
            "Espheim/NetworkCode/cimxml/Espheim_SSI.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Remedial_Action_use_cases/Remedial_Action_use_cases.md",
    },
    "RA_UC4": {
        "title": "Proposing modifications after RAO",
        "description": "TSO refuses an existing RAS and proposes a counter-proposal with new schedule.",
        "files": [
            "Svedala/NetworkCode/cimxml/Svedala_RAS_acceptance_proposal.xml",
            "Svedala/NetworkCode/cimxml/Svedala_RAS_Jotunheim_proposed.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Remedial_Action_use_cases/Remedial_Action_use_cases.md",
    },
    "RA_UC5": {
        "title": "Activation after RAO",
        "description": "RCC communicates activation of topology remedial action after RAO via EventSchedule in RAS.",
        "files": [
            "Svedala/NetworkCode/cimxml/Svedala_RA.xml",
            "Svedala/NetworkCode/cimxml/Svedala_RAS.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Remedial_Action_use_cases/Remedial_Action_use_cases.md",
    },
    "RA_UC6": {
        "title": "Countertrade Remedial Action",
        "description": "Countertrade RA defined via PowerSchedule (Belgovia_PS) or PowerBidSchedule (Belgovia_SIS).",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_RA.xml",
            "Belgovia/NetworkCode/cimxml/Belgovia_PS.xml",
            "Belgovia/NetworkCode/cimxml/Belgovia_SIS.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Remedial_Action_use_cases/Remedial_Action_use_cases.md",
    },
    "RA_UC7": {
        "title": "Redispatch Remedial Action",
        "description": "Redispatch RA using bid schedules or power shift key strategy.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_RA.xml",
            "Belgovia/NetworkCode/cimxml/Belgovia_SIS.xml",
            "Belgovia/NetworkCode/cimxml/Belgovia_ER.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Remedial_Action_use_cases/Remedial_Action_use_cases.md",
    },
    "RAS_UC8": {
        "title": "RAS FAP acceptance",
        "description": "Remedial action schedule FAP acceptance and simple RAS exchange.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_RAS_FAP.xml",
            "Svedala/NetworkCode/cimxml/Svedala_FAP_RAS_simple.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Remedial_Action_use_cases/Remedial_Action_use_cases.md",
    },
    "RA_UC9": {
        "title": "Shared RA on Boundary",
        "description": "Cross-border contingency and remedial action coordination.",
        "files": [
            "Svedala/NetworkCode/cimxml/Svedala-Belgovia_CO.xml",
            "Belgovia/NetworkCode/cimxml/Belgovia_RAS.xml",
        ],
        "schema": "nc",
        "doc": None,
    },
    "AS_UC1": {
        "title": "Availability Schedule",
        "description": "Cancel, shorten, or reschedule an availability schedule using AvailabilityRemedialAction.",
        "files": [
            "Svedala/NetworkCode/cimxml/Svedala_AS.xml",
            "Svedala/NetworkCode/cimxml/Svedala_RA.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Availability _Schedule_use_case/Availability _Schedule_use_case.md",
    },
    "AE_UC1": {
        "title": "Secured Assessed Element",
        "description": "Define a secured assessed element evaluated in the base case.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_AE.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Assessed_Element_use_cases/Assessed_Element_use_cases.md",
    },
    "AE_UC2": {
        "title": "Scanned Assessed Element",
        "description": "Define a scanned assessed element secured in another region.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_AE.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Assessed_Element_use_cases/Assessed_Element_use_cases.md",
    },
    "AE_UC3": {
        "title": "Disable AE via SIS",
        "description": "Temporarily disable an assessed element using State Instruction Schedule.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_AE.xml",
            "Belgovia/NetworkCode/cimxml/Belgovia_SIS.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Assessed_Element_use_cases/Assessed_Element_use_cases.md",
    },
    "AE_UC4": {
        "title": "Exclude an Assessed Element",
        "description": "Exclude an assessed element from RAO optimization while keeping it in security analysis.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_AE.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Assessed_Element_use_cases/Assessed_Element_use_cases.md",
    },
    "AE_UC5": {
        "title": "AE with Contingency",
        "description": "Model combinations of assessed elements and contingencies with inclusion/exclusion logic.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_AE.xml",
            "Belgovia/NetworkCode/cimxml/Belgovia_CO.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Assessed_Element_use_cases/Assessed_Element_use_cases.md",
    },
    "AE_UC6": {
        "title": "AE with Remedial Action",
        "description": "Model assessed elements with remedial action associations.",
        "files": [
            "Belgovia/NetworkCode/cimxml/Belgovia_AE.xml",
            "Belgovia/NetworkCode/cimxml/Belgovia_RA.xml",
        ],
        "schema": "nc",
        "doc": "TestUseCases/Assessed_Element_use_cases/Assessed_Element_use_cases.md",
    },
    "OR_UC1": {
        "title": "Object Registry",
        "description": "Import and export object registry data.",
        "files": [
            "Espheim/NetworkCode/cimxml/Espheim_OR.xml",
        ],
        "schema": "nc",
        "doc": None,
    },
    "SAR_UC1": {
        "title": "Security Analysis Result",
        "description": "Import and export security analysis results.",
        "files": [
            "Jotunheim/GridSituation/cimxml/2D_SAR_1030Z_Jotunheim.xml",
        ],
        "schema": "nc",
        "doc": None,
    },
}

# Known issues per use case
KNOWN_ISSUES = {
    "CO_UC1": ["PR#38 entsoe/application-profiles-library — contingentStatus allowed values fix"],
    "ER_UC1": ["#244 entsoe/relicapgrid — Espheim_ER missing properties", "#247 — BiddingZoneBorder inverse refs"],
    "RA_UC1": ["#248 entsoe/relicapgrid — RA inverse reference violations"],
    "RA_UC3": ["#245 entsoe/relicapgrid — Espheim_RA missing isActivationRestricted"],
    "RA_UC6": ["#246 entsoe/relicapgrid — Belgovia_SIS missing properties"],
    "RA_UC7": ["#246 entsoe/relicapgrid — Belgovia_SIS missing properties"],
    "RAS_UC8": ["#252 entsoe/relicapgrid — RemedialActionScheduleResponseKInd typo"],
    "AS_UC1": ["#249 entsoe/relicapgrid — Svedala_AS inverse cardinality"],
    "AE_UC3": ["#246 entsoe/relicapgrid — Belgovia_SIS missing properties"],
}


# Edit operations per use case
# Each returns (modified_data, edit_description) or None if no edit applies to this file

def edit_CO_UC1(data, filename):
    """Update contingency name to demonstrate edit capability."""
    co_id = "7e31c67d-67ba-4592-8ac1-9e806d697c8e"
    mask = (data["ID"] == co_id) & (data["KEY"] == "IdentifiedObject.name")
    if mask.any():
        old = data.loc[mask, "VALUE"].values[0]
        data.loc[mask, "VALUE"] = old + " (updated)"
        return data, f"Renamed contingency {co_id}: '{old}' -> '{old} (updated)'"
    return None

def edit_RA_UC1(data, filename):
    """Verify topology action exists and toggle enabled state."""
    ga_id = "176d262c-701c-4ced-99b2-a155c136e787"
    mask = (data["ID"] == ga_id) & (data["KEY"] == "GridStateAlteration.enabled")
    if mask.any():
        old = data.loc[mask, "VALUE"].values[0]
        new = "false" if old == "true" else "true"
        data.loc[mask, "VALUE"] = new
        return data, f"Toggled GridStateAlteration {ga_id} enabled: {old} -> {new}"
    return None

def edit_RA_UC3(data, filename):
    """Disable a topology action via SSI (set enabled=false)."""
    if "SSI" not in filename:
        return None
    ga_mask = (data["KEY"] == "GridStateAlteration.enabled")
    if ga_mask.any():
        idx = data.loc[ga_mask].index[0]
        old = data.loc[idx, "VALUE"]
        data.loc[idx, "VALUE"] = "false"
        return data, f"Disabled GridStateAlteration: enabled {old} -> false"
    return None

def edit_RA_UC6(data, filename):
    """Update PowerSchedule value to demonstrate countertrade edit."""
    if "PS" not in filename:
        return None
    mask = (data["KEY"] == "RegularTimePoint.value1")
    if mask.any():
        idx = data.loc[mask].index[0]
        old = data.loc[idx, "VALUE"]
        data.loc[idx, "VALUE"] = str(float(old) + 10.0) if old else "10.0"
        return data, f"Adjusted PowerSchedule value1: {old} -> {data.loc[idx, 'VALUE']}"
    return None

def edit_AE_UC3(data, filename):
    """Disable an assessed element by setting enabled=false via SIS timepoint."""
    if "SIS" not in filename:
        return None
    ae_tp_id = "a26e3ae0-0a7d-4f42-ad64-e9105ec3cd41"
    mask = (data["ID"] == ae_tp_id) & (data["KEY"] == "AssessedElementTimePoint.enabled")
    if mask.any():
        old = data.loc[mask, "VALUE"].values[0]
        data.loc[mask, "VALUE"] = "false"
        return data, f"Disabled AE timepoint {ae_tp_id}: enabled {old} -> false"
    return None

def edit_AE_UC4(data, filename):
    """Exclude an assessed element by setting inBaseCase=false."""
    ae_id = "1eb2eb03-dda6-4e59-b7c8-a2edb117d676"
    mask = (data["ID"] == ae_id) & (data["KEY"] == "AssessedElement.inBaseCase")
    if mask.any():
        data.loc[mask, "VALUE"] = "false"
        return data, f"Excluded AE {ae_id}: inBaseCase -> false"
    return None

UC_EDITS = {
    "CO_UC1": edit_CO_UC1,
    "RA_UC1": edit_RA_UC1,
    "RA_UC3": edit_RA_UC3,
    "RA_UC6": edit_RA_UC6,
    "AE_UC3": edit_AE_UC3,
    "AE_UC4": edit_AE_UC4,
}


def resolve_profile(data):
    """Resolve conformsTo URIs to a profile URI with SHACL rules."""
    conforms_to = data.query("KEY == 'conformsTo'")
    if conforms_to.empty:
        return None
    version_lookup = pandas.DataFrame(list(version_to_base.items()), columns=["VALUE", "profile_uri"])
    matched = conforms_to[["VALUE"]].merge(version_lookup, on="VALUE", how="inner")
    if matched.empty:
        return None
    return matched["profile_uri"].values[0]


def get_rules(profile_uri):
    """Get SHACL rules for a profile, with caching."""
    if profile_uri not in rules_cache:
        shacl_files = profile_to_shacl.get(profile_uri, [])
        if shacl_files:
            rules_cache[profile_uri] = parse_shacl([str(f) for f in shacl_files])
        else:
            rules_cache[profile_uri] = []
    return rules_cache[profile_uri]


def process_file(xml_file, out_dir, rdf_map, uc_code=None):
    """Process a single instance file: import, excel, validate, edit, export."""
    result = {
        "file": xml_file.name,
        "import": "FAIL",
        "excel": "SKIP",
        "validate": "SKIP",
        "violations": 0,
        "edit": "SKIP",
        "export": "SKIP",
        "time_s": 0,
    }

    start = time.time()

    # Import
    try:
        data = pandas.read_RDF([str(xml_file)])
        result["import"] = "OK"
    except Exception as e:
        result["import"] = f"FAIL: {e}"
        result["time_s"] = round(time.time() - start, 3)
        return result

    # Excel export (per-type sheets)
    try:
        excel_path = out_dir / f"{xml_file.stem}.xlsx"
        types = data.types_dict()
        with pandas.ExcelWriter(str(excel_path)) as writer:
            for class_type in types:
                sheet_name = (class_type or "Unknown")[:31]
                class_data = data.type_tableview(class_type)
                class_data.to_excel(writer, sheet_name=sheet_name)
        result["excel"] = "OK"
    except Exception as e:
        result["excel"] = f"FAIL: {e}"

    # SHACL validate
    profile_uri = resolve_profile(data)
    if profile_uri and profile_uri in profile_to_shacl:
        rules = get_rules(profile_uri)
        if rules:
            try:
                violations = validate_shacl(data, rules, engine="pandas", check_external=False)
                result["violations"] = len(violations)
                result["validate"] = "OK" if len(violations) == 0 else f"{len(violations)} violations"

                shacl_file_names = [str(f.relative_to(profiles_path)) for f in profile_to_shacl[profile_uri]]
                write_shacl_report(violations, str(out_dir / f"{xml_file.stem}_report.rdf"),
                                   format="xml", validated_file=xml_file.name, shacl_files=shacl_file_names)
            except Exception as e:
                result["validate"] = f"FAIL: {e}"
    else:
        result["validate"] = "NO RULES"

    # Edit step
    edit_fn = UC_EDITS.get(uc_code)
    if edit_fn:
        try:
            edit_result = edit_fn(data, xml_file.name)
            if edit_result:
                data, edit_desc = edit_result
                result["edit"] = "OK"
                print(f"    EDIT: {edit_desc}")
            else:
                result["edit"] = "N/A"
        except Exception as e:
            result["edit"] = f"FAIL: {e}"

    # RDF/XML re-export (to memory, then save with _export suffix)
    try:
        exported = data.export_to_cimxml(
            rdf_map=rdf_map,
            export_type=ExportType.XML_PER_INSTANCE,
            export_to_memory=True,
            export_undefined=True,
        )
        for buf in exported:
            export_name = pathlib.Path(buf.name).stem + "_export.xml"
            (out_dir / export_name).write_bytes(buf.getvalue())
        result["export"] = "OK"
    except Exception as e:
        result["export"] = f"FAIL: {e}"

    result["time_s"] = round(time.time() - start, 3)
    return result


# Main execution
if __name__ == "__main__":
    # Clean and create results dir
    if results_path.exists():
        shutil.rmtree(results_path)
    results_path.mkdir()

    # Export schema paths (passed as strings to export_to_cimxml)
    nc_rdf_map = str(NC_SCHEMA)
    cgmes_rdf_map = str(CGMES_SCHEMA)

    all_results = []

    for uc_code, uc_def in USE_CASES.items():
        print(f"\n{'=' * 80}")
        print(f"{uc_code}: {uc_def['title']}")
        print(f"{'=' * 80}")

        out_dir = results_path / uc_code
        out_dir.mkdir()

        # Write description
        desc_lines = [f"# {uc_code}: {uc_def['title']}\n\n{uc_def['description']}\n"]
        if uc_def.get("doc"):
            doc_path = instance_path.parent / uc_def["doc"]
            if doc_path.exists():
                desc_lines.append(f"\n## Source documentation\n\nSee [{doc_path.name}]({doc_path.relative_to(base_path)})\n")
        if uc_code in KNOWN_ISSUES:
            desc_lines.append("\n## Known issues\n")
            for issue in KNOWN_ISSUES[uc_code]:
                desc_lines.append(f"- {issue}\n")

        (out_dir / "description.md").write_text("".join(desc_lines))

        rdf_map = cgmes_rdf_map if uc_def.get("schema") == "cgmes" else nc_rdf_map

        for rel_path in uc_def["files"]:
            xml_file = instance_path / rel_path
            if not xml_file.exists():
                print(f"  MISSING: {rel_path}")
                all_results.append({"uc": uc_code, "file": rel_path, "import": "MISSING",
                                    "excel": "SKIP", "validate": "SKIP", "violations": 0, "export": "SKIP", "time_s": 0})
                continue

            result = process_file(xml_file, out_dir, rdf_map, uc_code=uc_code)
            result["uc"] = uc_code
            all_results.append(result)

            status = "PASS" if result["violations"] == 0 and "FAIL" not in str(result["import"]) else "ISSUES"
            print(f"  {result['file']:50s} import={result['import']:4s} excel={result['excel']:4s} "
                  f"validate={str(result['validate']):20s} edit={result['edit']:4s} export={result['export']:4s} [{result['time_s']}s]")

    # Write summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")

    results_df = pandas.DataFrame(all_results)

    summary_lines = ["# Test Use Case Results\n\n"]
    summary_lines.append(f"Generated: {pandas.Timestamp.now().isoformat()}\n\n")
    summary_lines.append("| UC | File | Import | Excel | Validate | Edit | Export |\n")
    summary_lines.append("|---|---|---|---|---|---|---|\n")

    for _, row in results_df.iterrows():
        summary_lines.append(f"| {row['uc']} | {row['file']} | {row['import']} | {row['excel']} | {row['validate']} | {row['edit']} | {row['export']} |\n")

    total_files = len(results_df)
    total_violations = results_df["violations"].sum()
    clean = len(results_df[results_df["violations"] == 0])
    summary_lines.append(f"\n**Total: {total_files} files, {clean} clean, {total_violations} violations**\n")

    (results_path / "summary.md").write_text("".join(summary_lines))
    print(f"\nResults written to {results_path}/")
    print(f"Total: {total_files} files, {clean} clean, {total_violations} violations")
