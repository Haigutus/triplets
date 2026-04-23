import pandas
import re
import logging

logger = logging.getLogger(__name__)

def _min_count(df, property_path, min_count, target_class=None, **kwargs):
    """ minimum cardinality constraint."""
    if target_class:
        target_ids = df.query("KEY == 'Type' and VALUE == @target_class")["ID"].unique()
    else:
        target_ids = df["ID"].unique()
    
    property_data = df.query("KEY == @property_path")
    counts = property_data.groupby("ID").size().reindex(target_ids, fill_value=0).reset_index(name='count')
    violations = counts[counts['count'] < min_count]
    
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"], "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:minCount",
        "ERROR_MESSAGE": violations.apply(lambda row: f"Property {property_path} has {row['count']} values, minimum required is {min_count}", axis=1)
    })

def _max_count(df, property_path, max_count, target_class=None, **kwargs):
    """ maximum cardinality constraint."""
    if target_class:
        target_ids = df.query("KEY == 'Type' and VALUE == @target_class")["ID"].unique()
        property_data = df[df["ID"].isin(target_ids) & (df["KEY"] == property_path)]
    else:
        property_data = df.query("KEY == @property_path")
        
    counts = property_data.groupby("ID").size().reset_index(name='count')
    violations = counts[counts['count'] > max_count]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"], "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:maxCount",
        "ERROR_MESSAGE": violations.apply(lambda row: f"Property {property_path} has {row['count']} values, maximum allowed is {max_count}", axis=1)
    })

def _datatype(df, property_path, datatype, **kwargs):
    """ datatype constraint."""
    property_data = df.query("KEY == @property_path").copy()
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    # pySHACL flags null/NA values as datatype violations if a specific type is expected
    invalid_null = property_data[property_data['VALUE'].isna()]
    
    violations = pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    # Process non-null values
    to_check = property_data[~property_data['VALUE'].isna()].copy()
    
    invalid_type = pandas.DataFrame()
    if datatype in ["xsd:float", "xsd:double", "xsd:decimal"]:
        to_check['numeric_value'] = pandas.to_numeric(to_check['VALUE'], errors='coerce')
        invalid_type = to_check[to_check['numeric_value'].isna()]
    elif datatype in ["xsd:integer", "xsd:int", "xsd:long"]:
        to_check['numeric_value'] = pandas.to_numeric(to_check['VALUE'], errors='coerce')
        # Check if numeric and is integer
        invalid_type = to_check[to_check['numeric_value'].isna() | (to_check['numeric_value'] % 1 != 0)]
    elif datatype == "xsd:boolean":
        invalid_type = to_check[~to_check['VALUE'].astype(str).str.lower().isin(['true', 'false', '1', '0'])]
    
    all_invalid = pandas.concat([invalid_null, invalid_type])
    if not all_invalid.empty:
        violations = pandas.DataFrame({
            "ID": all_invalid["ID"].values, "KEY": all_invalid["KEY"].values, "VALUE": all_invalid["VALUE"].values, "VIOLATION_TYPE": "sh:datatype",
            "ERROR_MESSAGE": [f"Value '{v}' is not a valid {datatype}" for v in all_invalid["VALUE"].values]
        })
    return violations

def _min_length(df, property_path, min_length, **kwargs):
    """ minimum string length constraint."""
    property_data = df.query("KEY == @property_path").copy()
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    property_data['length'] = property_data['VALUE'].astype(str).str.len()
    violations = property_data[property_data['length'] < min_length]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": violations["KEY"].values, "VALUE": violations["VALUE"].values, "VIOLATION_TYPE": "sh:minLength",
        "ERROR_MESSAGE": [f"Value '{v}' has length {len(str(v))}, minimum required is {min_length}" for v in violations["VALUE"].values]
    })

def _max_length(df, property_path, max_length, **kwargs):
    """ maximum string length constraint."""
    property_data = df.query("KEY == @property_path").copy()
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    property_data['length'] = property_data['VALUE'].astype(str).str.len()
    violations = property_data[property_data['length'] > max_length]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": violations["KEY"].values, "VALUE": violations["VALUE"].values, "VIOLATION_TYPE": "sh:maxLength",
        "ERROR_MESSAGE": [f"Value '{v}' has length {len(str(v))}, maximum allowed is {max_length}" for v in violations["VALUE"].values]
    })

