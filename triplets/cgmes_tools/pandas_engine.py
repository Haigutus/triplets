#-------------------------------------------------------------------------------
# Name:        cgmes_tools
# Purpose:     Collection of functions to work with CGMES files
#
# Author:      kristjan.vilgo
#
# Created:     2019-06-10
# Copyright:   (c) kristjan.vilgo 2019
# Licence:     MIT
#-------------------------------------------------------------------------------

import html
import json
import os
import webbrowser
import pandas
import math
import aniso8601

from uuid import uuid4
from lxml import etree
from builtins import str
from triplets import tools as rdf_parser  # backwards compat alias; tools replaces rdf_parser
from triplets.parser import parse as load_all_to_dataframe

import logging

logger = logging.getLogger(__name__)



dependencies = dict(EQ   = ["EQBD"],
                    SSH  = ["EQ"],
                    TP   = ["EQ"],
                    SV   = ["TPBD", "TP", "SSH"],
                    TPBD = ["EQBD"],
                    EQBD = [])

def generate_instances_ID(dependencies=dependencies):
    """Generate UUIDs for each profile defined in the dependencies dictionary.

    Parameters
    ----------
    dependencies : dict, optional
        Dictionary mapping profile names to lists of dependent profile names.
        Defaults to a predefined CGMES profile dependencies dictionary.

    Returns
    -------
    dict
        Dictionary with profile names as keys and generated UUIDs as values.

    Examples
    --------
    >>> generate_instances_ID()
    {'EQ': '123e4567-e89b-12d3-a456-426614174000', ...}
    """
    return {profile: str(uuid4()) for profile in dependencies}


def get_metadata_from_filename(file_name):
    """Extract metadata from a CGMES filename following the CGMES naming convention.

    Parameters
    ----------
    file_name : str
        Name of the CGMES file (e.g., '20230101T0000Z_A01_ENTITY_EQ_001.xml').

    Returns
    -------
    dict
        Dictionary containing metadata keys (e.g., 'Model.scenarioTime', 'Model.processType')
        and their corresponding values extracted from the filename.

    Notes
    -----
    - Expects filenames to follow CGMES conventions with underscores separating metadata fields.
    - Handles cases with 4 or 5 metadata elements, setting 'Model.processType' to empty string
      for older formats (pre-QoDC 2.1).
    - Splits 'Model.modelingEntity' into 'Model.mergingEntity', 'Model.domain', and
      'Model.forEntity' if applicable.

    Examples
    --------
    >>> get_metadata_from_filename('20230101T0000Z_A01_ENTITY_EQ_001.xml')
    {'Model.scenarioTime': '20230101T0000Z', 'Model.processType': 'A01', ...}
    """
    # Separators
    file_type_separator           = "."
    meta_separator                = "_"
    entity_and_domain_separator   = "-"

    #print(file_name)
    file_metadata = {}
    file_name, file_type = file_name.split(file_type_separator)

    # Parse file metadata
    file_meta_list = file_name.split(meta_separator)

    # Naming before QoDC 2.1, where EQ might not have processType
    if len(file_meta_list) == 4:

        file_metadata["Model.scenarioTime"],\
        file_metadata["Model.modelingEntity"],\
        file_metadata["Model.messageType"],\
        file_metadata["Model.version"] = file_meta_list
        file_metadata["Model.processType"] = ""

        print("Warning - only 4 meta elements found, expecting 5, setting Model.processType to empty string")

    # Naming after QoDC 2.1, always 5 positions
    elif len(file_meta_list) == 5:

        file_metadata["Model.scenarioTime"],\
        file_metadata["Model.processType"],\
        file_metadata["Model.modelingEntity"],\
        file_metadata["Model.messageType"],\
        file_metadata["Model.version"] = file_meta_list

    else:
        print("Non CGMES file {}".format(file_name))

    if file_metadata.get("Model.modelingEntity", False):

        entity_and_area_list = file_metadata["Model.modelingEntity"].split(entity_and_domain_separator)

        if len(entity_and_area_list) == 1:
            file_metadata["Model.mergingEntity"],\
            file_metadata["Model.domain"] = "", "" # Set empty string for both
            file_metadata["Model.forEntity"] = entity_and_area_list[0]

        if len(entity_and_area_list) == 2:
            file_metadata["Model.mergingEntity"],\
            file_metadata["Model.domain"] = entity_and_area_list
            file_metadata["Model.forEntity"] = ""

        if len(entity_and_area_list) == 3:
            file_metadata["Model.mergingEntity"],\
            file_metadata["Model.domain"],\
            file_metadata["Model.forEntity"] = entity_and_area_list


    return file_metadata


