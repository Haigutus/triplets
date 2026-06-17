"""Polars-native implementation of triplet data manipulation tools.

Uses polars lazy evaluation, hash joins, and native operations for performance.
All functions accept and return polars DataFrames.
"""

import logging
import polars as pl

logger = logging.getLogger(__name__)


def _numeric_columns(data_view):
    """Cast columns that contain only numbers to Float64."""
    for col in data_view.columns:
        if col == "ID":
            continue
        try:
            data_view = data_view.with_columns(pl.col(col).cast(pl.Float64, strict=True))
        except (pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError):
            pass
    return data_view


def _tableview(ids, data, string_to_number, multivalue, label):
    """Shared pivot core for the three tableview functions.

    ids: polars DataFrame with an ID column selecting the objects.
    """
    if ids.is_empty():
        logger.warning(f'No data available for {label}')
        return None

    object_data = ids.select("ID").unique().join(data, on="ID", how="inner")

    if multivalue:
        # Aggregate all values per (ID, KEY) into lists, then pivot.
        agg = object_data.group_by(["ID", "KEY"]).agg(pl.col("VALUE").alias("VALUE"))
        data_view = agg.pivot(on="KEY", index="ID", values="VALUE")
        # Unwrap single-element lists to scalars for cleaner output.
        # Multi-element lists stay joined (matching pandas multivalue behavior).
        for col in data_view.columns:
            if col == "ID":
                continue
            dtype = data_view[col].dtype
            if dtype == pl.List(pl.Utf8) or dtype == pl.List(pl.String):
                data_view = data_view.with_columns(
                    pl.when(pl.col(col).list.len() == 1)
                    .then(pl.col(col).list.first())
                    .otherwise(pl.col(col).list.join(", "))
                    .alias(col)
                )
    else:
        data_view = object_data.unique(subset=["ID", "KEY"], keep="first").pivot(on="KEY", index="ID", values="VALUE")

    return _numeric_columns(data_view) if string_to_number else data_view


def type_tableview(data, type_name, string_to_number=True, type_key="Type", multivalue=False):
    """Create a table view of all objects of a specified type using polars pivot."""
    ids = data.filter((pl.col("VALUE") == type_name) & (pl.col("KEY") == type_key))
    return _tableview(ids, data, string_to_number, multivalue, type_name)


def key_tableview(data, key, string_to_number=True, multivalue=False):
    """Create a table view of all objects with a specified key."""
    ids = data.filter(pl.col("KEY") == key)
    return _tableview(ids, data, string_to_number, multivalue, key)


def id_tableview(data, id, string_to_number=True, multivalue=False):
    """Create a table view of objects by ID (single ID, list of IDs, or DataFrame with ID column)."""
    if isinstance(id, str):
        id = [id]
    if isinstance(id, list):
        id = pl.DataFrame({"ID": id})

    ids = data.join(id.select("ID"), on="ID", how="semi")
    return _tableview(ids, data, string_to_number, multivalue, id["ID"].to_list())


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
    """Extract namespace map from triplet data.

    Returns ``(namespace_map dict, xml_base)`` — same contract as the pandas engine.
    """
    nsmap_ids = data.filter(
        (pl.col("KEY") == "Type") & (pl.col("VALUE") == "NamespaceMap")
    ).select("ID")
    if nsmap_ids.is_empty():
        return {}, ""
    nsmap_data = nsmap_ids.join(data, on="ID", how="inner").filter(pl.col("KEY") != "Type")
    namespace_map = dict(zip(nsmap_data["KEY"].cast(pl.Utf8).to_list(),
                             nsmap_data["VALUE"].cast(pl.Utf8).to_list()))
    xml_base = namespace_map.pop("xml_base", "")
    return namespace_map, xml_base


def _u(data):
    """Cast the triplet columns to Utf8 (avoids Categorical join/is_in pitfalls)."""
    cols = [c for c in ("ID", "KEY", "VALUE", "INSTANCE_ID") if c in data.columns]
    return data.with_columns([pl.col(c).cast(pl.Utf8) for c in cols])


