# -------------------------------------------------------------------------------
# Name:        pandas_engine
# Purpose:     Pandas-based query, filter, diff, transform and mutate tools
#              for triplet (ID, KEY, VALUE, INSTANCE_ID) DataFrames.
#
# Extracted from rdf_parser.py
#
# Author:      kristjan.vilgo
#
# Copyright:   (c) kristjan.vilgo 2018
# Licence:     MIT
# -------------------------------------------------------------------------------

import logging

import pandas

logger = logging.getLogger(__name__)


def get_namespace_map(data: pandas.DataFrame):
    """
    Extract namespace prefix-to-URI mapping and optional xml:base from a triplet dataset.

    This function searches for a `NamespaceMap` object (identified by ``KEY='Type'`` and ``VALUE='NamespaceMap'``)
    within the dataset. It then collects all key-value pairs under that instance where:
    - ``KEY`` is the namespace prefix (e.g., "cim", "rdf")
    - ``VALUE`` is the full URI (e.g., "http://iec.ch/TC57/2013/CIM-schema-cim16#")

    Special keys:
    - ``xml_base``: Extracted separately if present (used as base URI in RDF).
    - ``Type``: Automatically excluded.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset with columns ['INSTANCE_ID', 'ID', 'KEY', 'VALUE'].
        Must contain a `NamespaceMap` instance for successful extraction.

    Returns
    -------
    namespace_map : dict
        Mapping of namespace prefixes to URIs (e.g., ``{"cim": "...", "rdf": "..."}``).
        **Empty dict** if no `NamespaceMap` is found.
    xml_base : str
        Value of ``xml_base`` if defined within the `NamespaceMap`; otherwise ``empty str``.

    Examples
    --------
    >>> ns_map, base = get_namespace_map(triplet_data)
    >>> print(ns_map)
    {'cim': 'http://iec.ch/TC57/2013/CIM-schema-cim16#', 'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'}
    >>> print(base)
    'http://example.com/base/'

    >>> ns_map, base = get_namespace_map(empty_data)
    >>> print(ns_map, base)
    {} ""

    Notes
    -----
    - The function is **idempotent** and safe to call on any dataset.
    - Uses inner merge on ``ID`` to scope entries to the correct `NamespaceMap` instance.
    - Always returns a tuple of length 2: ``(dict, str)``.
    """
    namespace_map_data = data.merge(data.query("KEY == 'Type' and VALUE == 'NamespaceMap'").ID)

    if namespace_map_data.empty:
        return {}, ""

    namespace_map = namespace_map_data.set_index("KEY")["VALUE"].to_dict()
    namespace_map.pop("Type", None)
    xml_base = namespace_map.pop("xml_base", None)
    return namespace_map, xml_base


def _numeric_columns(data_view):
    """Convert columns that contain only numbers to numeric dtypes."""
    for column in data_view.columns:
        try:
            data_view[column] = pandas.to_numeric(data_view[column], errors="raise")
        except (ValueError, TypeError):
            pass
    return data_view


def _tableview(rows, data, string_to_number, multivalue, label):
    """Shared pivot core for the three tableview functions.

    rows: triplet rows whose IDs select the objects; data: full dataset
    providing all triplets of the selected objects.
    """
    if rows.empty:
        logger.warning(f'No data available for {label}')
        return None

    object_data = pandas.merge(rows[["ID"]].drop_duplicates(), data, on="ID")

    if multivalue:
        def _aggregate(x):
            x_list = list(x)
            if len(x_list) == 1:
                return x_list[0]
            return x_list

        data_view = object_data.pivot_table(index="ID", columns="KEY", values="VALUE", aggfunc=_aggregate)
    else:
        data_view = object_data.drop_duplicates(["ID", "KEY"]).pivot(index="ID", columns="KEY")["VALUE"]

    return _numeric_columns(data_view) if string_to_number else data_view


