"""CGMES tools — metadata, visualization, and data quality utilities.

Re-exports all public functions from pandas_engine for backwards compatibility.
`from triplets import cgmes_tools` and `triplets.cgmes_tools.function()` work as before.
"""
from .pandas_engine import *  # noqa: F401,F403
from .pandas_engine import (
    dependencies,
    default_filename_mask,
    generate_instances_ID,
    get_metadata_from_filename,
    get_filename_from_metadata,
    get_metadata_from_xml,
    get_metadata_from_FullModel,
    update_FullModel_from_dict,
    update_FullModel_from_filename,
    update_filename_from_FullModel,
    get_loaded_models,
    get_model_data,
    get_loaded_model_parts,
    get_EIC_to_mRID_map,
    get_GeneratingUnits,
    statistics_GeneratingUnit_types,
    get_limits,
    scale_load,
    switch_equipment_terminals,
    darw_relations_graph,
    draw_relations_to,
    draw_relations_from,
    draw_relations,
    get_dangling_references,
)
