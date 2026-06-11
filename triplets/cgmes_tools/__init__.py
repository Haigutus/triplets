"""CGMES tools — metadata, visualization, and data quality utilities.

The implementations are pandas-based (pandas_engine.py), but the functions
accept triplet data in any supported flavor: pandas, polars, pyarrow
Table/RecordBatch, or a DuckDB connection holding a `triplets` table.
Non-pandas input is converted at this boundary (Arrow-backed, ~10 ms per
million rows), and DataFrame results are converted back to the input flavor
(DuckDB input returns pandas).
"""
import functools
import logging

import pandas

from . import pandas_engine
from .pandas_engine import (  # noqa: F401 — no triplet-data argument, re-exported as-is
    dependencies,
    default_filename_mask,
    generate_instances_ID,
    get_metadata_from_filename,
    get_filename_from_metadata,
    get_metadata_from_xml,
)

logger = logging.getLogger(__name__)

# Functions taking a triplet dataset as first argument — wrapped below so any
# supported data flavor can be passed directly.
DATA_FUNCTIONS = [
    # metadata
    "get_metadata_from_FullModel", "update_FullModel_from_dict",
    "update_FullModel_from_filename", "update_filename_from_FullModel",
    # model inventory
    "get_loaded_models", "get_model_data", "get_loaded_model_parts",
    "get_EIC_to_mRID_map",
    # equipment / statistics
    "get_GeneratingUnits", "statistics_GeneratingUnit_types", "get_limits",
    # modification
    "scale_load", "switch_equipment_terminals",
    # data quality
    "get_dangling_references",
    # visualization
    "darw_relations_graph", "draw_relations_to", "draw_relations_from",
    "draw_relations",
]


def _to_pandas(data):
    """Triplet data in any supported flavor → pandas DataFrame."""
    if isinstance(data, pandas.DataFrame):
        return data
    module = type(data).__module__
    if module.startswith("polars"):
        logger.debug("cgmes_tools input: polars → pandas")
        return data.to_pandas(use_pyarrow_extension_array=True)
    if module.startswith("pyarrow"):
        logger.debug("cgmes_tools input: pyarrow → pandas")
        return data.to_pandas(types_mapper=pandas.ArrowDtype)
    if module.startswith(("duckdb", "_duckdb")):
        logger.debug("cgmes_tools input: duckdb triplets table → pandas")
        return data.execute("SELECT * FROM triplets").df()
    return data  # trust pandas-compatible input


def _match_input_flavor(result, data):
    """Convert a pandas DataFrame result back to the flavor of the input data."""
    if not isinstance(result, pandas.DataFrame):
        return result
    keep_index = not isinstance(result.index, pandas.RangeIndex)
    module = type(data).__module__
    if module.startswith("polars"):
        import polars
        return polars.from_pandas(result, include_index=keep_index)
    if module.startswith("pyarrow"):
        import pyarrow
        return pyarrow.Table.from_pandas(result, preserve_index=keep_index)
    return result  # pandas in (and duckdb in) → pandas out


def _pandas_boundary(function):
    @functools.wraps(function)
    def wrapper(data, *args, **kwargs):
        result = function(_to_pandas(data), *args, **kwargs)
        return _match_input_flavor(result, data)
    return wrapper


for _name in DATA_FUNCTIONS:
    globals()[_name] = _pandas_boundary(getattr(pandas_engine, _name))

__all__ = [
    "dependencies", "default_filename_mask", "generate_instances_ID",
    "get_metadata_from_filename", "get_filename_from_metadata",
    "get_metadata_from_xml",
] + DATA_FUNCTIONS