default_filename_mask = "{scenarioTime:%Y%m%dT%H%MZ}_{processType}_{modelingEntity}_{messageType}_{version:03d}"


def get_filename_from_metadata(meta_data, file_type="xml", filename_mask=default_filename_mask):
    """Generate a CGMES filename from metadata using a specified filename mask.

    Parameters
    ----------
    meta_data : dict
        Dictionary containing metadata keys (e.g., 'scenarioTime', 'processType') and values.
    file_type : str, optional
        File extension for the generated filename (default is 'xml').
    filename_mask : str, optional
        Format string defining the filename structure (default follows CGMES convention).

    Returns
    -------
    str
        Generated filename adhering to the CGMES naming convention.

    Notes
    -----
    - Removes 'Model.' prefix from metadata keys for compatibility with string formatting.
    - Converts 'scenarioTime' to datetime and 'version' to integer before formatting.
    - Uses the provided filename_mask to construct the filename.

    Examples
    --------
    >>> meta = {'Model.scenarioTime': '20230101T0000Z', 'Model.processType': 'A01', ...}
    >>> get_filename_from_metadata(meta)
    '20230101T0000Z_A01_ENTITY_EQ_001.xml'
    """
    # Separators
    file_type_separator = "."
    meta_separator = "_"
    entity_and_area_separator = "-"

    # Remove Model. from dictionary as python string format can't use . in variable name
    meta_data = {key.split(".")[1]:meta_data[key] for key in meta_data}

    # DateTime fields from text to DateTime
    DateTime_fields = ["scenarioTime"]#, 'created']
    for field in DateTime_fields:
        meta_data[field] = aniso8601.parse_datetime(meta_data[field])

    # Integers to integers
    meta_data["version"] = int(meta_data["version"])

    # Add metadata to file name string
    file_name = filename_mask.format(**meta_data)

    # Add file type to file name string
    file_name = file_type_separator.join([file_name, file_type])

    return file_name


def get_metadata_from_xml(filepath_or_fileobject):
    """Extract metadata from the FullModel element of a CGMES XML file.

    Parameters
    ----------
    filepath_or_fileobject : str or file-like object
        Path to the XML file or a file-like object containing CGMES XML data.

    Returns
    -------
    pandas.DataFrame
        DataFrame with columns ['tag', 'text', 'attrib'] containing metadata from the
        FullModel element.

    Examples
    --------
    >>> df = get_metadata_from_xml('path/to/file.xml')
    >>> print(df)
       tag                text  attrib
    0  Model.scenarioTime  20230101T0000Z  {}
    ...
    """
    parsed_xml = etree.parse(filepath_or_fileobject)

    header = parsed_xml.find("{*}FullModel")
    meta_elements = header.getchildren()

    meta_list = []
    for element in meta_elements:
         meta_list.append([element.tag, element.text, element.attrib])

    xml_metadata = pandas.DataFrame(meta_list, columns=["tag", "text", "attrib"])

    return xml_metadata


def get_metadata_from_FullModel(data):
    """Extract metadata from the FullModel entries in a CGMES triplet dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data with 'KEY', 'VALUE', and 'ID' columns.

    Returns
    -------
    dict
        Dictionary of metadata key-value pairs for the FullModel instance.

    Notes
    -----
    - Assumes the dataset contains a 'Type' key with value 'FullModel'.
    - Removes the 'Type' key from the resulting metadata dictionary.

    Examples
    --------
    >>> meta = get_metadata_from_FullModel(data)
    >>> print(meta)
    {'Model.scenarioTime': '20230101T0000Z', 'Model.processType': 'A01', ...}
    """
    UUID = data.query("KEY == 'Type' and VALUE == 'FullModel'").ID.iloc[0]
    metadata = data.get_object_data(UUID).to_dict()
    metadata.pop("Type", None)  # Remove Type form metadata

    return metadata


