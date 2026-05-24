"""
Create DLR (Dynamic Line Rating) use case data.

Extends the Belgovia_SHS with a CurrentLimitSchedule for the BO-Line_2 PATL,
with 15-minute DLR values over 24 hours simulating weather-dependent ratings.

Base: Belgovia_SHS.xml (existing CurrentLimitSchedule for BO-TR2_1)
Target: BO-Line_2 PATL (b59a4b04-016f-ec37-917a-3297a36f61f8, baseline 1574 A)
AE reference: AE2 (d463cbba-c89c-4199-bbb9-1a33d90cae2c) already references this limit
"""
import pandas
import triplets
import uuid
import math
from pathlib import Path

base_path = Path(__file__).parent.absolute()
output_dir = base_path / "test_results" / "DLR_UC1"
output_dir.mkdir(parents=True, exist_ok=True)

# Load existing SHS as basis — keep all existing content, append DLR schedule
shs = pandas.read_RDF([str(base_path / "test_data/relicapgrid/Instance/Belgovia/NetworkCode/cimxml/Belgovia_SHS.xml")])
instance_id = shs["INSTANCE_ID"].iloc[0]

print(f"Loaded SHS: {len(shs)} rows, types: {shs.types_dict()}")

# Reference IDs
PATL_CURRENT_LIMIT_ID = "b59a4b04-016f-ec37-917a-3297a36f61f8"  # BO-Line_2 PATL (1574 A)
BASELINE_RATING = 1574.0  # Amps

# Generate DLR schedule IDs
SCHEDULE_ID = str(uuid.uuid4())
SCHEDULE_MRID = SCHEDULE_ID

# Generate 96 time points (24h x 4 per hour = 15 min intervals)
# Baseline: 1574 A, DLR range: 1200-1900 A
time_points = []
for i in range(96):
    hour = i / 4.0  # 0.0 to 23.75
    tp_id = str(uuid.uuid4())

    wind_uplift = 0.15 * (0.5 + 0.5 * math.sin((hour - 6) * math.pi / 12))  # 0-15% from wind
    cooling_uplift = 0.10 * (0.5 + 0.5 * math.cos((hour - 3) * math.pi / 12))  # 0-10% from cool ambient

    dlr_rating = BASELINE_RATING * (1.0 + wind_uplift + cooling_uplift)
    dlr_rating = round(dlr_rating)

    timestamp = f"2023-07-22T{int(hour):02d}:{int((hour % 1) * 60):02d}:00Z"

    time_points.append({
        "id": tp_id,
        "timestamp": timestamp,
        "value": dlr_rating,
    })

# Build new triplet rows for the schedule
new_rows = []

def add_row(obj_id, key, value):
    new_rows.append({
        "ID": obj_id,
        "KEY": key,
        "VALUE": str(value),
        "INSTANCE_ID": instance_id,
    })

# CurrentLimitSchedule
add_row(SCHEDULE_ID, "Type", "CurrentLimitSchedule")
add_row(SCHEDULE_ID, "IdentifiedObject.name", "DLR_UC1: DLR Schedule for BO-Line_2")
add_row(SCHEDULE_ID, "IdentifiedObject.mRID", SCHEDULE_MRID)
add_row(SCHEDULE_ID, "IdentifiedObject.description", "DLR_UC1: Dynamic Line Rating schedule for Belgovia-Galia tie line BO-Line_2, 15-min intervals")
add_row(SCHEDULE_ID, "CurrentLimitSchedule.CurrentLimit", PATL_CURRENT_LIMIT_ID)
add_row(SCHEDULE_ID, "BaseTimeSeries.interpolationKind", "TimeSeriesInterpolationKind.linear")

# CurrentLimitTimePoints
for tp in time_points:
    add_row(tp["id"], "Type", "CurrentLimitTimePoint")
    add_row(tp["id"], "CurrentLimitTimePoint.CurrentLimitSchedule", SCHEDULE_ID)
    add_row(tp["id"], "CurrentLimitTimePoint.atTime", tp["timestamp"])
    add_row(tp["id"], "CurrentLimitTimePoint.value", round(tp["value"]))

