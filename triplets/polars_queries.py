"""Polars reimplementation of rdf_parser query functions.

All functions use lazy evaluation where possible for maximum performance.
Input is a Polars DataFrame (or LazyFrame) in triplet format [ID, KEY, VALUE, INSTANCE_ID].
"""
import polars as pl
import logging

logger = logging.getLogger(__name__)


def type_tableview(data: pl.DataFrame, type_name: str, string_to_number: bool = True, type_key: str = "Type") -> pl.DataFrame | None:
    """Pivot triplets of a given type into a table view (ID × KEY → VALUE)."""
    # Find IDs where KEY==type_key and VALUE==type_name
    type_ids = data.lazy().filter(
        (pl.col("KEY") == type_key) & (pl.col("VALUE") == type_name)
    ).select("ID")

    # Join to get all triplets for those IDs, dedup, pivot
    result = (
        data.lazy()
        .join(type_ids, on="ID", how="semi")
        .unique(subset=["ID", "KEY"])
        .collect()
        .pivot(on="KEY", index="ID", values="VALUE")
    )

    if result.is_empty():
        logger.warning(f"No data available for {type_name}")
        return None

    if string_to_number:
        # Cast columns that look numeric
        for col_name in result.columns:
            if col_name == "ID":
                continue
            try:
                numeric = result[col_name].cast(pl.Float64, strict=True)
                result = result.with_columns(numeric.alias(col_name))
            except (pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError):
                pass

    return result


def key_tableview(data: pl.DataFrame, key: str, string_to_number: bool = True) -> pl.DataFrame | None:
    """Pivot triplets containing a given key into a table view (ID × KEY → VALUE)."""
    key_ids = data.lazy().filter(pl.col("KEY") == key).select("ID")

    result = (
        data.lazy()
        .join(key_ids, on="ID", how="semi")
        .unique(subset=["ID", "KEY"])
        .collect()
        .pivot(on="KEY", index="ID", values="VALUE")
    )

    if result.is_empty():
        logger.warning(f"No data available for {key}")
        return None

    if string_to_number:
        for col_name in result.columns:
            if col_name == "ID":
                continue
            try:
                numeric = result[col_name].cast(pl.Float64, strict=True)
                result = result.with_columns(numeric.alias(col_name))
            except (pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError):
                pass

    return result


def id_tableview(data: pl.DataFrame, id, string_to_number: bool = True) -> pl.DataFrame | None:
    """Pivot triplets for specific ID(s) into a table view."""
    if isinstance(id, str):
        id = [id]
    if isinstance(id, list):
        id_filter = pl.Series("ID", id)
    else:
        # Assume DataFrame-like with "ID" column
        id_filter = id["ID"] if hasattr(id, "__getitem__") else id

    result = (
        data.lazy()
        .filter(pl.col("ID").is_in(id_filter))
        .unique(subset=["ID", "KEY"])
        .collect()
        .pivot(on="KEY", index="ID", values="VALUE")
    )

    if result.is_empty():
        logger.warning(f"No data available for {id}")
        return None

    if string_to_number:
        for col_name in result.columns:
            if col_name == "ID":
                continue
            try:
                numeric = result[col_name].cast(pl.Float64, strict=True)
                result = result.with_columns(numeric.alias(col_name))
            except (pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError):
                pass

    return result


def filter_by_type(data: pl.DataFrame, type_name: str, type_key: str = "Type") -> pl.DataFrame:
    """Filter triplets to only objects of a specific type."""
    type_ids = data.lazy().filter(
        (pl.col("KEY") == type_key) & (pl.col("VALUE") == type_name)
    ).select("ID")

    return data.lazy().join(type_ids, on="ID", how="semi").collect()


def filter_by_triplet(data: pl.DataFrame, filter_data: pl.DataFrame) -> pl.DataFrame:
    """Filter triplets by IDs present in another DataFrame."""
    filter_ids = filter_data.lazy().select("ID").unique()
    return data.lazy().join(filter_ids, on="ID", how="semi").collect()