def references_to(data, reference, levels=1):
    """Objects that reference `reference`, traversing up to `levels`. Matches the
    pandas engine: level 0 is the object itself; level N rows carry level/ID_TO/ID_FROM."""
    data = _u(data)
    base = data.filter(pl.col("ID") == reference).with_columns(pl.lit(0, dtype=pl.Int64).alias("level"))
    parts, frontier = [base], base
    for level in range(1, levels + 1):
        to_ids = frontier.select("ID").unique().to_series().to_list()
        links = (data.filter(pl.col("VALUE").is_in(to_ids))
                 .select(pl.col("ID").alias("ID_FROM"), pl.col("VALUE").alias("ID_TO"))
                 .unique(subset="ID_FROM", keep="first"))
        if links.is_empty():
            break
        objs = (data.join(links, left_on="ID", right_on="ID_FROM", how="inner")
                .with_columns(pl.col("ID").alias("ID_FROM"), pl.lit(level, dtype=pl.Int64).alias("level")))
        parts.append(objs)
        frontier = objs
    return pl.concat(parts, how="diagonal_relaxed")


def references_from(data, reference, levels=1):
    """Objects referenced BY `reference`, traversing up to `levels`. Matches pandas:
    level 0 is the object itself; level N rows carry level/ID_TO/ID_FROM."""
    data = _u(data)
    base = data.filter(pl.col("ID") == reference).with_columns(pl.lit(0, dtype=pl.Int64).alias("level"))
    parts, frontier = [base], base
    for level in range(1, levels + 1):
        edges = frontier.select(pl.col("ID").alias("ID_FROM"), pl.col("VALUE").alias("ID_TO"))
        objs = (edges.join(data, left_on="ID_TO", right_on="ID", how="inner")
                .with_columns(pl.col("ID_TO").alias("ID"), pl.lit(level, dtype=pl.Int64).alias("level")))
        if objs.is_empty():
            break
        parts.append(objs)
        frontier = objs
    return pl.concat(parts, how="diagonal_relaxed")


def references(data, ID, levels=1):
    """All references to and from an object (both directions), matching pandas."""
    FROM = references_from(data, ID, levels)
    TO = references_to(data, ID, levels)
    return pl.concat([FROM, TO], how="diagonal_relaxed").unique(
        subset=["ID", "KEY", "VALUE", "INSTANCE_ID"], keep="first", maintain_order=True)


def references_all(data):
    """All reference links as (ID_FROM, KEY, ID_TO), matching pandas."""
    data = _u(data)
    triples = data.select("ID", "KEY", "VALUE").unique()
    ids = data.select("ID").unique().to_series().to_list()
    return (triples.filter(pl.col("VALUE").is_in(ids))
            .select(pl.col("ID").alias("ID_FROM"), pl.col("KEY"), pl.col("VALUE").alias("ID_TO")))


def references_to_simple(data, reference, columns=["Type"]):
    """Pivot of objects referencing `reference` (index ID_FROM), limited to `columns`."""
    refs = references_to(data, reference, levels=1).unique(subset=["ID_FROM", "KEY"], keep="first")
    view = refs.pivot(on="KEY", index="ID_FROM", values="VALUE")
    keep = [c for c in (columns or []) if c in view.columns]
    return view.select(["ID_FROM", *keep])


def references_from_simple(data, reference, columns=["Type"]):
    """Pivot of objects referenced BY `reference` (index ID_TO), limited to `columns`."""
    refs = references_from(data, reference, levels=1).unique(subset=["ID_TO", "KEY"], keep="first")
    view = refs.pivot(on="KEY", index="ID_TO", values="VALUE")
    keep = [c for c in (columns or []) if c in view.columns]
    return view.select(["ID_TO", *keep])


def references_simple(data, reference, columns=None, levels=1):
    """Pivot of the object and everything linked to/from it (index ID), with the
    level/ID_FROM/ID_TO metadata merged back, matching pandas."""
    ref = references(data, reference, levels=levels).unique(subset=["ID", "KEY"], keep="first")
    view = ref.select("ID", "KEY", "VALUE").pivot(on="KEY", index="ID", values="VALUE")
    if not columns:
        columns = [c for c in ("Type", "IdentifiedObject.name") if c in view.columns]
    keep = [c for c in columns if c in view.columns]
    meta = ref.select("ID", "level", "ID_FROM", "ID_TO")
    return (view.select(["ID", *keep])
            .join(meta, on="ID", how="left")
            .unique(subset="ID", keep="first", maintain_order=True)
            .sort("level"))