def _pattern(df, property_path, regex, **kwargs):
    """ regex pattern constraint."""
    property_data = df.query("KEY == @property_path").copy()
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    property_data['matches'] = property_data['VALUE'].astype(str).str.match(regex)
    violations = property_data[~property_data['matches']]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": violations["KEY"].values, "VALUE": violations["VALUE"].values, "VIOLATION_TYPE": "sh:pattern",
        "ERROR_MESSAGE": [f"Value '{v}' does not match pattern '{regex}'" for v in violations["VALUE"].values]
    })

def _has_value(df, property_path, required_value, **kwargs):
    """ that property has a specific required value."""
    all_ids = df["ID"].unique()
    property_data = df.query("KEY == @property_path and VALUE == @required_value")
    ids_with_value = property_data["ID"].unique()
    missing_ids = [id for id in all_ids if id not in ids_with_value]
    if not missing_ids: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": missing_ids, "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:hasValue",
        "ERROR_MESSAGE": [f"Property {property_path} does not have required value '{required_value}'" for _ in missing_ids]
    })

def _in(df, property_path, allowed_values, **kwargs):
    """ that values are in allowed set."""
    property_data = df.query("KEY == @property_path")
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    violations = property_data[~property_data['VALUE'].isin(allowed_values)]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": violations["KEY"].values, "VALUE": violations["VALUE"].values, "VIOLATION_TYPE": "sh:in",
        "ERROR_MESSAGE": [f"Value '{v}' is not in allowed set {allowed_values}" for v in violations["VALUE"].values]
    })

def _min_inclusive(df, property_path, min_value, **kwargs):
    """ minimum inclusive numeric constraint."""
    property_data = df.query("KEY == @property_path").copy()
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    property_data['numeric_value'] = pandas.to_numeric(property_data['VALUE'], errors='coerce')
    violations = property_data[property_data['numeric_value'] < min_value]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": violations["KEY"].values, "VALUE": violations["VALUE"].values, "VIOLATION_TYPE": "sh:minInclusive",
        "ERROR_MESSAGE": [f"Value {v} is less than minimum {min_value}" for v in violations["VALUE"].values]
    })

def _max_inclusive(df, property_path, max_value, **kwargs):
    """ maximum inclusive numeric constraint."""
    property_data = df.query("KEY == @property_path").copy()
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    property_data['numeric_value'] = pandas.to_numeric(property_data['VALUE'], errors='coerce')
    violations = property_data[property_data['numeric_value'] > max_value]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": violations["KEY"].values, "VALUE": violations["VALUE"].values, "VIOLATION_TYPE": "sh:maxInclusive",
        "ERROR_MESSAGE": [f"Value {v} is greater than maximum {max_value}" for v in violations["VALUE"].values]
    })

def _class(df, property_path, target_class, check_external=True, **kwargs):
    """ that referenced objects are of specified class."""
    property_data = df.query("KEY == @property_path")
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    if not check_external:
        # Get all objects present in the current dataframe
        present_ids = df["ID"].unique()
        # Check for external references (objects not in this file/dataframe)
        external_refs = property_data[~property_data['VALUE'].isin(present_ids)]
        if not external_refs.empty:
            if logger.isEnabledFor(logging.DEBUG):
                for _, row in external_refs.iterrows():
                    logger.debug(f"Skipping class validation for external reference: {row['VALUE']} (Property: {property_path})")
            # Filter out external references
            property_data = property_data[property_data['VALUE'].isin(present_ids)]
            if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

    type_data = df.query("KEY == 'Type' and VALUE == @target_class")
    valid_ids = type_data["ID"].unique()
    violations = property_data[~property_data['VALUE'].isin(valid_ids)]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": violations["KEY"].values, "VALUE": violations["VALUE"].values, "VIOLATION_TYPE": "sh:class",
        "ERROR_MESSAGE": [f"Referenced object '{v}' is not of class {target_class}" for v in violations["VALUE"].values]
    })

