import polars as pl
import logging

logger = logging.getLogger(__name__)

def _min_count(df, property_path, min_count, target_class=None, **kwargs):
    """ minimum cardinality constraint."""
    if target_class: target_ids = df.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class)).select("ID")
    else: target_ids = df.select("ID").unique()
    counts = df.filter(pl.col("KEY") == property_path).group_by("ID").agg(pl.len().alias("count"))
    violations = target_ids.join(counts, on="ID", how="left").with_columns(pl.col("count").fill_null(0)).filter(pl.col("count") < min_count)
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit(property_path).alias("KEY"), pl.lit(None, dtype=pl.Utf8).alias("VALUE"), pl.lit("sh:minCount").alias("VIOLATION_TYPE"),
        (pl.lit(f"Property {property_path} has count ") + pl.col("count").cast(pl.Utf8) + pl.lit(f" but requires minimum {min_count}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _max_count(df, property_path, max_count, target_class=None, **kwargs):
    """ maximum cardinality constraint."""
    data = df.filter(pl.col("KEY") == property_path)
    if target_class:
        target_ids = df.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class)).select("ID")
        data = data.join(target_ids, on="ID", how="inner")
    violations = data.group_by("ID").agg([pl.len().alias("count"), pl.first("VALUE").alias("VALUE")]).filter(pl.col("count") > max_count)
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit(property_path).alias("KEY"), pl.lit("sh:maxCount").alias("VIOLATION_TYPE"),
        (pl.lit(f"Property {property_path} appears ") + pl.col("count").cast(pl.Utf8) + pl.lit(f" times but maximum is {max_count}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _datatype(df, property_path, datatype, **kwargs):
    """ datatype constraint."""
    data = df.filter(pl.col("KEY") == property_path)
    if data.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    # Flag null values
    invalid_null = data.filter(pl.col("VALUE").is_null())
    
    to_check = data.filter(pl.col("VALUE").is_not_null())
    invalid_type = pl.DataFrame(schema=data.schema)
    
    if datatype in ["xsd:float", "xsd:double", "xsd:decimal"]:
        invalid_type = to_check.with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num").is_null())
    elif datatype in ["xsd:integer", "xsd:int", "xsd:long"]:
        # Cast to float first then check if integer
        invalid_type = to_check.with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num").is_null() | (pl.col("num") % 1 != 0))
    elif datatype == "xsd:boolean":
        invalid_type = to_check.filter(~pl.col("VALUE").str.to_lowercase().is_in(["true", "false", "1", "0"]))
    
    all_invalid = pl.concat([invalid_null, invalid_type]) if not invalid_type.is_empty() or not invalid_null.is_empty() else pl.DataFrame(schema=data.schema)
    
    if all_invalid.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return all_invalid.with_columns([
        pl.lit("sh:datatype").alias("VIOLATION_TYPE"),
        (pl.lit(f"Value is not a valid {datatype}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _min_length(df, property_path, min_length, **kwargs):
    """ minimum length constraint."""
    violations = df.filter((pl.col("KEY") == property_path) & (pl.col("VALUE").str.len_chars() < min_length))
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:minLength").alias("VIOLATION_TYPE"),
        (pl.lit(f"String length ") + pl.col("VALUE").str.len_chars().cast(pl.Utf8) + pl.lit(f" is less than minimum {min_length}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _max_length(df, property_path, max_length, **kwargs):
    """ maximum length constraint."""
    violations = df.filter((pl.col("KEY") == property_path) & (pl.col("VALUE").str.len_chars() > max_length))
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:maxLength").alias("VIOLATION_TYPE"),
        (pl.lit(f"String length ") + pl.col("VALUE").str.len_chars().cast(pl.Utf8) + pl.lit(f" is greater than maximum {max_length}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _pattern(df, property_path, regex, **kwargs):
    """ pattern constraint."""
    violations = df.filter((pl.col("KEY") == property_path) & (~pl.col("VALUE").str.contains(regex)))
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:pattern").alias("VIOLATION_TYPE"),
        (pl.lit(f"Value '") + pl.col("VALUE") + pl.lit(f"' does not match pattern: {regex}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _min_inclusive(df, property_path, min_value, **kwargs):
    """ min inclusive constraint."""
    violations = df.filter(pl.col("KEY") == property_path).with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num") < min_value)
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:minInclusive").alias("VIOLATION_TYPE"),
        (pl.lit("Value ") + pl.col("num").cast(pl.Utf8) + pl.lit(f" is less than minimum {min_value}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _max_inclusive(df, property_path, max_value, **kwargs):
    """ max inclusive constraint."""
    violations = df.filter(pl.col("KEY") == property_path).with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num") > max_value)
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:maxInclusive").alias("VIOLATION_TYPE"),
        (pl.lit("Value ") + pl.col("num").cast(pl.Utf8) + pl.lit(f" is greater than maximum {max_value}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _class(df, property_path, target_class, check_external=True, **kwargs):
    """ class constraint."""
    refs = df.filter(pl.col("KEY") == property_path)
    if refs.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    if not check_external:
        present_ids = df.select("ID").unique()
        refs = refs.filter(pl.col("VALUE").is_in(present_ids["ID"]))
        if refs.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    types = df.filter(pl.col("KEY") == "Type").select([pl.col("ID"), pl.col("VALUE").alias("ActualType")])
    violations = refs.join(types, left_on="VALUE", right_on="ID", how="left").filter((pl.col("ActualType") != target_class) | (pl.col("ActualType").is_null()))
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:class").alias("VIOLATION_TYPE"),
        (pl.lit(f"Referenced object has type ") + pl.col("ActualType").fill_null("Unknown") + pl.lit(f" but expected {target_class}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _node_kind(df, property_path, node_kind, **kwargs):
    """ node kind."""
    data = df.filter(pl.col("KEY") == property_path)
    if data.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    all_ids = set(df.select("ID").unique()["ID"].to_list())
    
    if node_kind == "sh:IRI":
        # Simplified triplets: nulls are not IRIs, alphanumeric strings are
        violations = data.filter(pl.col("VALUE").is_null())
    elif node_kind == "sh:BlankNode":
        violations = data.filter(~pl.col("VALUE").str.starts_with("_:"))
    elif node_kind == "sh:Literal":
        # Literal if not a known ID
        violations = data.filter(pl.col("VALUE").is_in(list(all_ids)))
    else: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    if violations.is_empty(): return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:nodeKind").alias("VIOLATION_TYPE"),
        (pl.lit(f"Value is not of node kind {node_kind}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _and(df, property_path, constraints, **kwargs):
    """ logical AND."""
    all_violations = []
    for constraint in constraints:
        violations = validate(df, [constraint], **kwargs)
        if not violations.is_empty(): all_violations.append(violations)
    if not all_violations: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pl.concat(all_violations)

def _or(df, property_path, constraints, **kwargs):
    """ logical OR."""
    all_ids = df.select("ID").unique()["ID"].to_list()
    failed_ids_per_alt = []
    all_nested_violations = []
    for constraint in constraints:
        violations = validate(df, [constraint], **kwargs)
        failed_ids = set(violations["ID"].to_list())
        failed_ids_per_alt.append(failed_ids)
        all_nested_violations.append(violations)
    
    if not failed_ids_per_alt: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    failing_ids = set(all_ids)
    for failed in failed_ids_per_alt: failing_ids = failing_ids.intersection(failed)
    if not failing_ids: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    
    violation_reports = []
    for violations in all_nested_violations:
        report = violations.filter(pl.col("ID").is_in(list(failing_ids)))
        if not report.is_empty(): violation_reports.append(report)
    if not violation_reports:
        return pl.DataFrame({"ID": list(failing_ids), "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:or", "ERROR_MESSAGE": [f"None of the OR constraints satisfied" for _ in failing_ids]})
    return pl.concat(violation_reports)

def _not(df, property_path, constraint, **kwargs):
    """ logical NOT."""
    violations = validate(df, [constraint], **kwargs)
    all_ids = df.select("ID").unique()["ID"].to_list()
    violating_ids = set(violations["ID"].to_list())
    passed_ids = set(all_ids) - violating_ids
    if not passed_ids: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pl.DataFrame({"ID": list(passed_ids), "KEY": property_path, "VALUE": None, "VIOLATION_TYPE": "sh:not", "ERROR_MESSAGE": [f"Constraint should not be satisfied" for _ in passed_ids]})

CONSTRAINT_VALIDATORS = {
    'sh:minCount': _min_count, 'sh:maxCount': _max_count, 'sh:datatype': _datatype, 'sh:minLength': _min_length,
    'sh:maxLength': _max_length, 'sh:pattern': _pattern, 'sh:minInclusive': _min_inclusive, 'sh:maxInclusive': _max_inclusive,
    'sh:class': _class, 'sh:nodeKind': _node_kind, 'sh:and': _and, 'sh:or': _or, 'sh:not': _not
}

INTERNAL_TO_SHACL = {
    'min_count': 'sh:minCount', 'max_count': 'sh:maxCount', 'datatype': 'sh:datatype', 'min_length': 'sh:minLength',
    'max_length': 'sh:maxLength', 'pattern': 'sh:pattern', 'min_inclusive': 'sh:minInclusive', 'max_inclusive': 'sh:maxInclusive',
    'target_class': 'sh:class', 'node_kind': 'sh:nodeKind', 'has_value': 'sh:hasValue', 'allowed_values': 'sh:in',
    'equals': 'sh:equals', 'disjoint': 'sh:disjoint', 'less_than': 'sh:lessThan', 'closed': 'sh:closed',
    'and': 'sh:and', 'or': 'sh:or', 'not': 'sh:not'
}

def validate(df, rules, check_external=True, **kwargs):
    """ Apply Polars validators to a list of SHACL constraint dictionaries."""
    all_violations = []
    for constraint in rules:
        target_class = constraint.get('class')
        df_to_check = df
        if target_class:
            target_ids = df.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class)).select("ID").unique()
            df_to_check = df.join(target_ids, on="ID", how="inner")
            if df_to_check.is_empty(): continue

        prop = constraint.get('property')
        for key, value in constraint.items():
            shacl_term = INTERNAL_TO_SHACL.get(key)
            if shacl_term and shacl_term in CONSTRAINT_VALIDATORS:
                validator = CONSTRAINT_VALIDATORS[shacl_term]
                violations = validator(df_to_check, prop, value, target_class=target_class, check_external=check_external) if shacl_term != 'sh:class' else validator(df_to_check, prop, value, check_external=check_external)
                if not violations.is_empty():
                    if 'severity' in constraint: violations = violations.with_columns(pl.lit(constraint['severity']).alias("SEVERITY"))
                    if 'rule_name' in constraint: violations = violations.with_columns(pl.lit(constraint['rule_name']).alias("RULE_NAME"))
                    if 'id' in constraint: violations = violations.with_columns(pl.lit(constraint['id']).alias("SOURCE_SHAPE"))
                    if 'description' in constraint: violations = violations.with_columns(pl.lit(constraint['description']).alias("DESCRIPTION"))
                    if 'message' in constraint: violations = violations.with_columns(pl.lit(constraint['message']).alias("MESSAGE"))
                    all_violations.append(violations)
    if not all_violations: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pl.concat(all_violations)