def update_FullModel_from_dict(data, metadata, update=True, add=False):
    """Update or add metadata to FullModel entries in a CGMES triplet dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    metadata : dict
        Dictionary of metadata key-value pairs to update or add.
    update : bool, optional
        If True, update existing metadata keys (default is True).
    add : bool, optional
        If True, add new metadata keys (default is False).

    Returns
    -------
    pandas.DataFrame
        Updated triplet dataset with modified FullModel metadata.

    Examples
    --------
    >>> meta = {'Model.scenarioTime': '20230102T0000Z'}
    >>> updated_data = update_FullModel_from_dict(data, meta)
    """
    additional_meta_list = []

    for row in data.query("KEY == 'Type' and VALUE == 'FullModel'").itertuples():
        for key in metadata:
            additional_meta_list.append({"ID": row.ID, "KEY": key, "VALUE": metadata[key], "INSTANCE_ID": row.INSTANCE_ID})

    update_data = pandas.DataFrame(additional_meta_list)

    return data.update_triplets_from_triplets(update_data, update, add)

def update_FullModel_from_filename(data, parser=get_metadata_from_filename, update=False, add=True):
    """Update FullModel metadata in a triplet dataset using metadata parsed from filenames.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data with 'label' keys for filenames.
    parser : callable, optional
        Function to parse metadata from filenames, returning a dictionary
        (default is get_metadata_from_filename).
    update : bool, optional
        If True, update existing metadata keys (default is False).
    add : bool, optional
        If True, add new metadata keys (default is True).

    Returns
    -------
    pandas.DataFrame
        Updated triplet dataset with FullModel metadata derived from filenames.

    Examples
    --------
    >>> updated_data = update_FullModel_from_filename(data)
    """
    additional_meta_list = []

    # For each instance that has label, as label contains the filename
    for label in data.query("KEY == 'label'").itertuples():
        # Parse metadata from filename to dictionary
        metadata = parser(label.VALUE)

        # Create triplets form parsed metadata
        for row in data.query("KEY == 'Type' and VALUE == 'FullModel' and INSTANCE_ID == '{}'".format(label.INSTANCE_ID)).itertuples():
            for key in metadata:
                additional_meta_list.append({"ID": row.ID, "KEY": key, "VALUE": metadata[key], "INSTANCE_ID": row.INSTANCE_ID})

    update_data = pandas.DataFrame(additional_meta_list)

    return data.update_triplets_from_triplets(update_data, update, add)


def update_filename_from_FullModel(data, filename_mask=default_filename_mask, filename_key="label"):
    """Update filenames in a CGMES triplet dataset based on FullModel metadata.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data with FullModel metadata.
    filename_mask : str, optional
        Format string defining the filename structure (default follows CGMES convention).
    filename_key : str, optional
        Key in the dataset where filenames are stored (default is 'label').

    Returns
    -------
    pandas.DataFrame
        Updated triplet dataset with filenames modified based on FullModel metadata.

    Examples
    --------
    >>> updated_data = update_filename_from_FullModel(data)
    """
    list_of_updates = []

    for _, label in data.query("KEY == '{}'".format(filename_key)).iterrows():
        # Get metadata
        metadata = get_metadata_from_FullModel(data.query("INSTANCE_ID == '{}'".format(label.INSTANCE_ID)))
        # Get new filename
        filename = get_filename_from_metadata(metadata, filename_mask=filename_mask)
        # Set new filename
        # data.loc[_, "VALUE"] = filename
        list_of_updates.append({"ID": label.ID, "KEY": filename_key, "VALUE": filename, "INSTANCE_ID": label.INSTANCE_ID})

    update_data = pandas.DataFrame(list_of_updates)
    return data.update_triplets_from_triplets(update_data, add=False)