def _node_kind(df, property_path, node_kind, **kwargs):
    """ node kind (IRI, BlankNode, Literal)."""
    property_data = df.query("KEY == @property_path")
    if property_data.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    all_ids = set(df["ID"].unique())
    invalid = pandas.DataFrame()
    
    if node_kind == "sh:IRI":
        # In simplified triplets, nulls are NOT IRIs. 
        # Alphanumeric strings are treated as IRIs (UUIDs or refs)
        invalid = property_data[property_data['VALUE'].isna()]
    elif node_kind == "sh:BlankNode":
        is_blank = property_data['VALUE'].astype(str).str.startswith('_:')
        invalid = property_data[~is_blank]
    elif node_kind == "sh:Literal":
        # In triplet DataFrames all values are strings (literals) - skip this check
        pass
        
    if not invalid.empty:
        return pandas.DataFrame({
            "ID": invalid["ID"].values, "KEY": invalid["KEY"].values, "VALUE": invalid["VALUE"].values, "VIOLATION_TYPE": "sh:nodeKind",
            "ERROR_MESSAGE": [f"Value '{v}' is not of node kind {node_kind}" for v in invalid["VALUE"].values]
        })
    return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _equals(df, property_a, property_b, **kwargs):
    """ that two properties have equal values."""
    data_a = df.query("KEY == @property_a")[["ID", "VALUE"]].rename(columns={"VALUE": "value_a"})
    data_b = df.query("KEY == @property_b")[["ID", "VALUE"]].rename(columns={"VALUE": "value_b"})
    merged = pandas.merge(data_a, data_b, on="ID", how='outer')
    violations = merged[(merged['value_a'] != merged['value_b']) | merged['value_a'].isna() | merged['value_b'].isna()]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": property_a, "VALUE": violations["value_a"].values, "VIOLATION_TYPE": "sh:equals",
        "ERROR_MESSAGE": [f"Property {property_a} ('{a}') does not equal {property_b} ('{b}')" for a, b in zip(violations["value_a"].values, violations["value_b"].values)]
    })

def _disjoint(df, property_a, property_b, **kwargs):
    """ that two properties have no shared values."""
    data_a = df.query("KEY == @property_a")[["ID", "VALUE"]].rename(columns={"VALUE": "value_a"})
    data_b = df.query("KEY == @property_b")[["ID", "VALUE"]].rename(columns={"VALUE": "value_b"})
    merged = pandas.merge(data_a, data_b, on="ID")
    violations = merged[merged['value_a'] == merged['value_b']]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": property_a, "VALUE": violations["value_a"].values, "VIOLATION_TYPE": "sh:disjoint",
        "ERROR_MESSAGE": [f"Properties {property_a} and {property_b} have shared value '{v}'" for v in violations["value_a"].values]
    })

def _less_than(df, property_a, property_b, **kwargs):
    """ that property_a values are less than property_b values."""
    data_a = df.query("KEY == @property_a")[["ID", "VALUE"]].rename(columns={"VALUE": "value_a"})
    data_b = df.query("KEY == @property_b")[["ID", "VALUE"]].rename(columns={"VALUE": "value_b"})
    merged = pandas.merge(data_a, data_b, on="ID")
    merged['numeric_a'] = pandas.to_numeric(merged['value_a'], errors='coerce')
    merged['numeric_b'] = pandas.to_numeric(merged['value_b'], errors='coerce')
    violations = merged[merged['numeric_a'] >= merged['numeric_b']]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": property_a, "VALUE": violations["value_a"].values, "VIOLATION_TYPE": "sh:lessThan",
        "ERROR_MESSAGE": [f"Property {property_a} value {a} is not less than {property_b} value {b}" for a, b in zip(violations["value_a"].values, violations["value_b"].values)]
    })

def _inverse_min_count(df, property_path, min_count, target_class=None, full_df=None, **kwargs):
    """Inverse path minimum cardinality: count how many entities reference each target via property_path."""
    source_df = full_df if full_df is not None else df
    if target_class:
        target_ids = df.query("KEY == 'Type' and VALUE == @target_class")["ID"].unique()
    else:
        target_ids = df["ID"].unique()

    # Find rows in full dataset where KEY matches the inverse property and VALUE is a target ID
    refs = source_df[source_df["KEY"] == property_path]
    counts = refs.groupby("VALUE").size().reindex(target_ids, fill_value=0).reset_index(name='count')
    counts.columns = ["ID", "count"]
    violations = counts[counts['count'] < min_count]

    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"], "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:minCount(inverse)",
        "ERROR_MESSAGE": violations.apply(lambda row: f"Entity is referenced by {row['count']} objects via {property_path}, minimum required is {min_count}", axis=1)
    })

