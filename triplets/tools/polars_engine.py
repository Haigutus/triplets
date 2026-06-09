"""Polars-native implementation of triplet data manipulation tools.

Uses polars lazy evaluation, hash joins, and native operations for performance.
All functions accept and return polars DataFrames.
"""

import logging
import polars as pl

logger = logging.getLogger(__name__)


def type_tableview(data, type_name, string_to_number=True, type_key="Type", multivalue=False):
    """Create a table view of all objects of a specified type using polars pivot."""
    type_ids = data.filter(
        (pl.col("VALUE") == type_name) & (pl.col("KEY") == type_key)
    ).select("ID")

    if type_ids.is_empty():
        logger.warning(f'No data available for {type_name}')
        return None

    type_data = type_ids.join(data, on="ID", how="inner")

    if multivalue:
        data_view = type_data.group_by(["ID", "KEY"]).agg(
            pl.when(pl.col("VALUE").count() == 1)
            .then(pl.col("VALUE").first())
            .otherwise(pl.col("VALUE").implode().list.eval(pl.element()).first())
            .alias("VALUE")
        ).pivot(on="KEY", index="ID", values="VALUE")
    else:
        type_data = type_data.unique(subset=["ID", "KEY"], keep="first")
        data_view = type_data.pivot(on="KEY", index="ID", values="VALUE")

    if string_to_number:
        for col in data_view.columns:
            if col == "ID":
                continue
            try:
                data_view = data_view.with_columns(
                    pl.col(col).cast(pl.Float64, strict=True)
                )
            except (pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError):
                pass

    return data_view


def key_tableview(data, key, string_to_number=True):
    """Create a table view of all objects with a specified key."""
    key_ids = data.filter(pl.col("KEY") == key).select("ID")

    if key_ids.is_empty():
        logger.warning(f'No data available for key: {key}')
        return None

    key_data = key_ids.join(data, on="ID", how="inner").unique(subset=["ID", "KEY"], keep="first")
    data_view = key_data.pivot(on="KEY", index="ID", values="VALUE")

    if string_to_number:
        for col in data_view.columns:
            if col == "ID":
                continue
            try:
                data_view = data_view.with_columns(pl.col(col).cast(pl.Float64, strict=True))
            except (pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError):
                pass

    return data_view


def id_tableview(data, id, string_to_number=True):
    """Create a table view of a specific object by ID."""
    obj_data = data.filter(pl.col("ID") == id).unique(subset=["ID", "KEY"], keep="first")

    if obj_data.is_empty():
        logger.warning(f'No data available for ID: {id}')
        return None

    data_view = obj_data.pivot(on="KEY", index="ID", values="VALUE")

    if string_to_number:
        for col in data_view.columns:
            if col == "ID":
                continue
            try:
                data_view = data_view.with_columns(pl.col(col).cast(pl.Float64, strict=True))
            except (pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError):
                pass

    return data_view


def types_dict(data):
    """Return dict of {type_name: count}."""
    types = data.filter(pl.col("KEY") == "Type").group_by("VALUE").agg(
        pl.col("ID").n_unique().alias("count")
    )
    return dict(zip(types["VALUE"].to_list(), types["count"].to_list()))


def get_object_data(data, object_UUID):
    """Get all data for a specific object."""
    obj = data.filter(pl.col("ID") == object_UUID)
    if obj.is_empty():
        return None
    # Return as a simple key-value series (polars doesn't have Series.set_index like pandas)
    return obj.select(["KEY", "VALUE"])


def get_namespace_map(data):
    """Extract namespace map from triplet data."""
    nsmap_ids = data.filter(
        (pl.col("KEY") == "Type") & (pl.col("VALUE") == "NamespaceMap")
    ).select("ID")
    if nsmap_ids.is_empty():
        return pl.DataFrame()
    nsmap_data = nsmap_ids.join(data, on="ID", how="inner")
    nsmap_data = nsmap_data.filter(pl.col("KEY") != "Type")
    return nsmap_data


def references_to_simple(data, reference, columns=["Type"]):
    """Find objects that reference the given ID (simple, one level)."""
    # Find rows where VALUE == reference (these are references TO the object)
    refs = data.filter(pl.col("VALUE") == reference).select("ID").unique()
    if refs.is_empty():
        return pl.DataFrame(schema={"ID": pl.Utf8, "KEY": pl.Utf8, "VALUE": pl.Utf8})
    ref_data = refs.join(data, on="ID", how="inner")
    if columns:
        ref_data = ref_data.filter(pl.col("KEY").is_in(columns))
    return ref_data


