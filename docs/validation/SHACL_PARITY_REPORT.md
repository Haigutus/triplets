# SHACL Engine Parity Cross-Check

**Data File:** `test_data/relicapgrid/Instance/BoundaryConfigurationExamples/TC-Boundary_data_split/BoundaryData.xml`

**Polars Parallel Violations:** 21
**pySHACL Violations:** 16

## Error Type: `sh:class`
- **Found by Polars:** 9
- **Found by pySHACL:** 5

### Example (Polars):
```json
{
  "ID":"fe850064-97a7-89ae-37e7-e67d935e7576",
  "KEY":"BoundaryPoint.ConnectivityNode",
  "VALUE":"a2ca87bb-9b25-455d-8a8c-a29933a84cef",
  "VIOLATION_TYPE":"sh:class",
  "ERROR_MESSAGE":"Referenced object has type Unknown but expected ConnectivityNode",
  "SEVERITY":"Violation",
  "RULE_NAME":"BoundaryPoint.ConnectivityNode-valueType",
  "SOURCE_SHAPE":"http:\/\/iec.ch\/TC57\/ns\/CIM\/CoreEquipment-EU\/Constraints#BoundaryPoint.ConnectivityNode-valueType",
  "DESCRIPTION":"This constraint validates the value type of the  association at the used direction.",
  "MESSAGE":"One of the following does not conform: 1) The value type shall be IRI; 2) The value type shall be an instance of the class: cim:ConnectivityNode."
}
```

### Example (pySHACL):
```json
{
  "ID":"urn:uuid:8754e9f2-5c69-4df6-9b3a-9029676cb571",
  "KEY":"http:\/\/iec.ch\/TC57\/CIM100#VoltageLevel.Substation",
  "VALUE":"afbe77a6-7d5e-4c6e-997f-376e59218a75",
  "VIOLATION_TYPE":"sh:class",
  "ERROR_MESSAGE":"One of the following does not conform: 1) The value type shall be IRI; 2) The value type shall be an instance of the class: cim:Substation.",
  "SEVERITY":"Violation",
  "RULE_NAME":"VoltageLevel.Substation-valueType",
  "SOURCE_SHAPE":"http:\/\/iec.ch\/TC57\/ns\/CIM\/CoreEquipment-EU\/Constraints#VoltageLevel.Substation-valueType"
}
```

### Parity Analysis
Mismatch in `class` usually happens when pySHACL resolves types through the graph while Polars depends on the explicit `Type` key in the triplet set.

## Error Type: `sh:datatype`
- **Found by Polars:** 12
- **Found by pySHACL:** 6

### Example (Polars):
```json
{
  "ID":"a2ca87bb-9b25-455d-8a8c-a29933a84cef",
  "KEY":"IdentifiedObject.description",
  "VALUE":null,
  "VIOLATION_TYPE":"sh:datatype",
  "ERROR_MESSAGE":"Value is not a valid xsd:string",
  "SEVERITY":"Violation",
  "RULE_NAME":"IdentifiedObject.description-datatype",
  "SOURCE_SHAPE":"http:\/\/iec.ch\/TC57\/ns\/CIM\/CoreEquipment-EU\/Constraints#IdentifiedObject.description-datatype",
  "DESCRIPTION":"This constraint validates the datatype of the description.",
  "MESSAGE":"The datatype is not literal or it violates the xsd datatype."
}
```

### Example (pySHACL):
```json
{
  "ID":"urn:uuid:636b98c2-ed10-4b29-af38-a6078d23f6f7",
  "KEY":"http:\/\/iec.ch\/TC57\/CIM100#IdentifiedObject.description",
  "VALUE":"nan",
  "VIOLATION_TYPE":"sh:datatype",
  "ERROR_MESSAGE":"The datatype is not literal or it violates the xsd datatype.",
  "SEVERITY":"Violation",
  "RULE_NAME":"IdentifiedObject.description-datatype",
  "SOURCE_SHAPE":"http:\/\/iec.ch\/TC57\/ns\/CIM\/CoreEquipment-EU\/Constraints#IdentifiedObject.description-datatype"
}
```

### Parity Analysis
Investigate difference: 6 violations.

## Error Type: `sh:nodeKind`
- **Found by Polars:** 0
- **Found by pySHACL:** 5

### Example (pySHACL):
```json
{
  "ID":"urn:uuid:8754e9f2-5c69-4df6-9b3a-9029676cb571",
  "KEY":"http:\/\/iec.ch\/TC57\/CIM100#VoltageLevel.Substation",
  "VALUE":"afbe77a6-7d5e-4c6e-997f-376e59218a75",
  "VIOLATION_TYPE":"sh:nodeKind",
  "ERROR_MESSAGE":"One of the following does not conform: 1) The value type shall be IRI; 2) The value type shall be an instance of the class: cim:Substation.",
  "SEVERITY":"Violation",
  "RULE_NAME":"VoltageLevel.Substation-valueType",
  "SOURCE_SHAPE":"http:\/\/iec.ch\/TC57\/ns\/CIM\/CoreEquipment-EU\/Constraints#VoltageLevel.Substation-valueType"
}
```

### Parity Analysis
Mismatch in `nodeKind` is expected because pySHACL operates on the full RDF graph, while Polars operates on simplified triplets where IRIs and Literals are distinguished by their presence in the ID column.