def get_loaded_models(data):
    """Retrieve a dictionary of loaded CGMES model parts and their UUIDs.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data with 'Model.profile' and 'Model.DependentOn' keys.

    Returns
    -------
    dict
        Dictionary where keys are StateVariables (SV) UUIDs and values are DataFrames
        containing model parts (ID, PROFILE, INSTANCE_ID) and their dependencies.

    Examples
    --------
    >>> models = get_loaded_models(data)
    >>> print(models)
    {'SV_UUID': DataFrame(...), ...}
    """
    FullModel_data = data.query("KEY == 'Model.profile' or KEY == 'Model.DependentOn'")

    SV_iterator = FullModel_data.query("VALUE == 'http://entsoe.eu/CIM/StateVariables/4/1'").itertuples()

    dependancies_dict = {}

    for SV in SV_iterator:

        current_dependencies = []

        dependancies_list = [SV.ID]

        for instance in dependancies_list:

            # Append current instance
            PROFILES = FullModel_data.query("ID == @instance & KEY == 'Model.profile'")

            for PROFILE in PROFILES.itertuples():
                current_dependencies.append(dict(ID=instance, PROFILE=PROFILE.VALUE, INSTANCE_ID=PROFILE.INSTANCE_ID))

            # Add newly found dependacies to processing
            dependancies_list.extend(FullModel_data.query("ID == @instance & KEY == 'Model.DependentOn'").VALUE.tolist())


        dependancies_dict[SV.ID] = pandas.DataFrame(current_dependencies).drop_duplicates()

        #print dependancies_dict


    return dependancies_dict

def get_model_data(data, model_instances_dataframe):
    """Extract data for specific CGMES model instances.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    model_instances_dataframe : pandas.DataFrame
        DataFrame containing 'INSTANCE_ID' column with model instance identifiers.

    Returns
    -------
    pandas.DataFrame
        Filtered dataset containing only data for the specified model instances.

    Examples
    --------
    >>> model_data = get_model_data(data, models['SV_UUID'])
    """
    IGM_data = pandas.merge(data, model_instances_dataframe[["INSTANCE_ID"]].drop_duplicates(), right_on="INSTANCE_ID", left_on="INSTANCE_ID")

    return IGM_data

def get_EIC_to_mRID_map(data, type):
    """Map Energy Identification Codes (EIC) to mRIDs for a specific object type.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    type : str
        Object type to filter (e.g., 'PowerTransformer').

    Returns
    -------
    pandas.DataFrame
        DataFrame with columns ['mRID', 'EIC'] mapping EICs to mRIDs.

    Notes
    -----
    - Filters data for objects of the specified type with 'IdentifiedObject.energyIdentCodeEic' key.
    - TODO: Add support for type=None to return all types and include type in result.

    Examples
    --------
    >>> eic_map = get_EIC_to_mRID_map(data, 'PowerTransformer')
    >>> print(eic_map)
       mRID                                  EIC
    0  uuid1  10X1001A1001A021
    """
    name_map = {"ID": "mRID", "VALUE": "EIC"}
    return rdf_parser.filter_triplets_by_type(data, type).drop_duplicates().query("KEY == 'IdentifiedObject.energyIdentCodeEic'")[name_map.keys()].rename(columns=name_map)


def get_loaded_model_parts(data):
    """Retrieve a DataFrame of loaded CGMES model parts with their FullModel metadata.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.

    Returns
    -------
    pandas.DataFrame
        DataFrame containing FullModel data for loaded model parts.

    Notes
    -----
    - Does not correctly resolve 'Model.DependentOn' relationships.

    Examples
    --------
    >>> model_parts = get_loaded_model_parts(data)
    """
    return data.type_tableview("FullModel")


def statistics_GeneratingUnit_types(data):
    """Calculate statistics for GeneratingUnit types in a CGMES dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.

    Returns
    -------
    pandas.DataFrame
        DataFrame with counts, total, and percentage of each GeneratingUnit type.

    Examples
    --------
    >>> stats = statistics_GeneratingUnit_types(data)
    >>> print(stats)
       Type  count  TOTAL    %
    0  Hydro   10     20  50.0
    ...
    """
    value_counts = pandas.DataFrame(get_GeneratingUnits(data).Type.value_counts())
    value_counts["TOTAL"] = value_counts["count"].sum()
    value_counts["%"] = value_counts["count"]/value_counts["TOTAL"]*100

    return value_counts