def type_tableview(data, type_name, string_to_number=True, type_key="Type", multivalue=False):
    """Create a table view of all objects of a specified type.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    type_name : str
        The type of objects to filter (e.g., 'ACLineSegment').
    string_to_number : bool, optional
        If True, convert columns containing numbers to numeric types (default is True).
    type_key : str, optional
        Key used to identify object types in the dataset (default is 'Type').
    multivalue : bool, optional
        If True, aggregate duplicate (ID, KEY) pairs into lists (default is False).

    Returns
    -------
    pandas.DataFrame or None
        Pivoted DataFrame with IDs as index and keys as columns, or None if no data is found.

    Examples
    --------
    >>> table = data.type_tableview("ACLineSegment", multivalue=True)
    """
    rows = data[(data["VALUE"] == type_name) & (data["KEY"] == type_key)]
    return _tableview(rows, data, string_to_number, multivalue, type_name)


def key_tableview(data, key, string_to_number=True, multivalue=False):
    """Create a table view of all objects with a specified key.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    key : str
        The key to filter objects by (e.g., 'GeneratingUnit.maxOperatingP').
    string_to_number : bool, optional
        If True, convert columns containing numbers to numeric types (default is True).
    multivalue : bool, optional
        If True, aggregate duplicate (ID, KEY) pairs into lists (default is False).

    Returns
    -------
    pandas.DataFrame or None
        Pivoted DataFrame with IDs as index and keys as columns, or None if no data is found.

    Examples
    --------
    >>> table = data.key_tableview("GeneratingUnit.maxOperatingP")
    """
    rows = data[data["KEY"] == key]
    return _tableview(rows, data, string_to_number, multivalue, key)


def id_tableview(data, id, string_to_number=True, multivalue=False):
    """Create a tabular view of a CGMES triplet dataset filtered by ID-s.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    id : str or list or pandas.DataFrame
        ID(s) to filter by (single ID, list of IDs, or DataFrame with an ID column).
    string_to_number : bool, optional
        If True, convert columns containing numbers to numeric types (default is True).
    multivalue : bool, optional
        If True, aggregate duplicate (ID, KEY) pairs into lists (default is False).

    Returns
    -------
    pandas.DataFrame or None
        Pivoted DataFrame with IDs as index and KEYs as columns.

    Examples
    --------
    >>> table = id_tableview(data, 'UUID')
    >>> table = id_tableview(data, ['UUID_1', 'UUID_2'])
    >>> table = id_tableview(data, pandas.DataFrame({"ID": ['UUID_1', 'UUID_2']}))
    """
    if isinstance(id, str):
        id = [id]
    if isinstance(id, list):
        id = pandas.DataFrame({"ID": id})

    rows = data[data["ID"].isin(id["ID"])]
    return _tableview(rows, data, string_to_number, multivalue, list(id["ID"]))


def references_to_simple(data, reference, columns=["Type"]):
    """Create a simplified table view of objects referencing a specified object.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the object to find references to.
    columns : list, optional
        Columns to include in the output table (default is ['Type']).

    Returns
    -------
    pandas.DataFrame
        Pivoted DataFrame with IDs of referencing objects and specified columns.

    Examples
    --------
    >>> table = data.references_to_simple("99722373_VL_TN1")
    """
    reference_data = references_to(data, reference, levels=1).drop_duplicates(["ID_FROM", "KEY"])

    # Convert form triplets to a table view with columns - ID, Type by default
    data_view = reference_data.pivot(index="ID_FROM", columns="KEY")["VALUE"][columns]

    return data_view


def references_to(data, reference, levels=1):
    """Retrieve all objects pointing to a specified reference object.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the reference object.
    levels : int, optional
        Number of reference levels to traverse (default is 1).

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets of objects pointing to the reference, with a 'level' column.

    Notes
    -----
    - TODO: Add the key on which the connection was made.

    Examples
    --------
    >>> refs = data.references_to("99722373_VL_TN1", levels=2)
    """
    # TODO - add the key on which connection was made
    level = 0

    # Get the object itself
    object_data = data.query(f"ID == '{reference}'").copy()
    object_data["level"] = level
    # object_data["ID_TO"] = reference
    # object_data["ID_FROM"] = reference

    # Add object to processing list
    objects_list = [object_data]

    for object_data in objects_list:

        level += 1

        # End loop if we have reached desired level
        if level > levels:
            break

        # Get column where possible reference to other objects reside
        reference_column = object_data[["ID"]]

        # Filter original data VALUE-s by found references ID-s
        reference_data = pandas.merge(reference_column, data,
                                      left_on="ID",
                                      right_on="VALUE",
                                      suffixes=("_TO", "_FROM"))[["ID_TO", "ID_FROM"]].drop_duplicates("ID_FROM")

        if not reference_data.empty:
            referring_objects = pandas.merge(reference_data, data,
                                             left_on="ID_FROM",
                                             right_on="ID")  # .drop(columns=["ID_FROM"])

            # Set object level
            referring_objects["level"] = level

            # Add data for future processing
            objects_list.append(referring_objects)

    return pandas.concat(objects_list)