def references_to(data, reference, levels=1):
    """Find objects that reference the given ID, traversing multiple levels."""
    result = references_to_simple(data, reference)
    seen_ids = set(result["ID"].to_list()) if not result.is_empty() else set()

    for _ in range(levels - 1):
        new_refs = []
        for ref_id in seen_ids.copy():
            level_refs = references_to_simple(data, ref_id)
            if not level_refs.is_empty():
                new_ids = set(level_refs["ID"].to_list()) - seen_ids
                if new_ids:
                    seen_ids.update(new_ids)
                    new_refs.append(level_refs)
        if not new_refs:
            break
        result = pl.concat([result] + new_refs)

    return result


def references_from_simple(data, reference, columns=["Type"]):
    """Find objects referenced BY the given ID (simple, one level)."""
    ref_values = data.filter(
        (pl.col("ID") == reference) & (pl.col("KEY") != "Type")
    ).select("VALUE").unique()

    if ref_values.is_empty():
        return pl.DataFrame(schema={"ID": pl.Utf8, "KEY": pl.Utf8, "VALUE": pl.Utf8})

    ref_ids = ref_values.rename({"VALUE": "ID"})
    ref_data = ref_ids.join(data, on="ID", how="inner")
    if columns:
        ref_data = ref_data.filter(pl.col("KEY").is_in(columns))
    return ref_data


def references_from(data, reference, levels=1):
    """Find objects referenced BY the given ID, traversing multiple levels."""
    result = references_from_simple(data, reference)
    seen_ids = set(result["ID"].to_list()) if not result.is_empty() else set()

    for _ in range(levels - 1):
        new_refs = []
        for ref_id in seen_ids.copy():
            level_refs = references_from_simple(data, ref_id)
            if not level_refs.is_empty():
                new_ids = set(level_refs["ID"].to_list()) - seen_ids
                if new_ids:
                    seen_ids.update(new_ids)
                    new_refs.append(level_refs)
        if not new_refs:
            break
        result = pl.concat([result] + new_refs)

    return result


def references_all(data):
    """Find all reference pairs in the dataset."""
    # References are rows where VALUE matches another object's ID
    all_ids = data.select("ID").unique()
    refs = data.join(all_ids.rename({"ID": "VALUE"}), on="VALUE", how="semi")
    refs = refs.filter(pl.col("KEY") != "Type")
    return refs


def references_simple(data, reference, columns=None, levels=1):
    """Find references both to and from an object."""
    to_refs = references_to(data, reference, levels=levels)
    from_refs = references_from(data, reference, levels=levels)

    parts = []
    if not to_refs.is_empty():
        parts.append(to_refs)
    if not from_refs.is_empty():
        parts.append(from_refs)

    if not parts:
        return pl.DataFrame(schema={"ID": pl.Utf8, "KEY": pl.Utf8, "VALUE": pl.Utf8})

    result = pl.concat(parts).unique()
    if columns:
        result = result.filter(pl.col("KEY").is_in(columns))
    return result


def references(data, ID, levels=1):
    """Find all references for an object (both directions)."""
    return references_simple(data, ID, levels=levels)


def filter_by_type(data, type_name, type_key="Type"):
    """Filter triplet data to only include objects of a specific type."""
    type_ids = data.filter(
        (pl.col("KEY") == type_key) & (pl.col("VALUE") == type_name)
    ).select("ID")
    return type_ids.join(data, on="ID", how="inner")


def filter_by_triplet(data, filter_triplet):
    """Filter data to rows matching the filter triplet."""
    return data.join(filter_triplet.select(["ID", "KEY", "VALUE"]), on=["ID", "KEY", "VALUE"], how="semi")


def set_VALUE_at_KEY(data, key, value):
    """Set VALUE for all rows with a given KEY (in-place mutation via reassignment)."""
    return data.with_columns(
        pl.when(pl.col("KEY") == key)
        .then(pl.lit(str(value)))
        .otherwise(pl.col("VALUE"))
        .alias("VALUE")
    )


def set_VALUE_at_KEY_and_ID(data, key, value, id):
    """Set VALUE for a specific KEY and ID combination."""
    return data.with_columns(
        pl.when((pl.col("KEY") == key) & (pl.col("ID") == id))
        .then(pl.lit(str(value)))
        .otherwise(pl.col("VALUE"))
        .alias("VALUE")
    )


def triplet_to_tableviews(triplet_df, multivalue=False):
    """Convert triplet DataFrame to dict of tableview DataFrames."""
    td = types_dict(triplet_df)
    tableviews = {}
    for class_name in td:
        tv = type_tableview(triplet_df, class_name, multivalue=multivalue)
        if tv is not None:
            tableviews[class_name] = tv
    return tableviews