def filter_triplets_by_type(data, type_name, type_key="Type"):
    """Filter triplet data to only include objects of a specific type."""
    type_ids = data.filter(
        (pl.col("KEY") == type_key) & (pl.col("VALUE") == type_name)
    ).select("ID")
    return type_ids.join(data, on="ID", how="inner")


def filter_triplets_by_triplets(data, filter_triplet):
    """Return all triplets whose ID appears in filter_triplet (matches pandas: merge on ID)."""
    return filter_triplet.select("ID").unique().join(data, on="ID", how="inner")


def filter_triplets(data, ID=None, KEY=None, VALUE=None, INSTANCE_ID=None, regex=False):
    """Filter triplets by any combination of columns with optional regex.

    Parameters
    ----------
    data : polars.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    ID, KEY, VALUE, INSTANCE_ID : str, optional
        Filter value. If regex=True, treated as regex pattern.
    regex : bool, default False
        If True, use regex matching (str.contains). If False, exact match.

    Returns
    -------
    polars.DataFrame
        Filtered triplet dataset.
    """
    expr = pl.lit(True)
    for col, val in [("ID", ID), ("KEY", KEY), ("VALUE", VALUE), ("INSTANCE_ID", INSTANCE_ID)]:
        if val is not None:
            if regex:
                expr = expr & pl.col(col).cast(pl.Utf8).str.contains(val)
            else:
                expr = expr & (pl.col(col).cast(pl.Utf8) == val)
    return data.filter(expr)


def _value_literal(value):
    """VALUE entries are strings or null — never the string \"None\" or raw numbers."""
    return pl.lit(None, dtype=pl.Utf8) if value is None else pl.lit(str(value))


def set_value_at_key(data, key, value):
    """Set VALUE for all rows with a given KEY (in-place mutation via reassignment)."""
    return data.with_columns(
        pl.when(pl.col("KEY") == key)
        .then(_value_literal(value))
        .otherwise(pl.col("VALUE"))
        .alias("VALUE")
    )


def set_value_at_key_and_id(data, key, value, id):
    """Set VALUE for a specific KEY and ID combination."""
    return data.with_columns(
        pl.when((pl.col("KEY") == key) & (pl.col("ID") == id))
        .then(_value_literal(value))
        .otherwise(pl.col("VALUE"))
        .alias("VALUE")
    )


def triplets_to_tableviews(triplet_df, multivalue=False):
    """Convert triplet DataFrame to dict of tableview DataFrames."""
    td = types_dict(triplet_df)
    tableviews = {}
    for class_name in td:
        tv = type_tableview(triplet_df, class_name, multivalue=multivalue)
        if tv is not None:
            tableviews[class_name] = tv
    return tableviews


def _tableviews_to_triplets(tableviews, multivalue=False):
    """Convert dict of tableview DataFrames to triplet DataFrame."""
    all_triplets = []
    for class_name, df in tableviews.items():
        if "Type" not in df.columns:
            df = df.with_columns(pl.lit(class_name).alias("Type"))
        triplet = tableview_to_triplets(df, multivalue=multivalue)
        triplet = triplet.filter(pl.col("VALUE").is_not_null())
        all_triplets.append(triplet)
    if not all_triplets:
        return pl.DataFrame(schema={"ID": pl.Utf8, "KEY": pl.Utf8, "VALUE": pl.Utf8, "INSTANCE_ID": pl.Utf8})
    return pl.concat(all_triplets)


def tableview_to_triplets(data, multivalue=False):
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