def references_from_simple(data, reference, columns=["Type"]):
    """Create a simplified table view of objects a specified object refers to.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the object to find references from.
    columns : list, optional
        Columns to include in the output table (default is ['Type']).

    Returns
    -------
    pandas.DataFrame
        Pivoted DataFrame with IDs of referenced objects and specified columns.

    Examples
    --------
    >>> table = data.references_from_simple("99722373_VL_TN1")
    """
    reference_data = references_from(data, reference, levels=1).drop_duplicates(["ID_TO", "KEY"])

    # Convert form triplets to a table view with columns - ID, Type by default
    data_view = reference_data.pivot(index="ID_TO", columns="KEY")["VALUE"][columns]

    return data_view


def references_from(data, reference, levels=1):
    """Retrieve all objects a specified object points to.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the reference object.
    levels : int, optional
        Number of reference levels to traverse (default is 1).

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets of objects referenced by the input, with a 'level' column.

    Notes
    -----
    - TODO: Add the key on which the connection was made.

    Examples
    --------
    >>> refs = data.references_from("99722373_VL_TN1", levels=2)
    """
    # TODO - add the key on which connection was made
    level = 0

    # Get the object itself
    object_data = data.query(f"ID == '{reference}'").copy()
    object_data["level"] = level
    #object_data["ID_TO"] = reference
    #object_data["ID_FROM"] = reference

    # Add object to processing list
    objects_list = [object_data]

    for object_data in objects_list:

        level += 1

        # End loop if we have reached desired level
        if level > levels:
            break

        # Get column where possible reference to other objects reside
        reference_column = object_data[["ID", "VALUE"]]

        # Filter original data ID-s by values form reference object
        reference_data = pandas.merge(reference_column, data,
                                      left_on="VALUE",
                                      right_on="ID",
                                      suffixes=("_FROM", "")).rename(columns={"VALUE_FROM": "ID_TO"})

        if not reference_data.empty:

            # Set object level
            reference_data["level"] = level

            # Add data for future processing
            objects_list.append(reference_data)

    return pandas.concat(objects_list)


def references_all(data):
    """Find all unique references (links) in the dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.

    Returns
    -------
    pandas.DataFrame
        DataFrame with columns ['ID_FROM', 'KEY', 'ID_TO'] representing all references.

    Notes
    -----
    - Does not consider INSTANCE_ID in reference matching.

    Examples
    --------
    >>> refs = data.references_all()
    """
    return data[["ID", "KEY", "VALUE"]].drop_duplicates().merge(data[["ID"]].drop_duplicates(), left_on="VALUE", right_on="ID", suffixes=("_FROM", "_TO"))[["ID_FROM", "KEY", "ID_TO"]]


def references_simple(data, reference, columns=None, levels=1):
    """Create a simplified table view of all references to and from a specified object.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    reference : str
        ID of the object to find references for.
    columns : list, optional
        Columns to include in the output table (default is ['Type', 'IdentifiedObject.name'] if available).
    levels : int, optional
        Number of reference levels to traverse (default is 1).

    Returns
    -------
    pandas.DataFrame
        Pivoted DataFrame with IDs, specified columns, and reference levels.

    Examples
    --------
    >>> table = data.references_simple("99722373_VL_TN1", columns=["Type"])
    """
    reference_data = references(data, reference, levels=levels).drop_duplicates(["ID", "KEY"])

    # Convert form triplets to a table view with columns - ID, Type by default
    data_view = reference_data[["ID", "KEY", "VALUE"]].pivot(index="ID", columns="KEY")["VALUE"]

    if not columns:
        columns = []
        available_columns = data_view.columns
        if "Type" in available_columns:
            columns.append("Type")

        if "IdentifiedObject.name" in available_columns:
            columns.append("IdentifiedObject.name")

    return data_view[columns].merge(reference_data[["ID", "level", "ID_FROM", "ID_TO"]], on="ID", how="left").drop_duplicates("ID").sort_values("level")


