import polars as pl
import logging

logger = logging.getLogger(__name__)

def _min_count(lf, property_path, min_count, target_class=None, **kwargs):
    """ minimum cardinality constraint (Lazy)."""
    if target_class: target_ids = lf.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class)).select("ID")
    else: target_ids = lf.select("ID").unique()
    counts = lf.filter(pl.col("KEY") == property_path).group_by("ID").agg(pl.len().alias("count"))
    return target_ids.join(counts, on="ID", how="left").with_columns(pl.col("count").fill_null(0)).filter(pl.col("count") < min_count).with_columns([
        pl.lit(property_path).alias("KEY"), pl.lit(None, dtype=pl.Utf8).alias("VALUE"), pl.lit("sh:minCount").alias("VIOLATION_TYPE"),
        (pl.lit(f"Property {property_path} has count ") + pl.col("count").cast(pl.Utf8) + pl.lit(f" but requires minimum {min_count}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _max_count(lf, property_path, max_count, target_class=None, **kwargs):
    """ maximum cardinality constraint (Lazy)."""
    data = lf.filter(pl.col("KEY") == property_path)
    if target_class:
        target_ids = lf.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class)).select("ID")
        data = data.join(target_ids, on="ID", how="inner")
    return data.group_by("ID").agg([pl.len().alias("count"), pl.first("VALUE").alias("VALUE")]).filter(pl.col("count") > max_count).with_columns([
        pl.lit(property_path).alias("KEY"), pl.lit("sh:maxCount").alias("VIOLATION_TYPE"),
        (pl.lit(f"Property {property_path} appears ") + pl.col("count").cast(pl.Utf8) + pl.lit(f" times but maximum is {max_count}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _datatype(lf, property_path, datatype, **kwargs):
    """ datatype constraint (Lazy)."""
    data = lf.filter(pl.col("KEY") == property_path)
    
    # Flag null values
    invalid_null = data.filter(pl.col("VALUE").is_null())
    
    to_check = data.filter(pl.col("VALUE").is_not_null())
    invalid_type = pl.LazyFrame(schema=data.schema)
    
    if datatype in ["xsd:float", "xsd:double", "xsd:decimal"]:
        invalid_type = to_check.with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num").is_null()).drop("num")
    elif datatype in ["xsd:integer", "xsd:int", "xsd:long"]:
        invalid_type = to_check.with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num").is_null() | (pl.col("num") % 1 != 0)).drop("num")
    elif datatype == "xsd:boolean":
        invalid_type = to_check.filter(~pl.col("VALUE").str.to_lowercase().is_in(["true", "false", "1", "0"]))
    
    # Both inputs now have the same schema (the original data schema)
    all_invalid = pl.concat([invalid_null, invalid_type])
    
    return all_invalid.with_columns([
        pl.lit("sh:datatype").alias("VIOLATION_TYPE"),
        (pl.lit(f"Value is not a valid {datatype}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _min_length(lf, property_path, min_length, **kwargs):
    """ minimum length constraint (Lazy)."""
    return lf.filter((pl.col("KEY") == property_path) & (pl.col("VALUE").str.len_chars() < min_length)).with_columns([
        pl.lit("sh:minLength").alias("VIOLATION_TYPE"),
        (pl.lit(f"String length ") + pl.col("VALUE").str.len_chars().cast(pl.Utf8) + pl.lit(f" is less than minimum {min_length}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _max_length(lf, property_path, max_length, **kwargs):
    """ maximum length constraint (Lazy)."""
    return lf.filter((pl.col("KEY") == property_path) & (pl.col("VALUE").str.len_chars() > max_length)).with_columns([
        pl.lit("sh:maxLength").alias("VIOLATION_TYPE"),
        (pl.lit(f"String length ") + pl.col("VALUE").str.len_chars().cast(pl.Utf8) + pl.lit(f" is greater than maximum {max_length}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _pattern(lf, property_path, regex, **kwargs):
    """ pattern constraint (Lazy)."""
    return lf.filter((pl.col("KEY") == property_path) & (~pl.col("VALUE").str.contains(regex))).with_columns([
        pl.lit("sh:pattern").alias("VIOLATION_TYPE"),
        (pl.lit(f"Value '") + pl.col("VALUE") + pl.lit(f"' does not match pattern: {regex}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _min_inclusive(lf, property_path, min_value, **kwargs):
    """ min inclusive constraint (Lazy)."""
    return lf.filter(pl.col("KEY") == property_path).with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num") < min_value).with_columns([
        pl.lit("sh:minInclusive").alias("VIOLATION_TYPE"),
        (pl.lit("Value ") + pl.col("num").cast(pl.Utf8) + pl.lit(f" is less than minimum {min_value}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _max_inclusive(lf, property_path, max_value, **kwargs):
    """ max inclusive constraint (Lazy)."""
    return lf.filter(pl.col("KEY") == property_path).with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num") > max_value).with_columns([
        pl.lit("sh:maxInclusive").alias("VIOLATION_TYPE"),
        (pl.lit("Value ") + pl.col("num").cast(pl.Utf8) + pl.lit(f" is greater than maximum {max_value}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _class(lf, property_path, target_class, check_external=True, **kwargs):
    """ class constraint (Lazy)."""
    refs = lf.filter(pl.col("KEY") == property_path)
    if not check_external:
        present_ids = lf.select("ID").unique()
        refs = refs.join(present_ids, left_on="VALUE", right_on="ID", how="inner")
    types = lf.filter(pl.col("KEY") == "Type").select([pl.col("ID"), pl.col("VALUE").alias("ActualType")])
    return refs.join(types, left_on="VALUE", right_on="ID", how="left").filter((pl.col("ActualType") != target_class) | (pl.col("ActualType").is_null())).with_columns([
        pl.lit("sh:class").alias("VIOLATION_TYPE"),
        (pl.lit(f"Referenced object has type ") + pl.col("ActualType").fill_null("Unknown") + pl.lit(f" but expected {target_class}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _node_kind(lf, property_path, node_kind, **kwargs):
    """ node kind (Lazy)."""
    data = lf.filter(pl.col("KEY") == property_path)
    if node_kind == "sh:IRI":
        violations = data.filter(pl.col("VALUE").is_null())
    elif node_kind == "sh:BlankNode":
        violations = data.filter(~pl.col("VALUE").str.starts_with("_:"))
    elif node_kind == "sh:Literal":
        all_ids = lf.select("ID").unique()
        violations = data.join(all_ids, left_on="VALUE", right_on="ID", how="inner")
    else: return pl.LazyFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:nodeKind").alias("VIOLATION_TYPE"),
        (pl.lit(f"Value is not of node kind {node_kind}")).alias("ERROR_MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _and(lf, property_path, constraints, **kwargs):
    """ logical AND (Lazy)."""
    return pl.LazyFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _or(lf, property_path, constraints, **kwargs):
    """ logical OR (Lazy)."""
    return pl.LazyFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

def _not(lf, property_path, constraint, **kwargs):
    """ logical NOT (Lazy)."""
    return pl.LazyFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

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
    """ Apply Polars validators in parallel using Lazy API."""
    lf = df.lazy() if isinstance(df, pl.DataFrame) else df
    lazy_tasks = []
    for constraint in rules:
        target_class = constraint.get('class')
        lf_to_check = lf
        if target_class:
            target_ids = lf.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class)).select("ID").unique()
            lf_to_check = lf.join(target_ids, on="ID", how="inner")

        prop = constraint.get('property')
        for key, value in constraint.items():
            shacl_term = INTERNAL_TO_SHACL.get(key)
            if shacl_term and shacl_term in CONSTRAINT_VALIDATORS:
                validator = CONSTRAINT_VALIDATORS[shacl_term]
                task = validator(lf_to_check, prop, value, target_class=target_class, check_external=check_external) if shacl_term != 'sh:class' else validator(lf_to_check, prop, value, check_external=check_external)
                if 'severity' in constraint: task = task.with_columns(pl.lit(constraint['severity']).alias("SEVERITY"))
                if 'rule_name' in constraint: task = task.with_columns(pl.lit(constraint['rule_name']).alias("RULE_NAME"))
                if 'id' in constraint: task = task.with_columns(pl.lit(constraint['id']).alias("SOURCE_SHAPE"))
                if 'description' in constraint: task = task.with_columns(pl.lit(constraint['description']).alias("DESCRIPTION"))
                if 'message' in constraint: task = task.with_columns(pl.lit(constraint['message']).alias("MESSAGE"))
                lazy_tasks.append(task)
    if not lazy_tasks: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    results = pl.collect_all(lazy_tasks)
    non_empty = [res for res in results if not res.is_empty()]
    if not non_empty: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])
    return pl.concat(non_empty)