def get_GeneratingUnits(data):
    """Retrieve a table of GeneratingUnits from a CGMES dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.

    Returns
    -------
    pandas.DataFrame
        DataFrame containing GeneratingUnit data, filtered by 'GeneratingUnit.maxOperatingP'.

    Examples
    --------
    >>> units = get_GeneratingUnits(data)
    >>> print(units)
       ID  GeneratingUnit.maxOperatingP  ...
    """
    return data.key_tableview("GeneratingUnit.maxOperatingP")






def get_limits(data):
    """Retrieve operational limits from a CGMES dataset, including equipment types.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.

    Returns
    -------
    pandas.DataFrame
        DataFrame containing operational limits with associated equipment types.

    Notes
    -----
    - Combines OperationalLimitSet, OperationalLimit, OperationalLimitType, and Terminal data.
    - Links equipment via Terminal.ConductingEquipment or OperationalLimitSet.Equipment.

    Examples
    --------
    >>> limits = get_limits(data)
    """
    # Get Limit Sets
    limits = data.type_tableview('OperationalLimitSet', string_to_number=False).reset_index()

    # Add OperationalLimits
    limits = limits.merge(data.key_tableview('OperationalLimit.OperationalLimitSet').reset_index(), left_on='ID', right_on='OperationalLimit.OperationalLimitSet', suffixes=("_OperationalLimitSet", "_OperationalLimit"))

    # Add LimitTypes
    limits = limits.merge(data.type_tableview("OperationalLimitType", string_to_number=False).reset_index(), right_on="ID", left_on="OperationalLimit.OperationalLimitType")

    # Add link to equipment via Terminals
    limits = limits.merge(data.type_tableview('Terminal', string_to_number=False).reset_index(), left_on="OperationalLimitSet.Terminal", right_on="ID", suffixes=("", "_Terminal"))

    limits["ID_Equipment"] = None

    # Get Equipment via terminal -> 'OperationalLimitSet.Terminal' -> 'Terminal.ConductingEquipment'
    if 'Terminal.ConductingEquipment' in limits.columns:
        limits["ID_Equipment"] = limits["ID_Equipment"].fillna(limits["Terminal.ConductingEquipment"])

    # Get Equipment directly -> 'OperationalLimitSet.Equipment'
    if 'OperationalLimitSet.Equipment' in limits.columns:
        limits["ID_Equipment"] = limits["ID_Equipment"].fillna(limits['OperationalLimitSet.Equipment'])

    # Add equipment type
    limits = limits.merge(data.query("KEY == 'Type'")[["ID", "VALUE"]], left_on="ID_Equipment", right_on="ID", suffixes=("", "_Type")).rename(columns={"VALUE":"Equipment_Type"})

    return limits


GRAPH_OPTIONS = {
    "nodes": {
        "shape": "dot",
        "size": 10,
    },
    "edges": {
        "color": {"inherit": True},
        "smooth": False,
    },
    "layout": {
        "hierarchical": {
            "enabled": True,
            "direction": "LR",
            "sortMethod": "directed",
        },
    },
    "interaction": {
        "navigationButtons": True,
    },
    "physics": {
        "hierarchicalRepulsion": {
            "centralGravity": 0,
            "springLength": 75,
            "nodeDistance": 145,
            "damping": 0.2,
        },
        "maxVelocity": 28,
        "minVelocity": 0.75,
        "solver": "hierarchicalRepulsion",
    },
}


class RelationsGraph(str):
    """Self-contained HTML document of a relations graph. Displays inline in Jupyter."""

    def _repr_html_(self):
        return f'<iframe srcdoc="{html.escape(self)}" style="width:100%; height:650px; border:none"></iframe>'