def references(data, ID, levels=1):
    """Retrieve all references (to and from) a specified object.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    ID : str
        ID of the object to find references for.
    levels : int, optional
        Number of reference levels to traverse (default is 1).

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets of all references to and from the object.

    Examples
    --------
    >>> refs = data.references("99722373_VL_TN1", levels=2)
    """
    FROM = references_from(data, ID, levels)
    TO = references_to(data, ID, levels)
    return pandas.concat([FROM, TO]).drop_duplicates(["ID", "KEY", "VALUE", "INSTANCE_ID"])


def types_dict(data):
    """Return a dictionary of object types and their occurrence counts.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.

    Returns
    -------
    dict
        Dictionary with object types as keys and their counts as values.

    Examples
    --------
    >>> types = data.types_dict()
    >>> print(types)
    {'ACLineSegment': 10, 'PowerTransformer': 5, ...}
    """
    types_dictionary = data[(data.KEY == "Type")]["VALUE"].value_counts().to_dict()

    return types_dictionary


def _string_or_none(value):
    """VALUE entries are strings or null — never the string "None" or raw numbers."""
    return None if value is None else str(value)


def set_value_at_key(data, key, value):
    """Set the value for all instances of a specified key.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    key : str
        The key to update.
    value : str
        The new value to set for the specified key.

    Notes
    -----
    - TODO: Add debug logging for key, initial value, and new value.
    - TODO: Store changes in a changes DataFrame.

    Examples
    --------
    >>> data.set_value_at_key("label", "new_label")
    """
    data.loc[data[data.KEY == key].index, "VALUE"] = _string_or_none(value)  # TODO add changes to change DataFrame


def set_value_at_key_and_id(data, key, value, id):
    """Set the value for a specific key and ID.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    key : str
        The key to update.
    value : str
        The new value to set.
    id : str
        The ID of the object to update.

    Examples
    --------
    >>> data.set_value_at_key_and_id("label", "new_label", "uuid1")
    """
    data.loc[data[(data.ID == id) & (data.KEY == key)].index, "VALUE"] = _string_or_none(value)


def triplets_to_tableviews(triplet_df, multivalue=False):
    """Convert triplet DataFrame to dict of tableview DataFrames.

    Parameters
    ----------
    triplet_df : pandas.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    multivalue : bool, default False
        If True, aggregate duplicate (ID, KEY) pairs into lists.

    Returns
    -------
    dict
        {class_name: tableview_df}
    """
    types = types_dict(triplet_df)
    tableviews = {}
    for class_name in types:
        table_view = type_tableview(triplet_df, class_name, multivalue=multivalue)
        if table_view is not None:
            tableviews[class_name] = table_view
    return tableviews


def _tableviews_to_triplets(tableviews, multivalue=False):
    """Convert dict of tableview DataFrames to triplet DataFrame.

    Parameters
    ----------
    tableviews : dict
        {class_name: tableview_df}
    multivalue : bool, default False
        If True, unpack list values into separate triplets.

    Returns
    -------
    pandas.DataFrame
        Triplet DataFrame with columns [ID, KEY, VALUE, INSTANCE_ID].
    """
    all_triplets = []
    for class_name, df in tableviews.items():
        if 'Type' not in df.columns:
            df = df.assign(Type=class_name)
        triplet = tableview_to_triplets(df, multivalue=multivalue)
        triplet = triplet[triplet['VALUE'].notna()]
        all_triplets.append(triplet)
    if not all_triplets:
        return pandas.DataFrame(columns=['ID', 'KEY', 'VALUE', 'INSTANCE_ID'])
    return pandas.concat(all_triplets, ignore_index=True)


def get_object_data(data, object_UUID):
    """Retrieve data for a specific object by its UUID.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    object_UUID : str
        UUID of the object to retrieve.

    Returns
    -------
    pandas.Series
        Series with keys as index and values for the specified object.

    Examples
    --------
    >>> obj_data = data.get_object_data("uuid1")
    """
    return data.query("ID == '{}'".format(object_UUID)).set_index("KEY")["VALUE"]