def _inverse_max_count(df, property_path, max_count, target_class=None, full_df=None, **kwargs):
    """Inverse path maximum cardinality: count how many entities reference each target via property_path."""
    source_df = full_df if full_df is not None else df
    if target_class:
        target_ids = df.query("KEY == 'Type' and VALUE == @target_class")["ID"].unique()
    else:
        target_ids = df["ID"].unique()

    refs = source_df[source_df["KEY"] == property_path]
    counts = refs.groupby("VALUE").size().reindex(target_ids, fill_value=0).reset_index(name='count')
    counts.columns = ["ID", "count"]
    violations = counts[counts['count'] > max_count]

    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"], "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:maxCount(inverse)",
        "ERROR_MESSAGE": violations.apply(lambda row: f"Entity is referenced by {row['count']} objects via {property_path}, maximum allowed is {max_count}", axis=1)
    })

def _closed(df, allowed_properties, target_type=None, **kwargs):
    """ that only allowed properties are used."""
    if target_type:
        type_data = df.query("KEY == 'Type' and VALUE == @target_type")
        target_ids = type_data["ID"].unique()
        data_to_check = df[df["ID"].isin(target_ids)]
    else: data_to_check = df
    violations = data_to_check[~data_to_check['KEY'].isin(allowed_properties)]
    if violations.empty: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": violations["ID"].values, "KEY": violations["KEY"].values, "VALUE": violations["VALUE"].values, "VIOLATION_TYPE": "sh:closed",
        "ERROR_MESSAGE": [f"Property '{k}' is not in allowed properties list" for k in violations["KEY"].values]
    })

def _and(df, property_path, constraints, **kwargs):
    """ that all constraints are satisfied (logical AND)."""
    all_violations = []
    for constraint in constraints:
        # Recursively call validate for this nested constraint
        violations = validate(df, [constraint], **kwargs)
        if not violations.empty:
            all_violations.append(violations)
    if not all_violations: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.concat(all_violations, ignore_index=True)

def _or(df, property_path, constraints, **kwargs):
    """ that at least one constraint is satisfied (logical OR)."""
    # A list of lists of failed IDs for each alternative
    failed_ids_per_alt = []
    # A list of DataFrames containing all individual violations from each alternative
    all_nested_violations = []
    
    all_ids = df["ID"].unique()
    
    for constraint in constraints:
        # Recursively call validate for this alternative
        violations = validate(df, [constraint], **kwargs)
        failed_ids = set(violations["ID"].unique())
        failed_ids_per_alt.append(failed_ids)
        all_nested_violations.append(violations)
        
    if not failed_ids_per_alt: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    # IDs that failed ALL alternatives are the actual violators
    failing_ids = set(all_ids)
    for failed in failed_ids_per_alt:
        failing_ids = failing_ids.intersection(failed)
        
    if not failing_ids: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    # We return the detailed violations from all alternatives for the objects that failed everything
    violation_reports = []
    for violations in all_nested_violations:
        report = violations[violations["ID"].isin(failing_ids)].copy()
        # Add a hint that this is part of an OR failure
        if not report.empty:
            report['VIOLATION_TYPE'] = 'sh:or' # We can keep original or mark as OR
            violation_reports.append(report)
            
    if not violation_reports: 
        # Fallback to generic OR violation if nested reports are empty (shouldn't happen)
        return pandas.DataFrame({
            "ID": list(failing_ids), "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:or",
            "ERROR_MESSAGE": [f"None of the OR constraints satisfied for property {property_path}" for _ in failing_ids]
        })
        
    return pandas.concat(violation_reports, ignore_index=True)

def _not(df, property_path, constraint, **kwargs):
    """ that constraint is NOT satisfied (logical NOT)."""
    # Recursively call validate
    violations = validate(df, [constraint], **kwargs)
    
    all_ids = df["ID"].unique()
    violating_ids = set(violations["ID"].unique())
    # NOT logic: IDs that PASSED the nested constraint are now VIOLATORS
    passed_ids = set(all_ids) - violating_ids
    
    if not passed_ids: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.DataFrame({
        "ID": list(passed_ids), "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:not",
        "ERROR_MESSAGE": [f"Constraint should not be satisfied for property {property_path}" for _ in passed_ids]
    })

CONSTRAINT_VALIDATORS = {
    'sh:minCount': _min_count, 'sh:maxCount': _max_count, 'sh:datatype': _datatype, 'sh:minLength': _min_length,
    'sh:maxLength': _max_length, 'sh:pattern': _pattern, 'sh:hasValue': _has_value, 'sh:in': _in,
    'sh:minInclusive': _min_inclusive, 'sh:maxInclusive': _max_inclusive, 'sh:class': _class, 'sh:nodeKind': _node_kind,
    'sh:equals': _equals, 'sh:disjoint': _disjoint, 'sh:lessThan': _less_than, 'sh:closed': _closed,
    'sh:and': _and, 'sh:or': _or, 'sh:not': _not,
}

