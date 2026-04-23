import pandas
import re


def _min_count(df, property_path, min_count):
    """ minimum cardinality constraint."""
    property_data = df.query("KEY == @property_path")
    counts = property_data.groupby("ID").size().reset_index(name='count')
    violations = counts[counts['count'] < min_count]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"],
        "KEY": property_path,
        "VALUE": None,
        "VIOLATION_TYPE": "sh:minCount",
        "MESSAGE": violations.apply(lambda row: f"Property {property_path} has {row['count']} values, minimum required is {min_count}", axis=1)
    })
    return result


def _max_count(df, property_path, max_count):
    """ maximum cardinality constraint."""
    property_data = df.query("KEY == @property_path")
    counts = property_data.groupby("ID").size().reset_index(name='count')
    violations = counts[counts['count'] > max_count]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"],
        "KEY": property_path,
        "VALUE": None,
        "VIOLATION_TYPE": "sh:maxCount",
        "MESSAGE": violations.apply(lambda row: f"Property {property_path} has {row['count']} values, maximum allowed is {max_count}", axis=1)
    })
    return result


def _datatype(df, property_path, datatype):
    """ datatype constraint."""
    property_data = df.query("KEY == @property_path").copy()

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    violations = pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    if datatype in ["xsd:float", "xsd:double", "xsd:decimal"]:
        property_data['numeric_value'] = pandas.to_numeric(property_data['VALUE'], errors='coerce')
        invalid = property_data[property_data['numeric_value'].isna()]
        if not invalid.empty:
            violations = pandas.DataFrame({
                "ID": invalid["ID"].values,
                "KEY": invalid["KEY"].values,
                "VALUE": invalid["VALUE"].values,
                "VIOLATION_TYPE": "sh:datatype",
                "MESSAGE": [f"Value '{v}' is not a valid {datatype}" for v in invalid["VALUE"].values]
            })
    elif datatype in ["xsd:integer", "xsd:int", "xsd:long"]:
        property_data['numeric_value'] = pandas.to_numeric(property_data['VALUE'], errors='coerce')
        invalid = property_data[property_data['numeric_value'].isna() | (property_data['numeric_value'] != property_data['numeric_value'].astype(int, errors='ignore'))]
        if not invalid.empty:
            violations = pandas.DataFrame({
                "ID": invalid["ID"].values,
                "KEY": invalid["KEY"].values,
                "VALUE": invalid["VALUE"].values,
                "VIOLATION_TYPE": "sh:datatype",
                "MESSAGE": [f"Value '{v}' is not a valid {datatype}" for v in invalid["VALUE"].values]
            })
    elif datatype == "xsd:boolean":
        invalid = property_data[~property_data['VALUE'].isin(['true', 'false', '1', '0'])]
        if not invalid.empty:
            violations = pandas.DataFrame({
                "ID": invalid["ID"].values,
                "KEY": invalid["KEY"].values,
                "VALUE": invalid["VALUE"].values,
                "VIOLATION_TYPE": "sh:datatype",
                "MESSAGE": [f"Value '{v}' is not a valid {datatype}" for v in invalid["VALUE"].values]
            })

    return violations


def _min_length(df, property_path, min_length):
    """ minimum string length constraint."""
    property_data = df.query("KEY == @property_path").copy()

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    property_data['length'] = property_data['VALUE'].astype(str).str.len()
    violations = property_data[property_data['length'] < min_length]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": violations["KEY"].values,
        "VALUE": violations["VALUE"].values,
        "VIOLATION_TYPE": "sh:minLength",
        "MESSAGE": [f"Value '{v}' has length {len(str(v))}, minimum required is {min_length}" for v in violations["VALUE"].values]
    })
    return result


def _max_length(df, property_path, max_length):
    """ maximum string length constraint."""
    property_data = df.query("KEY == @property_path").copy()

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    property_data['length'] = property_data['VALUE'].astype(str).str.len()
    violations = property_data[property_data['length'] > max_length]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": violations["KEY"].values,
        "VALUE": violations["VALUE"].values,
        "VIOLATION_TYPE": "sh:maxLength",
        "MESSAGE": [f"Value '{v}' has length {len(str(v))}, maximum allowed is {max_length}" for v in violations["VALUE"].values]
    })
    return result


def _pattern(df, property_path, regex):
    """ regex pattern constraint."""
    property_data = df.query("KEY == @property_path").copy()

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    property_data['matches'] = property_data['VALUE'].astype(str).str.match(regex)
    violations = property_data[~property_data['matches']]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": violations["KEY"].values,
        "VALUE": violations["VALUE"].values,
        "VIOLATION_TYPE": "sh:pattern",
        "MESSAGE": [f"Value '{v}' does not match pattern '{regex}'" for v in violations["VALUE"].values]
    })
    return result


def _has_value(df, property_path, required_value):
    """ that property has a specific required value."""
    all_ids = df["ID"].unique()
    property_data = df.query("KEY == @property_path and VALUE == @required_value")
    ids_with_value = property_data["ID"].unique()
    missing_ids = [id for id in all_ids if id not in ids_with_value]

    if not missing_ids:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": missing_ids,
        "KEY": property_path,
        "VALUE": None,
        "VIOLATION_TYPE": "sh:hasValue",
        "MESSAGE": [f"Property {property_path} does not have required value '{required_value}'" for _ in missing_ids]
    })
    return result


