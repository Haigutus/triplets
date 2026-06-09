# -------------------------------------------------------------------------------
# Name:        export/csv_pandas.py
# Purpose:     Export triplet DataFrames to CSV format
# -------------------------------------------------------------------------------
import os
import logging

from io import BytesIO

from triplets.tools import triplet_to_tableviews

logger = logging.getLogger(__name__)


def export_to_csv(data, path=None, multivalue=True, export_to_memory=False, single_file=False, base_filename=None):
    """Export triplet data to CSV files, with each type as a separate file.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.
    path : str, optional
        Directory path to save CSV file(s).
    multivalue : bool, default True
        If True, aggregate duplicate (ID, KEY) pairs into lists.
    export_to_memory : bool, default False
        If True, return BytesIO objects; if False, save to disk.
    single_file : bool, default False
        If True, export all data using a single base filename.
    base_filename : str, optional
        Base filename when single_file=True. If None, uses 'export'.
    """
    if single_file:
        if base_filename is None:
            base_filename = 'export'
        tableviews = triplet_to_tableviews(data, multivalue=multivalue)
        exported_files = []
        for class_type, class_data in tableviews.items():
            file_name = '{}_{}.csv'.format(base_filename, class_type)
            output = BytesIO()
            output.name = file_name
            output.write(class_data.to_csv().encode('utf-8'))
            output.seek(0)
            exported_files.append(output)
    else:
        labels = data.query("KEY == 'label'")
        exported_files = []
        for _, label in labels.iterrows():
            instance_data = data[data.INSTANCE_ID == label.INSTANCE_ID]
            tableviews = triplet_to_tableviews(instance_data, multivalue=multivalue)
            base_name = label.VALUE.split(".")[0]
            for class_type, class_data in tableviews.items():
                file_name = '{}_{}.csv'.format(base_name, class_type)
                output = BytesIO()
                output.name = file_name
                output.write(class_data.to_csv().encode('utf-8'))
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
            with open(export_path, 'wb') as f:
                file_object.seek(0)
                f.write(file_object.read())
            exported_file_names.append(file_object.name)
            logger.info(f'Saved {export_path}')
        return exported_file_names