def update_triplets_from_triplets(data, update_data, update=True, add=True):
    """Update existing and/or add new rows from another triplet dataset.

    Merge keys are ID+KEY (plus INSTANCE_ID when update_data carries it), matching
    the pandas engine. Join keys are cast to Utf8 so a Categorical KEY in `data`
    joins cleanly with a string KEY in `update_data`.
    """
    merge_cols = ["ID", "KEY"]
    if "INSTANCE_ID" in update_data.columns:
        merge_cols = ["ID", "KEY", "INSTANCE_ID"]
    data = data.with_columns([pl.col(c).cast(pl.Utf8) for c in merge_cols])
    update_data = update_data.with_columns([pl.col(c).cast(pl.Utf8) for c in merge_cols])

    if update:
        # overwrite only VALUE on matched (ID,KEY[,INSTANCE_ID]); keep other columns
        # (e.g. INSTANCE_ID) of the original row, matching the pandas engine.
        new_value = update_data.select(merge_cols + ["VALUE"]).rename({"VALUE": "_new_value"})
        data = (data.join(new_value, on=merge_cols, how="left")
                .with_columns(pl.coalesce(["_new_value", "VALUE"]).alias("VALUE"))
                .drop("_new_value"))
    if add:
        new = update_data.join(data.select(merge_cols), on=merge_cols, how="anti")
        data = pl.concat([data, new], how="diagonal_relaxed")
    return data


def update_triplets_from_tableview(data, tableview, update=True, add=True, instance_id=None):
    """Update triplet data from a tableview DataFrame."""
    triplet = tableview_to_triplets(tableview)
    if instance_id:
        triplet = triplet.with_columns(pl.lit(instance_id).alias("INSTANCE_ID"))
    return update_triplets_from_triplets(data, triplet, update=update, add=add)


def remove_triplets_from_triplets(from_triplet, what_triplet, columns=["ID", "KEY", "VALUE"]):
    """Remove rows from one triplet that match another (anti-join)."""
    return from_triplet.join(what_triplet.select(columns), on=columns, how="anti")


def diff_triplets(old_data, new_data):
    """Rows unique to old (left_only) or new (right_only), matching the pandas
    outer-merge shape: columns [ID, KEY, VALUE, INSTANCE_ID_OLD, INSTANCE_ID_NEW, _merge]."""
    old = old_data.with_columns(pl.lit(True).alias("_in_old"))
    new = new_data.with_columns(pl.lit(True).alias("_in_new"))
    merged = old.join(new, on=["ID", "KEY", "VALUE"], how="full", suffix="_NEW", coalesce=True)
    merged = merged.with_columns(
        pl.when(pl.col("_in_old").is_null()).then(pl.lit("right_only"))
        .when(pl.col("_in_new").is_null()).then(pl.lit("left_only"))
        .otherwise(pl.lit("both")).alias("_merge")
    ).filter(pl.col("_merge") != "both")
    merged = merged.rename({"INSTANCE_ID": "INSTANCE_ID_OLD"})
    return merged.select(["ID", "KEY", "VALUE", "INSTANCE_ID_OLD", "INSTANCE_ID_NEW", "_merge"])


def diff_triplets_by_instance(data, INSTANCE_ID_1, INSTANCE_ID_2):
    """Triplets that differ between two instances in the dataset (symmetric difference
    on ID/KEY/VALUE), matching pandas drop_duplicates(keep=False)."""
    scope = data.filter(pl.col("INSTANCE_ID").is_in([INSTANCE_ID_1, INSTANCE_ID_2]))
    return scope.filter(pl.len().over(["ID", "KEY", "VALUE"]) == 1)


def print_triplets_diff(old_data, new_data, file_id_object="Distribution", file_id_key="label", exclude_objects=None):
    """Print a human-readable diff between two triplet datasets."""
    diff = diff_triplets(old_data, new_data)
    diff = diff.sort(["ID", "KEY"])

    # Remove file identification objects
    file_ids = filter_triplets_by_type(diff, file_id_object)
    if not file_ids.is_empty():
        diff = remove_triplets_from_triplets(diff, file_ids)

    # Exclude specified types
    if exclude_objects:
        for obj in exclude_objects:
            obj_data = filter_triplets_by_type(diff, obj)
            if not obj_data.is_empty():
                diff = remove_triplets_from_triplets(diff, obj_data)

    if diff.is_empty():
        print("No differences found")
        return

    # Print grouped by ID
    for id_val in diff["ID"].unique().to_list():
        id_diff = diff.filter(pl.col("ID") == id_val)
        print(f"\n{id_val}:")
        for row in id_diff.iter_rows(named=True):
            print(f"  {row['_merge']} {row['KEY']}: {row['VALUE']}")
