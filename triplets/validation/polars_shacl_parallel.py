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
        (pl.lit(f"Property {property_path} has count ") + pl.col("count").cast(pl.Utf8) + pl.lit(f" but requires minimum {min_count}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

def _max_count(lf, property_path, max_count, target_class=None, **kwargs):
    """ maximum cardinality constraint (Lazy)."""
    data = lf.filter(pl.col("KEY") == property_path)
    if target_class:
        target_ids = lf.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class)).select("ID")
        data = data.join(target_ids, on="ID", how="inner")
    return data.group_by("ID").agg([pl.len().alias("count"), pl.first("VALUE").alias("VALUE")]).filter(pl.col("count") > max_count).with_columns([
        pl.lit(property_path).alias("KEY"), pl.lit("sh:maxCount").alias("VIOLATION_TYPE"),
        (pl.lit(f"Property {property_path} appears ") + pl.col("count").cast(pl.Utf8) + pl.lit(f" times but maximum is {max_count}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

def _datatype(lf, property_path, datatype, **kwargs):
    """ datatype constraint (Lazy)."""
    data = lf.filter(pl.col("KEY") == property_path)
    if datatype in ["xsd:float", "xsd:double", "xsd:decimal"]:
        violations = data.with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num").is_null() & pl.col("VALUE").is_not_null())
    elif datatype in ["xsd:integer", "xsd:int", "xsd:long"]:
        violations = data.with_columns(pl.col("VALUE").cast(pl.Int64, strict=False).alias("num")).filter(pl.col("num").is_null() & pl.col("VALUE").is_not_null())
    elif datatype == "xsd:boolean":
        violations = data.filter(~pl.col("VALUE").is_in(["true", "false", "1", "0"]))
    else: return pl.LazyFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])
    return violations.with_columns([
        pl.lit("sh:datatype").alias("VIOLATION_TYPE"),
        (pl.lit(f"Value '") + pl.col("VALUE") + pl.lit(f"' is not a valid {datatype}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

def _min_length(lf, property_path, min_length, **kwargs):
    """ minimum length constraint (Lazy)."""
    return lf.filter((pl.col("KEY") == property_path) & (pl.col("VALUE").str.len_chars() < min_length)).with_columns([
        pl.lit("sh:minLength").alias("VIOLATION_TYPE"),
        (pl.lit(f"String length ") + pl.col("VALUE").str.len_chars().cast(pl.Utf8) + pl.lit(f" is less than minimum {min_length}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

def _max_length(lf, property_path, max_length, **kwargs):
    """ maximum length constraint (Lazy)."""
    return lf.filter((pl.col("KEY") == property_path) & (pl.col("VALUE").str.len_chars() > max_length)).with_columns([
        pl.lit("sh:maxLength").alias("VIOLATION_TYPE"),
        (pl.lit(f"String length ") + pl.col("VALUE").str.len_chars().cast(pl.Utf8) + pl.lit(f" is greater than maximum {max_length}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

def _pattern(lf, property_path, regex, **kwargs):
    """ pattern constraint (Lazy)."""
    return lf.filter((pl.col("KEY") == property_path) & (~pl.col("VALUE").str.contains(regex))).with_columns([
        pl.lit("sh:pattern").alias("VIOLATION_TYPE"),
        (pl.lit(f"Value '") + pl.col("VALUE") + pl.lit(f"' does not match pattern: {regex}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

def _min_inclusive(lf, property_path, min_value, **kwargs):
    """ min inclusive constraint (Lazy)."""
    return lf.filter(pl.col("KEY") == property_path).with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num") < min_value).with_columns([
        pl.lit("sh:minInclusive").alias("VIOLATION_TYPE"),
        (pl.lit("Value ") + pl.col("num").cast(pl.Utf8) + pl.lit(f" is less than minimum {min_value}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

def _max_inclusive(lf, property_path, max_value, **kwargs):
    """ max inclusive constraint (Lazy)."""
    return lf.filter(pl.col("KEY") == property_path).with_columns(pl.col("VALUE").cast(pl.Float64, strict=False).alias("num")).filter(pl.col("num") > max_value).with_columns([
        pl.lit("sh:maxInclusive").alias("VIOLATION_TYPE"),
        (pl.lit("Value ") + pl.col("num").cast(pl.Utf8) + pl.lit(f" is greater than maximum {max_value}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])

def _class(lf, property_path, target_class, check_external=True, **kwargs):
    """ class constraint (Lazy)."""
    refs = lf.filter(pl.col("KEY") == property_path)

    if not check_external:
        # Filter out external references to match original engine behavior
        present_ids = lf.select("ID").unique()
        refs = refs.join(present_ids, left_on="VALUE", right_on="ID", how="inner")

    types = lf.filter(pl.col("KEY") == "Type").select([pl.col("ID"), pl.col("VALUE").alias("ActualType")])
    # Left join to keep references even if their type is not found (external or missing type)
    # Comparison must account for nulls
    return refs.join(types, left_on="VALUE", right_on="ID", how="left").filter(
        (pl.col("ActualType") != target_class) | (pl.col("ActualType").is_null())
    ).with_columns([
        pl.lit("sh:class").alias("VIOLATION_TYPE"),
        (pl.lit(f"Referenced object has type ") + pl.col("ActualType").fill_null("Unknown") + pl.lit(f" but expected {target_class}")).alias("MESSAGE")
    ]).select(["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])


CONSTRAINT_VALIDATORS = {
    'sh:minCount': _min_count, 'sh:maxCount': _max_count, 'sh:datatype': _datatype, 'sh:minLength': _min_length,
    'sh:maxLength': _max_length, 'sh:pattern': _pattern, 'sh:minInclusive': _min_inclusive, 'sh:maxInclusive': _max_inclusive,
    'sh:class': _class
}

INTERNAL_TO_SHACL = {
    'min_count': 'sh:minCount', 'max_count': 'sh:maxCount', 'datatype': 'sh:datatype', 'min_length': 'sh:minLength',
    'max_length': 'sh:maxLength', 'pattern': 'sh:pattern', 'min_inclusive': 'sh:minInclusive', 'max_inclusive': 'sh:maxInclusive',
    'target_class': 'sh:class'
}

def validate(df, rules, check_external=True, **kwargs):
    """ Apply Polars validators in parallel using Lazy API."""
    lf = df.lazy() if isinstance(df, pl.DataFrame) else df
    lazy_tasks = []
    
    for constraint in rules:
        prop = constraint['property']
        target_class = constraint.get('class')
        
        for key, value in constraint.items():
            shacl_term = INTERNAL_TO_SHACL.get(key)
            if shacl_term and shacl_term in CONSTRAINT_VALIDATORS:
                validator = CONSTRAINT_VALIDATORS[shacl_term]
                
                # Original engine doesn't filter by target_class before calling validator
                # Create lazy task
                task = validator(lf, prop, value, target_class=target_class, check_external=check_external) if shacl_term != 'sh:class' else validator(lf, prop, value, check_external=check_external)
                
                # Add metadata columns lazily
                if 'severity' in constraint: task = task.with_columns(pl.lit(constraint['severity']).alias("SEVERITY"))
                if 'rule_name' in constraint: task = task.with_columns(pl.lit(constraint['rule_name']).alias("RULE_NAME"))
                
                lazy_tasks.append(task)

    if not lazy_tasks: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])
    
    # Parallel execution via collect_all
    results = pl.collect_all(lazy_tasks)
    
    # Filter out empty results and concat
    non_empty = [res for res in results if not res.is_empty()]
    if not non_empty: return pl.DataFrame(schema=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE"])
    return pl.concat(non_empty)