def _render_graph_html(title, nodes, edges, options):
    """Fill the vis-network graph template (vendored, works offline)."""
    from importlib.resources import files
    from string import Template

    def as_json(data):
        return json.dumps(data).replace("</", "<\\/")  # keep "</script>" in values from closing the script tag

    static = files("triplets.cgmes_tools") / "static"
    template = Template((static / "relations_graph.html").read_text(encoding="utf-8"))
    return template.substitute(
        title=title,
        vis_js=(static / "vis-network.min.js").read_text(encoding="utf-8"),
        nodes=as_json(nodes),
        edges=as_json(edges),
        options=as_json(options),
    )


def _instance_filenames(data):
    """Map INSTANCE_ID -> source filename (no path). Distribution objects store it under KEY 'label'."""
    distributions = data[(data["KEY"] == "Type") & (data["VALUE"] == "Distribution")]["ID"]
    labels = data[(data["KEY"] == "label") & data["ID"].isin(distributions)]
    return {instance_id: os.path.basename(str(path)) for instance_id, path in zip(labels["INSTANCE_ID"], labels["VALUE"])}


def darw_relations_graph(reference_data, ID_COLUMN="ID", notebook=False, open_browser=True, instance_labels=None):
    """Create a temporary HTML file to visualize relations in a CGMES dataset.

    Parameters
    ----------
    reference_data : pandas.DataFrame
        Triplet dataset containing reference data for visualization.
    ID_COLUMN : str
        Column name containing IDs (e.g., 'ID').
    notebook : bool, optional
        If True, return the graph HTML for Jupyter notebook display (default is False).
    open_browser : bool, optional
        If True, open the generated HTML file in the default browser
        (default is True; only applies when notebook=False).
    instance_labels : dict, optional
        INSTANCE_ID -> filename mapping shown in the object data tables.
        If None, derived from reference_data (Type==Distribution, KEY==label).

    Returns
    -------
    str or RelationsGraph
        File path to the generated HTML file (if notebook=False) or the graph
        HTML (if notebook=True).

    Notes
    -----
    - Renders with vis-network (vendored, hierarchical layout); the HTML is
      self-contained and works offline.
    - Selecting a node opens a panel with the object data table and a copy button.

    Examples
    --------
    >>> file_path = darw_relations_graph(data, 'ID')
    """

    pivot = reference_data.drop_duplicates([ID_COLUMN, "KEY"]).pivot(index=ID_COLUMN, columns="KEY")["VALUE"].reset_index()

    # Node name: first available of
    #   IdentifiedObject.name,
    #   Model.profile (old FullModel header),
    #   keyword       (new dcat:Dataset header, short profile name; conformsTo can be multiple),
    #   label         (Distribution carries the source filename there),
    # falling back to the object ID.
    node_data = pivot[[ID_COLUMN, "Type"]].copy()
    name = pandas.Series(pandas.NA, index=pivot.index, dtype="object")
    for column in ("IdentifiedObject.name", "Model.profile", "keyword", "label"):
        if column in pivot.columns:
            name = name.fillna(pivot[column])
    node_data["name"] = name.fillna(pivot[ID_COLUMN])

    if instance_labels is None:
        instance_labels = _instance_filenames(reference_data)

    # Nodes: label + level for layout, object data table for the selection panel
    nodes = []
    for node in node_data.itertuples():
        object_data = reference_data.query("{} == '{}'".format(ID_COLUMN, node.ID))
        object_table = object_data[[ID_COLUMN, "KEY", "VALUE", "INSTANCE_ID"]].rename(columns={ID_COLUMN: "ID"}).drop_duplicates()
        # Show the source filename instead of the instance UUID
        object_table["INSTANCE_ID"] = object_table["INSTANCE_ID"].map(instance_labels).fillna(object_table["INSTANCE_ID"])
        nodes.append({
            "id": node.ID,
            "label": "{} - {}".format(node.Type, node.name),
            "title": node.ID,  # plain-text hover tooltip
            "size": 10,
            "level": int(object_data.level.iloc[0]),
            "objectTable": object_table.to_html(index=False),
        })

    # Edges
    edges = []
    if "ID_FROM" in reference_data.columns and "ID_TO" in reference_data.columns:
        connections = reference_data[["ID_FROM", "ID_TO"]].dropna().drop_duplicates()
        edges = [{"from": id_from, "to": id_to, "arrows": "to"} for id_from, id_to in connections.itertuples(index=False)]

    from_UUID = reference_data[ID_COLUMN].tolist()[0]
    graph_html = _render_graph_html(title=from_UUID, nodes=nodes, edges=edges, options=GRAPH_OPTIONS)

    if notebook:
        return RelationsGraph(graph_html)

    file_name = f"{from_UUID}.html"
    with open(file_name, "w", encoding="utf-8") as file:
        file.write(graph_html)
    file_path = os.path.abspath(file_name)
    if open_browser:
        webbrowser.open(f"file://{file_path}")
    return file_path



