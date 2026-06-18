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

from . import tools, export

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
# Tool methods are auto-derived from each engine module (every public function
# there takes the DataFrame/connection as its first argument). Export methods
# live in export/ — not the engine modules — so they stay listed explicitly.
from .tools import _engine_functions, pandas_engine

PANDAS_TOOL_METHODS = sorted(_engine_functions(pandas_engine))

EXPORT_METHODS = [
    "export_to_excel", "export_to_csv", "export_to_cimxml",
    "export_to_nquads", "export_to_networkx",
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
    from .tools import polars_engine, _register_root

    POLARS_TOOL_METHODS = sorted(_engine_functions(polars_engine))

    @polars.api.register_dataframe_namespace("triplets")
    class PolarsTripletsAccessor:
        """Triplet operations on polars DataFrames via df.triplets.* namespace."""

        def __init__(self, df):
            self._df = df

    _add_methods(PolarsTripletsAccessor, POLARS_TOOL_METHODS)

    # Root symmetry with pandas: pl_df.type_tableview(...) works like the accessor.
    # The tools dispatcher auto-detects polars from the DataFrame. None of the names
    # is "triplets", so the namespace registered above is left intact.
    _register_root(polars.DataFrame, POLARS_TOOL_METHODS)
    _register_root(polars.DataFrame, [a for a, t in tools.ALIASES.items() if t in POLARS_TOOL_METHODS])
    _register_root(polars.DataFrame, [a for a, t in tools.DEPRECATED_ALIASES.items() if t in POLARS_TOOL_METHODS])
    logger.debug("Registered polars triplets namespace accessor + root methods")
else:
    logger.debug("polars not installed, skipping triplets namespace accessor")


# ── DuckDB ────────────────────────────────────────────────────────────────────
if duckdb:
    from .tools import duckdb_engine

    DUCKDB_TOOL_METHODS = sorted(_engine_functions(duckdb_engine))

    def _duckdb_export_fn(name):
        """A connection-first export callable: fetch the triplets table, run the export.

        Note: this materialises the whole `triplets` table into a pandas DataFrame
        (``SELECT * FROM triplets``) before exporting — a memory spike for large grids.
        """
        function = getattr(export, name)

        def fn(connection, *args, table_name="triplets", **kwargs):
            df = connection.execute(f"SELECT * FROM {table_name}").df()
            return function(df, *args, **kwargs)

        fn.__name__ = name
        fn.__doc__ = function.__doc__
        return fn

    # All connection-first callables to expose: engine tools + aliases + exports.
    _duckdb_methods = {name: getattr(duckdb_engine, name) for name in DUCKDB_TOOL_METHODS}
    _duckdb_methods.update({alias: getattr(duckdb_engine, target)
                            for alias, target in tools.ALIASES.items() if target in DUCKDB_TOOL_METHODS})
    _duckdb_methods.update({name: _duckdb_export_fn(name) for name in DUCKDB_EXPORT_METHODS})

    # Root: the connection is `self`. Skip native attributes so we never clobber a
    # built-in connection method (none of our names collide today).
    for _name, _fn in _duckdb_methods.items():
        if tools._is_native(duckdb.DuckDBPyConnection, _name):
            logger.debug("skip DuckDBPyConnection.%s — native attribute present", _name)
            continue
        setattr(duckdb.DuckDBPyConnection, _name, _fn)

    # Namespace: con.triplets.* — duckdb has no register_*_namespace API, so expose an
    # accessor via a property (accepted on the C-extension type). None of the method
    # names is "triplets", so the property does not collide with them.
    class DuckDBTripletsAccessor:
        """Triplet operations on a DuckDB connection via con.triplets.* namespace."""

        def __init__(self, connection):
            self._df = connection

    def _duckdb_accessor_method(fn):
        def method(self, *args, **kwargs):
            return fn(self._df, *args, **kwargs)
        method.__name__ = getattr(fn, "__name__", "method")
        method.__doc__ = getattr(fn, "__doc__", None)
        return method

    for _name, _fn in _duckdb_methods.items():
        setattr(DuckDBTripletsAccessor, _name, _duckdb_accessor_method(_fn))
    setattr(duckdb.DuckDBPyConnection, "triplets",
            property(lambda self: DuckDBTripletsAccessor(self)))
    logger.debug("Registered DuckDB connection tools + exports (root + namespace)")
else:
    logger.debug("duckdb not installed, skipping DuckDB tools/export patches")