def references_to(data: pl.DataFrame, reference: str, levels: int = 1) -> pl.DataFrame:
    """Find all objects that point TO the given reference ID."""
    # Level 0: the object itself
    object_data = data.filter(pl.col("ID") == reference).with_columns(pl.lit(0).alias("level"))

    objects_list = [object_data]
    current_ids = object_data.select("ID").unique()

    for level in range(1, levels + 1):
        # Find rows where VALUE matches any current ID (= objects pointing to current)
        ref_data = (
            data.lazy()
            .join(current_ids.lazy(), left_on="VALUE", right_on="ID", how="semi")
            .select("ID")
            .unique()
        )

        # Get all triplets for those referring objects
        referring = (
            data.lazy()
            .join(ref_data, on="ID", how="semi")
            .collect()
            .with_columns(pl.lit(level).alias("level"))
        )

        if referring.is_empty():
            break

        objects_list.append(referring)
        current_ids = referring.select("ID").unique()

    return pl.concat(objects_list)


def references_from(data: pl.DataFrame, reference: str, levels: int = 1) -> pl.DataFrame:
    """Find all objects that the given reference ID points FROM (outgoing references)."""
    # Level 0: the object itself
    object_data = data.filter(pl.col("ID") == reference).with_columns(pl.lit(0).alias("level"))

    objects_list = [object_data]
    # Values of current object are potential IDs of referenced objects
    current_values = object_data.select(pl.col("VALUE").alias("ID")).unique()

    for level in range(1, levels + 1):
        # Find triplets where ID matches a VALUE from current level
        referenced = (
            data.lazy()
            .join(current_values.lazy(), on="ID", how="semi")
            .collect()
            .with_columns(pl.lit(level).alias("level"))
        )

        if referenced.is_empty():
            break

        objects_list.append(referenced)
        current_values = referenced.select(pl.col("VALUE").alias("ID")).unique()

    return pl.concat(objects_list)


def references_all(data: pl.DataFrame) -> pl.DataFrame:
    """Find all unique references (ID_FROM → ID_TO via KEY)."""
    all_ids = data.lazy().select("ID").unique()

    return (
        data.lazy()
        .select(["ID", "KEY", "VALUE"])
        .unique()
        .join(all_ids, left_on="VALUE", right_on="ID", how="semi")
        .rename({"ID": "ID_FROM", "VALUE": "ID_TO"})
        .select(["ID_FROM", "KEY", "ID_TO"])
        .collect()
    )


def references_to_simple(data: pl.DataFrame, reference: str, columns: list = None) -> pl.DataFrame:
    """Simplified table view of objects pointing to reference."""
    if columns is None:
        columns = ["Type"]
    ref_data = references_to(data, reference, levels=1)

    # Get referring objects (level > 0) and pivot
    referring = ref_data.filter(pl.col("level") > 0)
    if referring.is_empty():
        return pl.DataFrame()

    # Get unique referring IDs
    ref_ids = referring.select("ID").unique()

    # Get all triplets for referring objects, pivot to table
    table = (
        data.lazy()
        .join(ref_ids.lazy(), on="ID", how="semi")
        .unique(subset=["ID", "KEY"])
        .collect()
        .pivot(on="KEY", index="ID", values="VALUE")
    )

    available = [c for c in columns if c in table.columns]
    return table.select(["ID"] + available) if available else table


def references_from_simple(data: pl.DataFrame, reference: str, columns: list = None) -> pl.DataFrame:
    """Simplified table view of objects that reference points to."""
    if columns is None:
        columns = ["Type"]
    ref_data = references_from(data, reference, levels=1)

    referenced = ref_data.filter(pl.col("level") > 0)
    if referenced.is_empty():
        return pl.DataFrame()

    ref_ids = referenced.select("ID").unique()
    table = (
        data.lazy()
        .join(ref_ids.lazy(), on="ID", how="semi")
        .unique(subset=["ID", "KEY"])
        .collect()
        .pivot(on="KEY", index="ID", values="VALUE")
    )

    available = [c for c in columns if c in table.columns]
    return table.select(["ID"] + available) if available else table