def draw_relations_to(data, UUID, notebook=False, open_browser=True):
    """Visualize relations pointing to a specific UUID in a CGMES dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    UUID : str
        UUID of the object to visualize incoming relations for.
    notebook : bool, optional
        If True, render the graph for Jupyter notebook (default is False).

    Returns
    -------
    str or RelationsGraph
        File path to the generated HTML file (if notebook=False) or the graph
        HTML (if notebook=True).

    Examples
    --------
    >>> file_path = draw_relations_to(data, 'uuid1')
    """
    reference_data = data.references_to(UUID, levels=99)

    ID_COLUMN = "ID"

    return darw_relations_graph(reference_data, ID_COLUMN, notebook, open_browser=open_browser, instance_labels=_instance_filenames(data))


def draw_relations_from(data, UUID, notebook=False, open_browser=True):
    """Visualize relations originating from a specific UUID in a CGMES dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    UUID : str
        UUID of the object to visualize outgoing relations for.
    notebook : bool, optional
        If True, render the graph for Jupyter notebook (default is False).

    Returns
    -------
    str or RelationsGraph
        File path to the generated HTML file (if notebook=False) or the graph
        HTML (if notebook=True).

    Examples
    --------
    >>> file_path = draw_relations_from(data, 'uuid1')
    """
    reference_data = data.references_from(UUID, levels=99)

    ID_COLUMN = "ID"

    return darw_relations_graph(reference_data, ID_COLUMN, notebook, open_browser=open_browser, instance_labels=_instance_filenames(data))


def draw_relations(data, UUID, notebook=False, levels=2, open_browser=True):
    """Visualize all relations (incoming and outgoing) for a specific UUID in a CGMES dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    UUID : str
        UUID of the object to visualize relations for.
    notebook : bool, optional
        If True, render the graph for Jupyter notebook (default is False).
    levels : int, optional
        Number of levels to traverse for relations (default is 2).

    Returns
    -------
    str or RelationsGraph
        File path to the generated HTML file (if notebook=False) or the graph
        HTML (if notebook=True).

    Examples
    --------
    >>> file_path = draw_relations(data, 'uuid1', levels=3)
    """
    reference_data = data.references(UUID, levels=levels)

    ID_COLUMN = "ID"

    return darw_relations_graph(reference_data, ID_COLUMN, notebook, open_browser=open_browser, instance_labels=_instance_filenames(data))


def scale_load(data, load_setpoint, cos_f=None):
    """Scale active and reactive power loads in a CGMES SSH instance.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing SSH load information.
    load_setpoint : float
        Target total active power (P) setpoint for scaling.
    cos_f : float, optional
        Cosine of the power factor angle (cos(φ)). If None, calculated from the
        ratio of total Q to P.

    Returns
    -------
    pandas.DataFrame
        Updated dataset with scaled P and Q values for ConformLoad instances.

    Notes
    -----
    - Scales only ConformLoad instances, preserving NonConformLoad values.
    - Maintains or computes the power factor using cos_f.

    Examples
    --------
    >>> updated_data = scale_load(data, load_setpoint=1000.0, cos_f=0.9)
    """
    # Retrieve load data and calculate total P and Q
    load_data = data.type_tableview('ConformLoad').reset_index()
    scalable_load_p = load_data["EnergyConsumer.p"].sum()
    scalable_load_q = load_data["EnergyConsumer.q"].sum()

    # Calculate cos_f if not provided
    if cos_f is None:
        cos_f = math.cos(math.atan(scalable_load_q / scalable_load_p))
        logger.info(f"cos(f) not given, taking from base case -> cos(f)={cos_f:.3f}")

    # Calculate total P including non-conform loads
    total_load_p = scalable_load_p + data.type_tableview('NonConformLoad')["EnergyConsumer.p"].sum()

    # Scale Load P across conform loads
    load_data["EnergyConsumer.p"] *= 1 + (load_setpoint - total_load_p) / scalable_load_p

    # Scale Load Q across conform loads based on the new P and the given or calculated cos_f
    load_data["EnergyConsumer.q"] = load_data["EnergyConsumer.p"] * math.tan(math.acos(cos_f))

    # Update the dataset with the new scaled P and Q values
    return data.update_triplets_from_tableview(load_data[['ID', 'EnergyConsumer.p', 'EnergyConsumer.q']], update=True, add=False)