# Append to existing SHS data
new_df = pandas.DataFrame(new_rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
# Convert to same dtypes
for col in new_df.columns:
    new_df[col] = new_df[col].astype(shs[col].dtype)

extended_shs = pandas.concat([shs, new_df], ignore_index=True)

print(f"Original SHS: {len(shs)} rows")
print(f"Added: {len(new_rows)} rows ({len(time_points)} time points)")
print(f"Extended SHS: {len(extended_shs)} rows")
print(f"Types: {extended_shs.types_dict()}")
print()

# Export to Excel
excel_path = output_dir / "Belgovia_SHS_DLR.xlsx"
types = extended_shs.types_dict()
with pandas.ExcelWriter(str(excel_path)) as writer:
    for class_type in types:
        sheet_name = (class_type or "Unknown")[:31]
        class_data = extended_shs.type_tableview(class_type)
        class_data.to_excel(writer, sheet_name=sheet_name)
print(f"Excel: {excel_path}")

# Export to RDF/XML
exported = extended_shs.export_to_cimxml(
    rdf_map=str(base_path / "triplets/export_schema/ENTSOE_NC_552_ED1.json"),
    export_type="xml_per_instance",
    export_to_memory=True,
    export_undefined=True,
)
for buf in exported:
    xml_path = output_dir / "Belgovia_SHS_DLR.xml"
    xml_path.write_bytes(buf.getvalue())
    print(f"RDF/XML: {xml_path}")

# Validate
from triplets.validation import validate_shacl
from triplets.validation.rdflib_shacl_parser import parse_shacl
from triplets.validation.shacl_report import write_shacl_report
import triplets.shacl.collect_available_rules as car

version_to_base = dict(zip(
    car.rdfs_data.query("KEY == 'versionIRI'")["VALUE"],
    car.rdfs_data.query("KEY == 'versionIRI'")["ID"]
))
profile_to_shacl = {}
resolved = car.mapping[car.mapping["shacl_file"] == "both"]
for _, row in resolved.iterrows():
    uri = row["profile_uri"]
    if uri not in profile_to_shacl: profile_to_shacl[uri] = []
    profile_to_shacl[uri].append(base_path / "test_data/entsoe-profiles" / row["shacl_path"])

ct = extended_shs.query("KEY == 'conformsTo'")
vl = pandas.DataFrame(list(version_to_base.items()), columns=["VALUE", "profile_uri"])
matched = ct[["VALUE"]].merge(vl, on="VALUE", how="inner")
if not matched.empty:
    profile_uri = matched["profile_uri"].values[0]
    shacl_files = profile_to_shacl.get(profile_uri, [])
    if shacl_files:
        rules = parse_shacl([str(f) for f in shacl_files])
        violations = validate_shacl(extended_shs, rules, engine="pandas", check_external=False)
        print(f"\nValidation: {len(violations)} violations")
        if len(violations) > 0:
            print(violations[["VIOLATION_TYPE", "KEY", "VALUE"]].to_string(index=False))
        report_path = output_dir / "Belgovia_SHS_DLR_report.rdf"
        write_shacl_report(violations, str(report_path), format="xml",
                           validated_file="Belgovia_SHS_DLR.xml",
                           shacl_files=[str(f.name) for f in shacl_files])
        print(f"Report: {report_path}")

# Write description
desc = f"""# DLR_UC1: Dynamic Line Rating for BO-Line_2

## Description
This use case demonstrates Dynamic Line Rating (DLR) by creating time-varying
PATL values for the Belgovia-Galia cross-border tie line BO-Line_2.

## Data chain
- **ACLineSegment**: BO-Line_2 (`b58bf21a-096a-4dae-9a01-3f03b60c24c7`) — from EQ
- **Terminal** → **OperationalLimitSet** (`9f6e19b4-4360-a6b0-2b73-35ed991e48a7`) — from EQ
- **CurrentLimit PATL**: 1574 A baseline (`{PATL_CURRENT_LIMIT_ID}`) — from EQ
- **CurrentLimitSchedule**: DLR values (`{SCHEDULE_ID}`) — created in SHS
- **AssessedElement AE2**: (`d463cbba-c89c-4199-bbb9-1a33d90cae2c`) references OperationalLimit — from AE

## DLR Schedule
- 96 time points (24h x 15-min intervals)
- Date: 2023-07-22
- Rating range: 1574-1967 A (always >= baseline PATL of 1574 A)
- Interpolation: linear

## Profiles involved
- **EQ**: ACLineSegment, Terminal, OperationalLimitSet, CurrentLimit definition
- **ER**: OperationalLimitType (PATL/TATL duration definitions)
- **SHS**: CurrentLimitSchedule with DLR time points
- **AE**: AssessedElement referencing the CurrentLimit for capacity calculation

## Test flow
1. Import Belgovia EQ (grid model with BO-Line_2)
2. Import Belgovia AE (assessed element referencing the PATL)
3. Import Belgovia SHS with DLR schedule (this file)
4. For each 15-min interval, the PATL is overridden by the DLR value
5. Run N-1 security analysis / capacity calculation with dynamic ratings
6. Export updated SHS with DLR values

## Sample DLR values
| Time | Rating (A) | vs Baseline |
|---|---|---|
"""

for tp in time_points[::4]:  # every hour
    pct = ((tp["value"] / BASELINE_RATING) - 1) * 100
    sign = "+" if pct >= 0 else ""
    desc += f"| {tp['timestamp']} | {tp['value']} | {sign}{pct:.1f}% |\n"

(output_dir / "description.md").write_text(desc)
print(f"\nDescription: {output_dir / 'description.md'}")
print(f"\nDLR use case created in {output_dir}")