def _in(df, property_path, allowed_values):
    """ that values are in allowed set."""
    property_data = df.query("KEY == @property_path")

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    violations = property_data[~property_data['VALUE'].isin(allowed_values)]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": violations["KEY"].values,
        "VALUE": violations["VALUE"].values,
        "VIOLATION_TYPE": "sh:in",
        "MESSAGE": [f"Value '{v}' is not in allowed set {allowed_values}" for v in violations["VALUE"].values]
    })
    return result


def _min_inclusive(df, property_path, min_value):
    """ minimum inclusive numeric constraint."""
    property_data = df.query("KEY == @property_path").copy()

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    property_data['numeric_value'] = pandas.to_numeric(property_data['VALUE'], errors='coerce')
    violations = property_data[property_data['numeric_value'] < min_value]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": violations["KEY"].values,
        "VALUE": violations["VALUE"].values,
        "VIOLATION_TYPE": "sh:minInclusive",
        "MESSAGE": [f"Value {v} is less than minimum {min_value}" for v in violations["VALUE"].values]
    })
    return result


def _max_inclusive(df, property_path, max_value):
    """ maximum inclusive numeric constraint."""
    property_data = df.query("KEY == @property_path").copy()

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    property_data['numeric_value'] = pandas.to_numeric(property_data['VALUE'], errors='coerce')
    violations = property_data[property_data['numeric_value'] > max_value]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": violations["KEY"].values,
        "VALUE": violations["VALUE"].values,
        "VIOLATION_TYPE": "sh:maxInclusive",
        "MESSAGE": [f"Value {v} is greater than maximum {max_value}" for v in violations["VALUE"].values]
    })
    return result


def _class(df, property_path, target_class):
    """ that referenced objects are of specified class."""
    property_data = df.query("KEY == @property_path")

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    type_data = df.query("KEY == 'Type' and VALUE == @target_class")
    valid_ids = type_data["ID"].unique()
    violations = property_data[~property_data['VALUE'].isin(valid_ids)]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": violations["KEY"].values,
        "VALUE": violations["VALUE"].values,
        "VIOLATION_TYPE": "sh:class",
        "MESSAGE": [f"Referenced object '{v}' is not of class {target_class}" for v in violations["VALUE"].values]
    })
    return result


def _node_kind(df, property_path, node_kind):
    """ node kind (IRI, BlankNode, Literal)."""
    property_data = df.query("KEY == @property_path")

    if property_data.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    violations = pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    if node_kind == "sh:IRI":
        is_iri = property_data['VALUE'].astype(str).str.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:|^#')
        invalid = property_data[~is_iri]
    elif node_kind == "sh:BlankNode":
        is_blank = property_data['VALUE'].astype(str).str.startswith('_:')
        invalid = property_data[~is_blank]
    elif node_kind == "sh:Literal":
        all_ids = df["ID"].unique()
        is_literal = ~property_data['VALUE'].isin(all_ids)
        invalid = property_data[~is_literal]
    else:
        return violations

    if not invalid.empty:
        violations = pandas.DataFrame({
            "ID": invalid["ID"].values,
            "KEY": invalid["KEY"].values,
            "VALUE": invalid["VALUE"].values,
            "VIOLATION_TYPE": "sh:nodeKind",
            "MESSAGE": [f"Value '{v}' is not of node kind {node_kind}" for v in invalid["VALUE"].values]
        })

    return violations


def _equals(df, property_a, property_b):
    """ that two properties have equal values."""
    data_a = df.query("KEY == @property_a")[["ID", "VALUE"]].rename(columns={"VALUE": "value_a"})
    data_b = df.query("KEY == @property_b")[["ID", "VALUE"]].rename(columns={"VALUE": "value_b"})

    merged = pandas.merge(data_a, data_b, on="ID", how='outer')
    violations = merged[(merged['value_a'] != merged['value_b']) | merged['value_a'].isna() | merged['value_b'].isna()]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": property_a,
        "VALUE": violations["value_a"].values,
        "VIOLATION_TYPE": "sh:equals",
        "MESSAGE": [f"Property {property_a} ('{a}') does not equal {property_b} ('{b}')" for a, b in zip(violations["value_a"].values, violations["value_b"].values)]
    })
    return result


def _disjoint(df, property_a, property_b):
    """ that two properties have no shared values."""
    data_a = df.query("KEY == @property_a")[["ID", "VALUE"]].rename(columns={"VALUE": "value_a"})
    data_b = df.query("KEY == @property_b")[["ID", "VALUE"]].rename(columns={"VALUE": "value_b"})

    merged = pandas.merge(data_a, data_b, on="ID")
    violations = merged[merged['value_a'] == merged['value_b']]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": property_a,
        "VALUE": violations["value_a"].values,
        "VIOLATION_TYPE": "sh:disjoint",
        "MESSAGE": [f"Properties {property_a} and {property_b} have shared value '{v}'" for v in violations["value_a"].values]
    })
    return result


