"""CSV export using polars native write_csv.

Exports triplet data to CSV files, one per type. Uses polars for fast I/O.
"""

import os
import logging
from io import BytesIO

import polars as pl

logger = logging.getLogger(__name__)


def export_to_csv(data, path=None, multivalue=True, export_to_memory=False, single_file=False, base_filename=None):
    """Export triplet data to CSV files using polars.

    Parameters
    ----------
    data : polars.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    path : str, optional
        Directory path to save CSV files.
    multivalue : bool, default True
        If True, aggregate duplicate (ID, KEY) pairs into lists.
    export_to_memory : bool, default False
        If True, return BytesIO objects; if False, save to disk.
    single_file : bool, default False
        If True, export all data using a single base filename.
    base_filename : str, optional
        Base filename when single_file=True. If None, uses 'export'.
    """
    from triplets.tools import polars_engine

    def _tableviews(df):
        td = polars_engine.types_dict(df)
        views = {}
        for class_name in td:
            # Use multivalue=False for polars CSV — list aggregation not needed for CSV output
            tv = polars_engine.type_tableview(df, class_name, string_to_number=False, multivalue=False)
            if tv is not None:
                views[class_name] = tv
        return views

    if single_file:
        if base_filename is None:
            base_filename = "export"
        tableviews = _tableviews(data)
        exported_files = []
        for class_type, class_data in tableviews.items():
            file_name = f"{base_filename}_{class_type}.csv"
            output = BytesIO()
            output.name = file_name
            class_data.write_csv(output)
            output.seek(0)
            exported_files.append(output)
    else:
        labels = data.filter(pl.col("KEY") == "label")
        exported_files = []
        for row in labels.iter_rows(named=True):
            instance_data = data.filter(pl.col("INSTANCE_ID") == row["INSTANCE_ID"])
            tableviews = _tableviews(instance_data)
            base_name = row["VALUE"].split(".")[0]
            for class_type, class_data in tableviews.items():
                file_name = f"{base_name}_{class_type}.csv"
                output = BytesIO()
                output.name = file_name
                class_data.write_csv(output)
                output.seek(0)
                exported_files.append(output)

    if export_to_memory:
        return exported_files
    else:
        if path is None:
            path = os.getcwd()
        exported_file_names = []
        for file_object in exported_files:
            export_path = os.path.join(path, file_object.name)
            with open(export_path, "wb") as f:
                file_object.seek(0)
                f.write(file_object.read())
            exported_file_names.append(file_object.name)
            logger.info(f"Saved {export_path}")
        return exported_file_names
