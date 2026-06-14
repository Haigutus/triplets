"""Accessor registration for pandas and polars DataFrames.

Registers the `df.triplets.*` namespace on both pandas and polars DataFrames,
providing triplet data manipulation and export operations.

Usage:
    import triplets

    data = pandas.read_RDF(["grid.xml"])
    data.triplets.type_tableview("ACLineSegment")
    data.triplets.types_dict()
    data.triplets.export_to_excel(export_to_memory=True)

    data = polars.read_rdf(["grid.xml"])
    data.triplets.type_tableview("ACLineSegment")
"""

import logging
import pandas

from . import tools, export, sparql, validation

logger = logging.getLogger(__name__)

try:
    import polars
except ImportError:
    polars = None

try:
    import duckdb
except ImportError:
    duckdb = None


# ── Method registries ────────────────────────────────────────────────────────
# Each name maps 1:1 to a function in tools/ or export/ that takes the
# DataFrame as its first argument. The accessor classes below are generated
# from these lists.

PANDAS_TOOL_METHODS = [
    # query
    "type_tableview", "key_tableview", "id_tableview", "types_dict",
    "get_object_data", "get_namespace_map",
    # references
    "references_to_simple", "references_to", "references_from_simple",
    "references_from", "references_all", "references_simple", "references",
    # filter
    "filter_triplets_by_type", "filter_triplets_by_triplets", "filter_triplets",
    # mutate
    "set_value_at_key", "set_value_at_key_and_id",
    "update_triplets_from_triplets", "update_triplets_from_tableview",
    # transform
    "triplets_to_tableviews", "tableview_to_triplets",
    # diff
    "diff_triplets_by_instance",
]

# Subset of tools currently implemented by the polars engine
POLARS_TOOL_METHODS = [
    # query
    "type_tableview", "key_tableview", "id_tableview", "types_dict",
    "get_object_data", "get_namespace_map",
    # references
    "references_to", "references_from", "references",
    # filter
    "filter_triplets_by_type", "filter_triplets",
    # transform
    "triplets_to_tableviews", "tableview_to_triplets",
    # diff
    "diff_triplets_by_instance",
]

EXPORT_METHODS = [
    "export_to_excel", "export_to_csv", "export_to_cimxml",
    "export_to_nquads", "export_to_networkx",
]

# Subset of tools implemented by the duckdb engine (patched onto the
# connection object directly, the connection holds the triplets table)
DUCKDB_TOOL_METHODS = [
    "types_dict", "type_tableview", "filter_triplets", "filter_triplets_by_type",
    "references_to", "references_from",
]

DUCKDB_EXPORT_METHODS = ["export_to_excel", "export_to_csv", "export_to_nquads", "export_to_cimxml"]


def _delegate(module, name):
    """Make an accessor method that calls module.name(self._df, ...)."""
    function = getattr(module, name)

    def method(self, *args, **kwargs):
        return function(self._df, *args, **kwargs)

    method.__name__ = name
    method.__doc__ = function.__doc__
    return method


def _add_methods(accessor_class, tool_methods):
    for name in tool_methods:
        setattr(accessor_class, name, _delegate(tools, name))
    for name in EXPORT_METHODS:
        setattr(accessor_class, name, _delegate(export, name))
    # convenience aliases (first-class, group by prefix for IDE autocomplete)
    for alias, target in tools.ALIASES.items():
        if target in tool_methods:
            setattr(accessor_class, alias, _delegate(tools, alias))
    # old names renamed in 0.1 — delegate through the tools alias, which warns
    for old_name, new_name in tools.DEPRECATED_ALIASES.items():
        if new_name in tool_methods:
            setattr(accessor_class, old_name, _delegate(tools, old_name))


# ── pandas ────────────────────────────────────────────────────────────────────
@pandas.api.extensions.register_dataframe_accessor("triplets")
class TripletsAccessor:
    """Triplet operations on pandas DataFrames via df.triplets.* namespace."""

    def __init__(self, df):
        self._df = df


_add_methods(TripletsAccessor, PANDAS_TOOL_METHODS)
logger.debug("Registered pandas triplets accessor")


# ── polars ────────────────────────────────────────────────────────────────────
if polars:
    @polars.api.register_dataframe_namespace("triplets")
    class PolarsTripletsAccessor:
        """Triplet operations on polars DataFrames via df.triplets.* namespace."""

        def __init__(self, df):
            self._df = df

    _add_methods(PolarsTripletsAccessor, POLARS_TOOL_METHODS)
    logger.debug("Registered polars triplets namespace accessor")
else:
    logger.debug("polars not installed, skipping triplets namespace accessor")


# ── df.sparql.* / df.shacl.* (separate root accessors) ──────────────────────
# These take triplet data in any flavor; the engines load it into rdflib.
# (DuckDB connections already have a native .query() — call
# triplets.sparql.query(connection, ...) directly instead of a method.)

def _register_root_accessor(name, methods):
    """Register one extra root accessor (df.<name>.<method>) on pandas + polars."""
    class _Accessor:
        def __init__(self, df):
            self._df = df

    _Accessor.__name__ = f"{name.capitalize()}Accessor"
    for module, method in methods:
        setattr(_Accessor, method, _delegate(module, method))

    pandas.api.extensions.register_dataframe_accessor(name)(_Accessor)
    if polars:
        polars.api.register_dataframe_namespace(name)(type(f"Polars{_Accessor.__name__}", (_Accessor,), {}))
    logger.debug("Registered df.%s accessor", name)


_register_root_accessor("sparql", [(sparql, "query")])
_register_root_accessor("shacl", [(validation, "validate")])


# ── DuckDB ────────────────────────────────────────────────────────────────────
if duckdb:
    from .tools import duckdb_engine

    def _duckdb_export(name):
        """Make a connection method: fetch the triplets table, run the pandas export."""
        function = getattr(export, name)

        def method(connection, *args, table_name="triplets", **kwargs):
            df = connection.execute(f"SELECT * FROM {table_name}").df()
            return function(df, *args, **kwargs)

        method.__name__ = name
        method.__doc__ = function.__doc__
        return method

    for name in DUCKDB_TOOL_METHODS:
        setattr(duckdb.DuckDBPyConnection, name, getattr(duckdb_engine, name))
    for alias, target in tools.ALIASES.items():
        if target in DUCKDB_TOOL_METHODS:
            setattr(duckdb.DuckDBPyConnection, alias, getattr(duckdb_engine, target))
    for name in DUCKDB_EXPORT_METHODS:
        setattr(duckdb.DuckDBPyConnection, name, _duckdb_export(name))
    logger.debug("Registered DuckDB connection tools + export helpers")
else:
    logger.debug("duckdb not installed, skipping DuckDB tools/export patches")