def tableview_to_triplets(data, multivalue=False):
    """Convert a table view back to a triplet format.

    Parameters
    ----------
    data : pandas.DataFrame
        Pivoted DataFrame (table view) to convert.
    multivalue : bool, optional
        If True, unpack list values into separate triplets (default is False).

    Returns
    -------
    pandas.DataFrame
        Triplet DataFrame with columns ['ID', 'KEY', 'VALUE'].
    """
    triplet_df = data.reset_index().melt(id_vars="ID", value_name="VALUE", var_name="KEY")

    if multivalue:
        import ast
        def _ensure_list(val):
            if isinstance(val, str) and val.startswith("[") and val.endswith("]"):
                try:
                    parsed = ast.literal_eval(val)
                    if isinstance(parsed, list):
                        return parsed
                except (ValueError, SyntaxError):
                    pass
            return val

        triplet_df["VALUE"] = triplet_df["VALUE"].apply(_ensure_list)
        triplet_df = triplet_df.explode("VALUE")

    # nullable string dtype: numbers become text, melt's NaN holes stay null
    # (plain astype(str) made them literal "nan" strings / mixed nan objects)
    return triplet_df.astype("string")


def update_triplets_from_triplets(data, update_data, update=True, add=True):
    """Update or add triplets from another triplet dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Original triplet dataset to update.
    update_data : pandas.DataFrame
        Triplet dataset containing updates or new data.
    update : bool, optional
        If True, update existing ID-KEY pairs (default is True).
    add : bool, optional
        If True, add new ID-KEY pairs (default is True).

    Returns
    -------
    pandas.DataFrame
        Updated triplet dataset.

    Notes
    -----
    - TODO: Add a changes DataFrame to track modifications.
    - TODO: Support updating ID and KEY fields.

    Examples
    --------
    >>> updated_data = data.update_triplets_from_triplets(update_data)
    """
    write_columns = ["ID", "KEY", "VALUE", "INSTANCE_ID"]

    # Choose what columns to use for final merge
    merge_columns = ["ID", "KEY"]
    if "INSTANCE_ID" in update_data.columns:
        merge_columns = ["ID", "KEY", "INSTANCE_ID"]

    # First reset index to be sure that data does not have duplicated keys
    data = data.reset_index(drop=True)

    # Make merge to see what updated data already exists in old and what needs to be added
    changes = data.reset_index(names="original_index").merge(update_data, on=merge_columns, how='right', indicator=True, suffixes=("_OLD", ""), sort=False)

    if update:
        # Filter data that needs to be updated
        data_to_update = changes.query("_merge == 'both'").drop_duplicates(subset="original_index")
        data.iloc[data_to_update["original_index"].astype(int)] = data_to_update[write_columns]

    if add:
        # Filter data that needs to be added
        data_to_add = changes.query("_merge == 'right_only'")[write_columns]
        data = pandas.concat([data, data_to_add]).drop_duplicates(keep='last', ignore_index=True)

    return data


def update_triplets_from_tableview(data, tableview, update=True, add=True, instance_id=None):
    """Update or add triplets from a table view.

    Parameters
    ----------
    data : pandas.DataFrame
        Original triplet dataset to update.
    tableview : pandas.DataFrame
        Table view containing updates or new data.
    update : bool, optional
        If True, update existing ID-KEY pairs (default is True).
    add : bool, optional
        If True, add new ID-KEY pairs (default is True).
    instance_id : str, optional
        Instance ID to assign to new triplets (default is None).

    Returns
    -------
    pandas.DataFrame
        Updated triplet dataset.

    Examples
    --------
    >>> updated_data = data.update_triplets_from_tableview(table_view, instance_id="uuid1")
    """
    update_triplet = tableview_to_triplets(tableview)

    if instance_id:
        update_triplet["INSTANCE_ID"] = instance_id

    return update_triplets_from_triplets(data, update_triplet, update, add)