def switch_equipment_terminals(data, equipment_id, connected: str="false"):
    """Update connection statuses of terminals for specified equipment in a CGMES dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing EQ and SSH information.
    equipment_id : str or list
        Identifier(s) (mRID) of the equipment whose terminals' statuses are to be updated.
    connected : str, optional
        New connection status ('true' or 'false', default is 'false').

    Returns
    -------
    pandas.DataFrame
        Updated dataset with modified terminal connection statuses.

    Raises
    ------
    ValueError
        If connected is not 'true' or 'false'.

    Examples
    --------
    >>> updated_data = switch_equipment_terminals(data, ['uuid1', 'uuid2'], connected='true')
    """

    # Validate the 'connected' parameter
    if connected not in ["true", "false"]:
        raise ValueError("The 'connected' parameter must be 'true' or 'false'.")

    # If only single ID is given wrap it into list
    if type(equipment_id) == str:
        equipment_id = [equipment_id]

    status_attribute = "ACDCTerminal.connected"

    # Find linked terminals to given equipment_id
    terminals = data.query("KEY == 'Terminal.ConductingEquipment'").merge(pandas.Series(equipment_id, name="VALUE"), on="VALUE")

    # Find correct instance ID (Status is in SSH, but EQ link in EQ)
    terminals = terminals[["ID", "KEY", "VALUE"]].merge(data.query("KEY == @status_attribute")[["ID", "INSTANCE_ID"]], on="ID")

    # Set the status attribute name
    terminals["KEY"] = status_attribute

    # Set the status (true/false)
    terminals["VALUE"] = connected

    return data.update_triplets_from_triplets(terminals, add=False, update=True)




def get_dangling_references(data, detailed=False):
    """Identify dangling references in a CGMES dataset.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing CGMES data.
    detailed : bool, optional
        If True, return detailed DataFrame of dangling references; otherwise, return
        counts of dangling reference types (default is False).

    Returns
    -------
    pandas.DataFrame or pandas.Series
        If detailed=True, a DataFrame with dangling references; otherwise, a Series with
        counts of dangling reference keys.

    Notes
    -----
    - Identifies references using the CGMES convention (e.g., keys with '.<CapitalLetter>').
    - A dangling reference is one where the referenced ID does not exist in the dataset.

    Examples
    --------
    >>> dangling = get_dangling_references(data, detailed=True)
    """
    cgmes_reference_pattern = r"\.[A-Z]"
    # Convert KEY to string first to handle pyarrow-backed dictionary dtypes
    references = data[data["KEY"].astype(str).str.contains(cgmes_reference_pattern)]
    dangling_references = data.query("KEY == 'Type'").merge(references, left_on="ID", right_on="VALUE", indicator=True, how="right", suffixes=("_TO", "_FROM")).query("_merge != 'both'")

    if detailed:
        return dangling_references
    else:
        return dangling_references.KEY_FROM.value_counts()


# TEST and examples
if __name__ == '__main__':

    path_list = ["../test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"]

    data = load_all_to_dataframe(path_list)


    object_UUID = "99722373_VL_TN1"

    draw_relations_from(data, object_UUID)
    draw_relations_to(data, object_UUID)
    draw_relations(data, object_UUID)










