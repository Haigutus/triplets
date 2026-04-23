# SHACL Validator Quick Start Guide

## Installation

The SHACL validators are already integrated into the triplets library. Just import and use:

```python
import pandas
import triplets  # This enables SHACL validators on DataFrames
```

## Basic Usage

### 1. Load Your RDF Data

```python
import pandas
import triplets

# Load CIM/CGMES RDF data
data = pandas.read_RDF(["path/to/your/file.xml"])

# Or load multiple files
data = pandas.read_RDF([
    "EQ_file.xml",
    "TP_file.xml",
    "SSH_file.xml"
])
```

### 2. Apply Validators

Once data is loaded, validators are available as DataFrame methods:

#### Check Required Properties (Cardinality)
```python
# Check that all entities have required mRID
violations = data.validate_min_count("IdentifiedObject.mRID", min_count=1)

if len(violations) > 0:
    print(f"Found {len(violations)} missing mRIDs")
    print(violations)
```

#### Check Single-Valued Properties
```python
# Ensure mRID appears at most once per entity
violations = data.validate_max_count("IdentifiedObject.mRID", max_count=1)

if len(violations) > 0:
    print(f"Found {len(violations)} duplicate mRIDs")
```

#### Validate Datatypes
```python
# Check that nominalVoltage is a float
violations = data.validate_datatype(
    "BaseVoltage.nominalVoltage",
    datatype="xsd:float"
)
```

#### Validate Numeric Ranges
```python
# Check that voltage is non-negative
violations = data.validate_min_inclusive(
    "BaseVoltage.nominalVoltage",
    min_value=0
)

# Check maximum voltage
violations = data.validate_max_inclusive(
    "BaseVoltage.nominalVoltage",
    max_value=1000000  # 1 MV
)
```

#### Validate String Patterns
```python
# Check UUID format for mRID
uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
violations = data.validate_pattern("IdentifiedObject.mRID", regex=uuid_pattern)

# Check minimum string length
violations = data.validate_min_length("IdentifiedObject.name", min_length=1)

# Check maximum string length
violations = data.validate_max_length("IdentifiedObject.description", max_length=500)
```

#### Validate Class References
```python
# Check that Region references point to GeographicalRegion entities
violations = data.validate_class(
    "SubGeographicalRegion.Region",
    target_class="GeographicalRegion"
)
```

### 3. Working with Violation Results

All validators return a DataFrame with violation details:

```python
violations = data.validate_min_count("IdentifiedObject.name", min_count=1)

# Violations DataFrame has these columns:
# - ID: Entity ID with violation
# - KEY: Property that was checked
# - VALUE: Current value (or None if missing)
# - MESSAGE: Description of the violation

# Check if any violations
if len(violations) > 0:
    print(f"Found {len(violations)} violations")

    # Show first few violations
    print(violations.head())

    # Get list of entity IDs with violations
    entity_ids = violations['ID'].unique()
    print(f"Affected entities: {entity_ids}")

    # Export violations to CSV
    violations.to_csv("violations_report.csv", index=False)
```

## Automatic SHACL Validation

Use the provided test script for automatic validation based on SHACL shapes:

```bash
python test_shacl_parse_and_validate.py
```

This will:
1. Parse SHACL constraint files from ENTSOE profiles
2. Extract all constraint rules automatically
3. Apply validators to test data
4. Generate comprehensive violation reports

## Available Validators

| Validator | Purpose | Parameters |
|-----------|---------|------------|
| `validate_min_count` | Required properties | `property`, `min_count` |
| `validate_max_count` | Single-valued properties | `property`, `max_count` |
| `validate_datatype` | Type checking | `property`, `datatype` |
| `validate_min_inclusive` | Minimum value | `property`, `min_value` |
| `validate_max_inclusive` | Maximum value | `property`, `max_value` |
| `validate_min_exclusive` | Minimum value (exclusive) | `property`, `min_value` |
| `validate_max_exclusive` | Maximum value (exclusive) | `property`, `max_value` |
| `validate_min_length` | Minimum string length | `property`, `min_length` |
| `validate_max_length` | Maximum string length | `property`, `max_length` |
| `validate_pattern` | Regex pattern | `property`, `regex` |
| `validate_class` | Object references | `property`, `target_class` |
| `validate_in` | Enumeration | `property`, `values` |
| `validate_node_kind` | Node type | `property`, `node_kind` |