def remove_triplets_from_triplets(from_triplet, what_triplet, columns=["ID", "KEY", "VALUE"]):
    """Remove triplets from one dataset that match another.

    Parameters
    ----------
    from_triplet : pandas.DataFrame
        Original triplet dataset.
    what_triplet : pandas.DataFrame
        Triplet dataset to remove from the original.
    columns : list, optional
        Columns to match for removal (default is ['ID', 'KEY', 'VALUE']).

    Returns
    -------
    pandas.DataFrame
        Dataset with matching triplets removed.

    Examples
    --------
    >>> result = remove_triplets_from_triplets(data, to_remove)
    """
    return from_triplet.drop(from_triplet.reset_index().merge(what_triplet[columns], on=columns, how="inner")["index"], axis=0)


def filter_triplets_by_triplets(data, filter_triplet):
    """Filter riplet DataFrame using IDs from another DataFrame.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    filter_triplet : pandas.DataFrame
        DataFrame containing atleast colum ID to filter by.

    Returns
    -------
    pandas.DataFrame
        Filtered DataFrame with columns ['ID, 'KEY', 'VALUE', 'INSTANCE_ID'].

    Examples
    --------
    >>> filtered = filter_triplets_by_triplets(data, filter_triplet)
    """

    return data.merge(filter_triplet[["ID"]], on="ID", how="inner")


def filter_triplets_by_type(data, type_name, type_key="Type"):
    """Filter triplet dataset by objects of a specific type.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    type_name : str
        Object type to filter by (e.g., 'ACLineSegment').
    type_key : str
        Key used in triplet to indicate type, by default "Type"

    Returns
    -------
    pandas.DataFrame
        Filtered triplet dataset containing only objects of the specified type.

    Examples
    --------
    >>> filtered = filter_triplets_by_type(data, "ACLineSegment")
    """
    filter_triplet = data[(data.KEY == type_key) & (data.VALUE == type_name)]

    return filter_triplets_by_triplets(data, filter_triplet)


def filter_triplets(data, ID=None, KEY=None, VALUE=None, INSTANCE_ID=None, regex=False):
    """Filter triplets by any combination of columns with optional regex.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    ID, KEY, VALUE, INSTANCE_ID : str, optional
        Filter value. If regex=True, treated as regex pattern.
    regex : bool, default False
        If True, use regex matching (re.search). If False, exact match.

    Returns
    -------
    pandas.DataFrame
        Filtered triplet dataset.

    Examples
    --------
    >>> filter_triplets(data, KEY="Type", VALUE="ACLineSegment")
    >>> filter_triplets(data, VALUE=".*Substation.*", regex=True)
    """
    mask = pandas.Series(True, index=data.index)
    for col, val in [("ID", ID), ("KEY", KEY), ("VALUE", VALUE), ("INSTANCE_ID", INSTANCE_ID)]:
        if val is not None:
            if regex:
                mask = mask & data[col].astype(str).str.contains(val, regex=True, na=False)
            else:
                mask = mask & (data[col].astype(str) == val)
    return data[mask]


def diff_triplets(old_data, new_data):
    """Compute the difference between two Triplet DataFrames.

    Parameters
    ----------
    old_data : pandas.DataFrame
        Original triplet dataset.
    new_data : pandas.DataFrame
        New triplet dataset to compare against.

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets unique to old_data or new_data, with an '_merge' column
        indicating 'left_only' (in old_data) or 'right_only' (in new_data).

    Examples
    --------
    >>> diff = diff_triplets(old_data, new_data)
    """
    return old_data.merge(new_data, on=["ID", "KEY", "VALUE"], how='outer', indicator=True, suffixes=("_OLD", "_NEW"), sort=False).query("_merge != 'both'")

def diff_triplets_by_instance(data, INSTANCE_ID_1, INSTANCE_ID_2):
    """Identify differences between two loaded INSTANCES, by thier INSTACE_ID in the same Triplet DataFrame.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing two or more INSTANCE.
    INSTANCE_ID_1 : str
        UUID of the first INSTANCE.
    INSTANCE_ID_2 : str
        UUID of the second INSTANCE.

    Returns
    -------
    pandas.DataFrame
        DataFrame containing triplets that differ between the two model parts.

    Examples
    --------
    >>> diff = diff_triplets_by_instance('uuid1', 'uuid2')
    """
    diff = data.query("INSTANCE_ID == '{}' or INSTANCE_ID == '{}'".format(INSTANCE_ID_1, INSTANCE_ID_2)).drop_duplicates(["ID", "KEY", "VALUE"], keep=False)

    return diff