def tableviews_to_triplet(tableviews, multivalue=False):
    """Convert dict of tableview DataFrames to triplet DataFrame."""
    all_triplets = []
    for class_name, df in tableviews.items():
        if "Type" not in df.columns:
            df = df.with_columns(pl.lit(class_name).alias("Type"))
        triplet = tableview_to_triplet(df, multivalue=multivalue)
        triplet = triplet.filter(pl.col("VALUE").is_not_null())
        all_triplets.append(triplet)
    if not all_triplets:
        return pl.DataFrame(schema={"ID": pl.Utf8, "KEY": pl.Utf8, "VALUE": pl.Utf8, "INSTANCE_ID": pl.Utf8})
    return pl.concat(all_triplets)


def tableview_to_triplet(data, multivalue=False):
    """Convert a table view back to triplet format."""
    # polars melt (unpivot)
    id_col = "ID" if "ID" in data.columns else data.columns[0]
    value_cols = [c for c in data.columns if c != id_col]
    triplet_df = data.unpivot(
        on=value_cols,
        index=id_col,
        variable_name="KEY",
        value_name="VALUE",
    )

    if multivalue:
        # Explode list values
        triplet_df = triplet_df.with_columns(
            pl.col("VALUE").map_elements(
                lambda v: v if not isinstance(v, str) or not v.startswith("[") else eval(v),
                return_dtype=pl.Object,
            )
        ).explode("VALUE")

    return triplet_df.cast({"VALUE": pl.Utf8, "KEY": pl.Utf8, "ID": pl.Utf8})


def update_triplet_from_triplet(data, update_data, update=True, add=True):
    """Update triplet data with values from another triplet dataset."""
    if update:
        # Anti-join to remove old values, then concat new ones
        data = data.join(
            update_data.select(["ID", "KEY"]),
            on=["ID", "KEY"],
            how="anti",
        )
    if add or update:
        data = pl.concat([data, update_data])
    return data


def update_triplet_from_tableview(data, tableview, update=True, add=True, instance_id=None):
    """Update triplet data from a tableview DataFrame."""
    triplet = tableview_to_triplet(tableview)
    if instance_id:
        triplet = triplet.with_columns(pl.lit(instance_id).alias("INSTANCE_ID"))
    return update_triplet_from_triplet(data, triplet, update=update, add=add)


def remove_triplet_from_triplet(from_triplet, what_triplet, columns=["ID", "KEY", "VALUE"]):
    """Remove rows from one triplet that match another (anti-join)."""
    return from_triplet.join(what_triplet.select(columns), on=columns, how="anti")


def diff_between_triplet(old_data, new_data):
    """Find differences between two triplet datasets."""
    # Rows in old but not in new
    removed = old_data.join(
        new_data.select(["ID", "KEY", "VALUE"]),
        on=["ID", "KEY", "VALUE"],
        how="anti",
    ).with_columns(pl.lit("-").alias("_merge"))

    # Rows in new but not in old
    added = new_data.join(
        old_data.select(["ID", "KEY", "VALUE"]),
        on=["ID", "KEY", "VALUE"],
        how="anti",
    ).with_columns(pl.lit("+").alias("_merge"))

    return pl.concat([removed, added])


def diff_between_INSTANCE(data, INSTANCE_ID_1, INSTANCE_ID_2):
    """Find differences between two instances in the same dataset."""
    data1 = data.filter(pl.col("INSTANCE_ID") == INSTANCE_ID_1)
    data2 = data.filter(pl.col("INSTANCE_ID") == INSTANCE_ID_2)
    return diff_between_triplet(data1, data2)


def print_triplet_diff(old_data, new_data, file_id_object="Distribution", file_id_key="label", exclude_objects=None):
    """Print a human-readable diff between two triplet datasets."""
    diff = diff_between_triplet(old_data, new_data)
    diff = diff.sort(["ID", "KEY"])

    # Remove file identification objects
    file_ids = filter_by_type(diff, file_id_object)
    if not file_ids.is_empty():
        diff = remove_triplet_from_triplet(diff, file_ids)

    # Exclude specified types
    if exclude_objects:
        for obj in exclude_objects:
            obj_data = filter_by_type(diff, obj)
            if not obj_data.is_empty():
                diff = remove_triplet_from_triplet(diff, obj_data)

    if diff.is_empty():
        print("No differences found")
        return

    # Print grouped by ID
    for id_val in diff["ID"].unique().to_list():
        id_diff = diff.filter(pl.col("ID") == id_val)
        print(f"\n{id_val}:")
        for row in id_diff.iter_rows(named=True):
            print(f"  {row['_merge']} {row['KEY']}: {row['VALUE']}")