def _less_than(df, property_a, property_b):
    """ that property_a values are less than property_b values."""
    data_a = df.query("KEY == @property_a")[["ID", "VALUE"]].rename(columns={"VALUE": "value_a"})
    data_b = df.query("KEY == @property_b")[["ID", "VALUE"]].rename(columns={"VALUE": "value_b"})

    merged = pandas.merge(data_a, data_b, on="ID")
    merged['numeric_a'] = pandas.to_numeric(merged['value_a'], errors='coerce')
    merged['numeric_b'] = pandas.to_numeric(merged['value_b'], errors='coerce')

    violations = merged[merged['numeric_a'] >= merged['numeric_b']]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": property_a,
        "VALUE": violations["value_a"].values,
        "VIOLATION_TYPE": "sh:lessThan",
        "MESSAGE": [f"Property {property_a} value {a} is not less than {property_b} value {b}" for a, b in zip(violations["value_a"].values, violations["value_b"].values)]
    })
    return result


def _closed(df, allowed_properties, target_type=None):
    """ that only allowed properties are used."""
    if target_type:
        type_data = df.query("KEY == 'Type' and VALUE == @target_type")
        target_ids = type_data["ID"].unique()
        data_to_check = df[df["ID"].isin(target_ids)]
    else:
        data_to_check = df

    violations = data_to_check[~data_to_check['KEY'].isin(allowed_properties)]

    if violations.empty:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": violations["ID"].values,
        "KEY": violations["KEY"].values,
        "VALUE": violations["VALUE"].values,
        "VIOLATION_TYPE": "sh:closed",
        "MESSAGE": [f"Property '{k}' is not in allowed properties list" for k in violations["KEY"].values]
    })
    return result


def _and(df, property_path, constraints):
    """ that all constraints are satisfied (logical AND)."""
    all_violations = []

    for constraint_type, params in constraints:
        validator = CONSTRAINT_VALIDATORS.get(constraint_type)
        if validator:
            if constraint_type in ['sh:equals', 'sh:disjoint', 'sh:lessThan']:
                violations = validator(df, **params)
            else:
                violations = validator(df, property_path, **params)
            all_violations.append(violations)

    if not all_violations:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.concat(all_violations, ignore_index=True)
    return result


def _or(df, property_path, constraints):
    """ that at least one constraint is satisfied (logical OR)."""
    constraint_results = []

    for constraint_type, params in constraints:
        validator = CONSTRAINT_VALIDATORS.get(constraint_type)
        if validator:
            if constraint_type in ['sh:equals', 'sh:disjoint', 'sh:lessThan']:
                violations = validator(df, **params)
            else:
                violations = validator(df, property_path, **params)
            constraint_results.append(violations)

    if not constraint_results:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    all_violation_ids = set()
    for violations in constraint_results:
        all_violation_ids.update(violations["ID"].unique())

    ids_failing_all = all_violation_ids
    for violations in constraint_results:
        passing_ids = set(df["ID"].unique()) - set(violations["ID"].unique())
        ids_failing_all = ids_failing_all - passing_ids

    if not ids_failing_all:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    result = pandas.DataFrame({
        "ID": list(ids_failing_all),
        "KEY": property_path,
        "VALUE": None,
        "VIOLATION_TYPE": "sh:or",
        "MESSAGE": [f"No constraint satisfied for property {property_path}" for _ in ids_failing_all]
    })
    return result


def _not(df, property_path, constraint):
    """ that constraint is NOT satisfied (logical NOT)."""
    constraint_type, params = constraint
    validator = CONSTRAINT_VALIDATORS.get(constraint_type)

    if not validator:
        return pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

    if constraint_type in ['sh:equals', 'sh:disjoint', 'sh:lessThan']:
        violations = validator(df, **params)
    else:
        violations = validator(df, property_path, **params)

    all_ids = df["ID"].unique()
    violating_ids = violations["ID"].unique()
    passing_ids = [id for id in all_ids if id not in violating_ids]

    result = pandas.DataFrame({
        "ID": passing_ids,
        "KEY": property_path,
        "VALUE": None,
        "VIOLATION_TYPE": "sh:not",
        "MESSAGE": [f"Constraint should not be satisfied for property {property_path}" for _ in passing_ids]
    })
    return result


CONSTRAINT_VALIDATORS = {
    'sh:minCount': _min_count,
    'sh:maxCount': _max_count,
    'sh:datatype': _datatype,
    'sh:minLength': _min_length,
    'sh:maxLength': _max_length,
    'sh:pattern': _pattern,
    'sh:hasValue': _has_value,
    'sh:in': _in,
    'sh:minInclusive': _min_inclusive,
    'sh:maxInclusive': _max_inclusive,
    'sh:class': _class,
    'sh:nodeKind': _node_kind,
    'sh:equals': _equals,
    'sh:disjoint': _disjoint,
    'sh:lessThan': _less_than,
    'sh:closed': _closed,
    'sh:and': _and,
    'sh:or': _or,
    'sh:not': _not,
}

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