def print_triplets_diff(old_data, new_data, file_id_object="Distribution", file_id_key="label", exclude_objects=None):
    """Print a human-readable diff of two triplet datasets.

    Parameters
    ----------
    old_data : pandas.DataFrame
        Original triplet dataset.
    new_data : pandas.DataFrame
        New triplet dataset to compare against.
    file_id_object : str, optional
        Object type containing file identifiers (default is 'Distribution').
    file_id_key : str, optional
        Key containing file identifiers (default is 'label').
    exclude_objects : list, optional
        List of object types to exclude from the diff (default is None).

    Notes
    -----
    - Outputs a diff format showing removed, added, and changed objects.
    - Nice diff viewer https://diffy.org/
    - TODO: Add name field for better reporting with Type.

    Examples
    --------
    >>> print_triplets_diff(old_data, new_data, exclude_objects=["NamespaceMap"])
    """
    # Get diff between datasets
    diff = diff_triplets(old_data, new_data)
    # Convert _merge to plain string before replacing (avoids categorical setitem error with pyarrow dtypes)
    diff["_merge"] = diff["_merge"].astype(str).replace({"left_only": "-", "right_only": "+"})
    diff = diff.sort_values(by=['ID', 'KEY'])

    # Extract internal structures keeping file name information
    file_id_data = filter_triplets_by_type(diff, file_id_object)
    diff = remove_triplets_from_triplets(diff, file_id_data)
    logger.info(f"INFO - removed {file_id_object} from diff")

    # Exclude defined types form export
    if exclude_objects:
        for object_name in exclude_objects:
            excluded_data = filter_triplets_by_type(diff, object_name)
            diff = remove_triplets_from_triplets(diff, excluded_data)
            logger.info(f"INFO - removed {object_name} from diff")

    # Extract types on left and right to get changed/modified types
    removed_added_modified_types = pandas.concat([
        old_data.merge(diff["ID"]).query("KEY == 'Type'").drop_duplicates(),
        new_data.merge(diff["ID"]).query("KEY == 'Type'").drop_duplicates()
        ])[["ID", "KEY", "VALUE"]].drop_duplicates()

    # Print old file name
    for _, file_id in old_data.query(f"KEY == '{file_id_key}'").VALUE.items():
        print(f"--- {file_id}")# from-file-modification-time")

    # Print new file name
    for _, file_id in new_data.query(f"KEY == '{file_id_key}'").VALUE.items():
        print(f"+++ {file_id}")# to-file-modification-time")

    # Print changes

    print("")
    print(f"@@ -1,0 +1,0 @@ Removed:")

    for key, value in diff.query("KEY == 'Type' and _merge == '-'").VALUE.value_counts().items():
        print(" ", key, value)

    print("")
    print(f"@@ -1,0 +1,0 @@ Added:")
    for key, value in diff.query("KEY == 'Type' and _merge == '+'").VALUE.value_counts().items():
        print(" ", key, value)

    print("")
    print(f"@@ -1,0 +1,0 @@ Changed:")
    for key, value in pandas.concat([removed_added_modified_types, diff.query("KEY == 'Type'")])[["ID", "KEY", "VALUE"]].drop_duplicates(keep=False).VALUE.value_counts().items():
        print(" ", key, value)

    # Types changed
    # TODO add name field to be used with Type for better reporting

    for group_name, group in removed_added_modified_types.groupby("VALUE"):
        #print(f"Types - {group_name}")
        for objec_type in group.itertuples():

            current_diff = diff.query("ID == @objec_type.ID")

            changes_on_left = len(current_diff.query("_merge == '-'"))
            changes_on_right = len(current_diff.query("_merge == '+'"))
            print("")
            print(f"@@ -1,{changes_on_left} +1,{changes_on_right} @@ {objec_type.VALUE} {objec_type.ID}")

            for _, change in (current_diff._merge.astype(str) + current_diff.KEY.astype(str) + " -> " + current_diff.VALUE.astype(str)).items():
                print(change)

    # Nice diff viewer https://diffy.org/
