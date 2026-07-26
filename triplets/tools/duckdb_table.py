"""Per-connection triplets table/schema for DuckDB.

DuckDBPyConnection has no instance ``__dict__``, so defaults live in a
WeakKeyDictionary. ``duckdb.connect(table=..., schema=...)`` is wrapped at
import; tools resolve call kwargs → connection defaults → package defaults.
"""
from weakref import WeakKeyDictionary

DEFAULT_TABLE = "triplets"
DEFAULT_SCHEMA = None  # DuckDB main/default schema

_config = WeakKeyDictionary()  # connection → (schema, table)


def quote(name):
    """Quote a DuckDB identifier (double quotes; escape by doubling)."""
    return '"' + str(name).replace('"', '""') + '"'


def sql_name(schema, table):
    """Quoted table ref: ``"t"`` or ``"schema"."t"``."""
    if schema is None:
        return quote(table)
    return f"{quote(schema)}.{quote(table)}"


def get(connection):
    """``(schema, table)`` for *connection* (package defaults if unset)."""
    return _config.get(connection, (DEFAULT_SCHEMA, DEFAULT_TABLE))


def set(connection, table=DEFAULT_TABLE, schema=DEFAULT_SCHEMA):
    """Store per-connection defaults."""
    _config[connection] = (schema, table)


def resolve(connection, table=None, schema=None, table_name=None):
    """Quoted SQL table ref for a call.

    Call kwargs win; else connection config; else package defaults.
    ``table_name`` is a legacy alias for a bare table name (no schema).
    """
    cfg_schema, cfg_table = get(connection)
    if table is None:
        table = table_name if table_name is not None else cfg_table
    if schema is None:
        schema = cfg_schema
    return sql_name(schema, table)


def set_triplets_table(connection, table=DEFAULT_TABLE, schema=DEFAULT_SCHEMA):
    """Set this connection's default triplets table/schema. Returns the connection."""
    set(connection, table=table, schema=schema)
    return connection


def install_connect(duckdb_module):
    """Wrap ``duckdb.connect`` so it accepts ``table=`` / ``schema=``."""
    original = duckdb_module.connect

    def connect(*args, table=DEFAULT_TABLE, schema=DEFAULT_SCHEMA, **kwargs):
        connection = original(*args, **kwargs)
        set(connection, table=table, schema=schema)
        return connection

    duckdb_module.connect = connect
    return connect