## Complete Example

```python
import pandas
import triplets

# Load data
data = pandas.read_RDF(["equipment.xml"])

# Run multiple validations
all_violations = []

# 1. Check required properties
all_violations.extend(data.validate_min_count("IdentifiedObject.mRID", min_count=1))
all_violations.extend(data.validate_min_count("IdentifiedObject.name", min_count=1))

# 2. Check datatypes
all_violations.extend(data.validate_datatype("BaseVoltage.nominalVoltage", datatype="xsd:float"))

# 3. Check ranges
all_violations.extend(data.validate_min_inclusive("BaseVoltage.nominalVoltage", min_value=0))

# 4. Check references
all_violations.extend(data.validate_class(
    "SubGeographicalRegion.Region",
    target_class="GeographicalRegion"
))

# Combine and report
if all_violations:
    violations_df = pandas.concat(all_violations, ignore_index=True)
    print(f"\nTotal violations: {len(violations_df)}")
    print(f"\nViolations by type:")
    print(violations_df.groupby('MESSAGE').size())

    # Save report
    violations_df.to_csv("full_validation_report.csv", index=False)
else:
    print("✓ No violations found - data is fully compliant!")
```

## Performance Tips

### For Large Datasets
```python
# Load data
data = pandas.read_RDF(["large_file.xml"])

# Focus validation on critical constraints first
critical_violations = []

# Quick cardinality checks
critical_violations.extend(data.validate_min_count("IdentifiedObject.mRID", min_count=1))

# Then run comprehensive validation if needed
if not critical_violations:
    # Run all validators...
    pass
```

### Batch Processing
```python
import glob

# Process multiple files
for file in glob.glob("*.xml"):
    print(f"Validating {file}...")
    data = pandas.read_RDF([file])

    violations = data.validate_min_count("IdentifiedObject.mRID", min_count=1)

    if len(violations) > 0:
        print(f"  ✗ {len(violations)} violations")
        violations.to_csv(f"{file}_violations.csv")
    else:
        print(f"  ✓ Valid")
```

## Common CIM/CGMES Validations

### Equipment Profile (EQ)
```python
# Required identifiers
data.validate_min_count("IdentifiedObject.mRID", min_count=1)
data.validate_max_count("IdentifiedObject.mRID", max_count=1)

# Base voltage
data.validate_datatype("BaseVoltage.nominalVoltage", datatype="xsd:float")
data.validate_min_inclusive("BaseVoltage.nominalVoltage", min_value=0)

# Equipment references
data.validate_class("ConductingEquipment.BaseVoltage", target_class="BaseVoltage")
data.validate_class("Equipment.EquipmentContainer", target_class="EquipmentContainer")
```

### Topology Profile (TP)
```python
# Connectivity nodes
data.validate_min_count("ConnectivityNode.ConnectivityNodeContainer", min_count=1)
data.validate_class(
    "ConnectivityNode.ConnectivityNodeContainer",
    target_class="ConnectivityNodeContainer"
)

# Terminals
data.validate_min_count("Terminal.ConductingEquipment", min_count=1)
data.validate_class("Terminal.ConductingEquipment", target_class="ConductingEquipment")
```

### Steady State Hypothesis (SSH)
```python
# Equipment states
data.validate_datatype("Switch.open", datatype="xsd:boolean")
data.validate_datatype("RotatingMachine.p", datatype="xsd:float")
data.validate_datatype("RotatingMachine.q", datatype="xsd:float")
```

## Getting Help

- See `triplets/shacl/USAGE.md` for detailed validator documentation
- See `SHACL_TESTING_SUMMARY.md` for test results and performance data
- Run test scripts to see validators in action:
  - `python test_shacl_with_real_data.py`
  - `python test_shacl_parse_and_validate.py`