INTERNAL_TO_SHACL = {
    'min_count': 'sh:minCount', 'max_count': 'sh:maxCount', 'datatype': 'sh:datatype', 'min_length': 'sh:minLength',
    'max_length': 'sh:maxLength', 'pattern': 'sh:pattern', 'min_inclusive': 'sh:minInclusive', 'max_inclusive': 'sh:maxInclusive',
    'target_class': 'sh:class', 'node_kind': 'sh:nodeKind', 'has_value': 'sh:hasValue', 'allowed_values': 'sh:in',
    'equals': 'sh:equals', 'disjoint': 'sh:disjoint', 'less_than': 'sh:lessThan', 'closed': 'sh:closed',
    'and': 'sh:and', 'or': 'sh:or', 'not': 'sh:not'
}

def validate(df, rules, check_external=True, **kwargs):
    """ Apply pandas validators to a list of SHACL constraint dictionaries."""
    all_violations = []
    for constraint in rules:
        target_class = constraint.get('class')
        is_inverse = constraint.get('inverse_path', False)
        df_to_check = df
        if target_class:
            target_ids = df.query("KEY == 'Type' and VALUE == @target_class")["ID"].unique()
            df_to_check = df[df["ID"].isin(target_ids)]
            if df_to_check.empty: continue

        prop = constraint.get('property')

        # Handle inverse path constraints
        if is_inverse:
            for key in ('min_count', 'max_count'):
                value = constraint.get(key)
                if value is None: continue
                validator = _inverse_min_count if key == 'min_count' else _inverse_max_count
                violations = validator(df_to_check, prop, value, target_class=target_class, full_df=df)
                if not violations.empty:
                    if 'severity' in constraint: violations['SEVERITY'] = constraint['severity']
                    if 'rule_name' in constraint: violations['RULE_NAME'] = constraint['rule_name']
                    if 'id' in constraint: violations['SOURCE_SHAPE'] = constraint['id']
                    if 'description' in constraint: violations['DESCRIPTION'] = constraint['description']
                    if 'message' in constraint: violations['MESSAGE'] = constraint['message']
                    all_violations.append(violations)
            continue

        for key, value in constraint.items():
            shacl_term = INTERNAL_TO_SHACL.get(key)
            if shacl_term and shacl_term in CONSTRAINT_VALIDATORS:
                validator = CONSTRAINT_VALIDATORS[shacl_term]

                if shacl_term == 'sh:class':
                    violations = validator(df_to_check, prop, value, check_external=check_external)
                else:
                    violations = validator(df_to_check, prop, value, target_class=target_class, check_external=check_external)

                if not violations.empty:
                    if 'severity' in constraint: violations['SEVERITY'] = constraint['severity']
                    if 'rule_name' in constraint: violations['RULE_NAME'] = constraint['rule_name']
                    if 'id' in constraint: violations['SOURCE_SHAPE'] = constraint['id']
                    if 'description' in constraint: violations['DESCRIPTION'] = constraint['description']
                    if 'message' in constraint: violations['MESSAGE'] = constraint['message']
                    all_violations.append(violations)
    if not all_violations: return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pandas.concat(all_violations, ignore_index=True)

# Monkeypatching for backward compatibility with _ prefix
import pandas as pd
pd.DataFrame._min_count = _min_count
pd.DataFrame._max_count = _max_count
pd.DataFrame._datatype = _datatype
pd.DataFrame._min_length = _min_length
pd.DataFrame._max_length = _max_length
pd.DataFrame._pattern = _pattern
pd.DataFrame._has_value = _has_value
pd.DataFrame._in = _in
pd.DataFrame._min_inclusive = _min_inclusive
pd.DataFrame._max_inclusive = _max_inclusive
pd.DataFrame._class = _class
pd.DataFrame._node_kind = _node_kind
pd.DataFrame._equals = _equals
pd.DataFrame._disjoint = _disjoint
pd.DataFrame._less_than = _less_than
pd.DataFrame._closed = _closed
pd.DataFrame._and = _and
pd.DataFrame._or = _or
pd.DataFrame._not = _not
