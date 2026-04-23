# SHACL Validators Usage Guide

This module provides vectorized pandas-based SHACL validators for RDF triplet data.

## Quick Start

```python
import pandas
import triplets
from triplets.shacl.validators import CONSTRAINT_VALIDATORS

# Load RDF data (assumes you have RDF files)
data = pandas.read_RDF(["model.xml"])

# Method 1: Use monkey-patched methods directly
violations = data.validate_min_count("ACLineSegment.r", min_count=1)
print(violations)

# Method 2: Use constraint mapping dictionary
validator = CONSTRAINT_VALIDATORS['sh:minCount']
violations = validator(data, "ACLineSegment.r", min_count=1)
print(violations)
```

## Available Validators

### Cardinality Constraints
- `validate_min_count(df, property_path, min_count)` - Minimum number of values
- `validate_max_count(df, property_path, max_count)` - Maximum number of values

### Value Constraints
- `validate_datatype(df, property_path, datatype)` - Data type validation (xsd:float, xsd:integer, xsd:boolean)
- `validate_min_length(df, property_path, min_length)` - Minimum string length
- `validate_max_length(df, property_path, max_length)` - Maximum string length
- `validate_pattern(df, property_path, regex)` - Regex pattern matching
- `validate_has_value(df, property_path, required_value)` - Required specific value
- `validate_in(df, property_path, allowed_values)` - Value in allowed set

### Numeric Constraints
- `validate_min_inclusive(df, property_path, min_value)` - Minimum numeric value (inclusive)
- `validate_max_inclusive(df, property_path, max_value)` - Maximum numeric value (inclusive)

### Reference Constraints
- `validate_class(df, property_path, target_class)` - Referenced object is of specified class
- `validate_node_kind(df, property_path, node_kind)` - Node type (sh:IRI, sh:BlankNode, sh:Literal)

### Comparative Constraints
- `validate_equals(df, property_a, property_b)` - Two properties have equal values
- `validate_disjoint(df, property_a, property_b)` - Two properties have no shared values
- `validate_less_than(df, property_a, property_b)` - First property less than second

### Logical Constraints
- `validate_and(df, property_path, constraints)` - All constraints must pass
- `validate_or(df, property_path, constraints)` - At least one constraint must pass
- `validate_not(df, property_path, constraint)` - Constraint must not pass

### Shape Constraints
- `validate_closed(df, allowed_properties, target_type=None)` - Only allowed properties present

## Usage Examples

### Cardinality Validation
```python
# Check that Terminal.ConductingEquipment has at least 1 value
violations = data.validate_min_count("Terminal.ConductingEquipment", min_count=1)

# Check that an object has at most 2 names
violations = data.validate_max_count("IdentifiedObject.name", max_count=2)
```

### Datatype Validation
```python
# Validate that resistance values are numeric
violations = data.validate_datatype("ACLineSegment.r", datatype="xsd:float")

# Validate integer values
violations = data.validate_datatype("count", datatype="xsd:integer")
```

### Pattern Validation
```python
# Validate that names start with uppercase letter
violations = data.validate_pattern("IdentifiedObject.name", regex=r"^[A-Z]")

# Validate email format
violations = data.validate_pattern("User.email", regex=r"^[\w\.-]+@[\w\.-]+\.\w+$")
```

### Numeric Range Validation
```python
# Validate that voltage is at least 110
violations = data.validate_min_inclusive("BaseVoltage.nominalVoltage", min_value=110)

# Validate that rating is at most 1000
violations = data.validate_max_inclusive("Rating.value", max_value=1000)
```

### String Length Validation
```python
# Validate minimum name length
violations = data.validate_min_length("IdentifiedObject.name", min_length=3)

# Validate maximum description length
violations = data.validate_max_length("IdentifiedObject.description", max_length=500)
```

### Value Set Validation
```python
# Validate that phase codes are in allowed set
violations = data.validate_in("Terminal.phases", allowed_values=["ABC", "A", "B", "C"])
```

### Reference/Class Validation
```python
# Validate that containers reference Substation objects
violations = data.validate_class("Equipment.container", target_class="Substation")
```

### Comparative Validation
```python
# Validate that minValue is less than maxValue
violations = data.validate_less_than("Curve.minValue", "Curve.maxValue")

# Validate that two properties are equal
violations = data.validate_equals("Equipment.name", "Equipment.aliasName")
```

### Closed Shape Validation
```python
# Validate that ACLineSegment only has allowed properties
allowed = ["Type", "IdentifiedObject.name", "ConductingEquipment.BaseVoltage",
           "ACLineSegment.r", "ACLineSegment.x", "ACLineSegment.bch"]
violations = data.validate_closed(allowed, target_type="ACLineSegment")
```

### Logical Constraints
```python
# Validate that ALL constraints pass (AND)
constraints = [
    ('sh:minCount', {'min_count': 1}),
    ('sh:datatype', {'datatype': 'xsd:float'}),
    ('sh:minInclusive', {'min_value': 0})
]
violations = data.validate_and("ACLineSegment.r", constraints)

# Validate that AT LEAST ONE constraint passes (OR)
constraints = [
    ('sh:hasValue', {'required_value': 'true'}),
    ('sh:hasValue', {'required_value': '1'})
]
violations = data.validate_or("Switch.normalOpen", constraints)
```

## Violations Format

All validators return a pandas DataFrame with the following columns:
- `ID` - The subject (RDF ID) that violated the constraint
- `KEY` - The property path that was validated
- `VALUE` - The value that caused the violation (or None for cardinality violations)
- `VIOLATION_TYPE` - The SHACL constraint type (e.g., 'sh:minCount')
- `MESSAGE` - Human-readable description of the violation

Example:
```
     ID                       KEY  VALUE VIOLATION_TYPE                                MESSAGE
0  obj1  Terminal.ConductingEquipment   None    sh:minCount  Property Terminal.ConductingEquipment has 0 values, minimum required is 1
```

## CONSTRAINT_VALIDATORS Dictionary

Use the mapping dictionary for programmatic access:

```python
from triplets.shacl.validators import CONSTRAINT_VALIDATORS

# Get validator by SHACL constraint type
validator = CONSTRAINT_VALIDATORS['sh:minCount']
violations = validator(data, "some.property", min_count=1)

# Available keys:
# 'sh:minCount', 'sh:maxCount', 'sh:datatype', 'sh:minLength', 'sh:maxLength',
# 'sh:pattern', 'sh:hasValue', 'sh:in', 'sh:minInclusive', 'sh:maxInclusive',
# 'sh:class', 'sh:nodeKind', 'sh:equals', 'sh:disjoint', 'sh:lessThan',
# 'sh:closed', 'sh:and', 'sh:or', 'sh:not'
```

## Notes

- All validators use vectorized pandas operations for performance
- Empty violation results return an empty DataFrame with the standard columns
- Validators are monkey-patched onto pandas.DataFrame for convenience
- No error handling is included in this minimal implementation
- Future versions may add shape definition loaders and batch validation runners
