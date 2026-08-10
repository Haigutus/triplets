"""Shared input-flavor detection and conversion.

Used by tools dispatch, export auto-select, SPARQL/validation input handling,
and result finalization so "which flavor is this?" and "turn it into X" live
in one place. Prefer these helpers over local if-polars/if-duckdb blocks.
flavor() is the single detector — no per-flavor is_polars/is_pandas helpers.
"""


def flavor(data) -> str:
    """Input flavor by defining module: "polars" / "pyarrow" / "duckdb" / "pandas".

    Module-name based so no optional dependency needs to be importable to
    classify another flavor's objects; anything unrecognized is treated as pandas.
    """
    module = type(data).__module__
    if module.startswith("polars"):
        return "polars"
    if module.startswith("pyarrow"):
        return "pyarrow"
    if module.startswith(("duckdb", "_duckdb")):
        return "duckdb"
    return "pandas"


def _duckdb_ref(data, table=None, schema=None, table_name=None):
    """Quoted DuckDB table ref for *data* (lazy import avoids cycles)."""
    from .tools.duckdb_engine import _resolve_table
    return _resolve_table(data, table=table, schema=schema, table_name=table_name)


def to_pandas(data, *, plain=False, table=None, schema=None, table_name=None):
    """Any supported flavor → pandas DataFrame.

    plain=False
        Keep ArrowDtype when the source is pyarrow (cheap load path).
    plain=True
        Prefer numpy/category-backed frames safe for in-place VALUE mutation
        (cgmes): strip pyarrow table pandas metadata before converting.
    table / schema / table_name
        DuckDB only — forwarded to the connection's table resolve.
    """
    kind = flavor(data)
    if kind == "pandas":
        return data
    if kind == "polars":
        return data.to_pandas()
    if kind == "pyarrow":
        if plain and hasattr(data, "replace_schema_metadata"):
            data = data.replace_schema_metadata(None)
        if plain:
            return data.to_pandas()
        import pandas
        return data.to_pandas(types_mapper=pandas.ArrowDtype)
    if kind == "duckdb":
        ref = _duckdb_ref(data, table=table, schema=schema, table_name=table_name)
        return data.execute(f"SELECT * FROM {ref}").df()
    return data


def as_frame(data, *, plain=False, table=None, schema=None, table_name=None):
    """pandas/polars unchanged; arrow/duckdb → pandas (for exporters that take either frame)."""
    if flavor(data) in ("pandas", "polars"):
        return data
    return to_pandas(data, plain=plain, table=table, schema=schema, table_name=table_name)


def to_arrow(data, *, columns=None, table=None, schema=None, table_name=None):
    """Any supported flavor → pyarrow.Table.

    pyarrow input passes through as-is (a RecordBatch is not upgraded to a
    Table) — callers that require Table-only APIs must normalize themselves.

    columns
        Optional column names to project; None keeps all columns.
    table / schema / table_name
        DuckDB only — forwarded to the connection's table resolve.
    """
    kind = flavor(data)
    if kind == "duckdb":
        ref = _duckdb_ref(data, table=table, schema=schema, table_name=table_name)
        select = ", ".join(columns) if columns is not None else "*"
        return data.execute(f"SELECT {select} FROM {ref}").to_arrow_table()
    if kind == "pyarrow":
        return data.select(columns) if columns is not None else data
    if kind == "polars":
        frame = data.select(list(columns)) if columns is not None else data
        return frame.to_arrow()
    # pandas (and anything else treated as pandas)
    import pyarrow
    frame = data[list(columns)] if columns is not None else data
    return pyarrow.Table.from_pandas(frame, preserve_index=False)


def to_polars(data, *, table=None, schema=None, table_name=None):
    """Any supported flavor → polars DataFrame.

    DuckDB/pyarrow go through Arrow (not a pandas round-trip).
    """
    import polars

    kind = flavor(data)
    if kind == "polars":
        return data
    if kind == "pyarrow":
        return polars.from_arrow(data)
    if kind == "duckdb":
        return polars.from_arrow(to_arrow(data, table=table, schema=schema,
                                          table_name=table_name))
    return polars.from_pandas(data)


def match_flavor(result, template):
    """If *result* is a pandas DataFrame, convert it to *template*'s flavor.

    polars / pyarrow templates convert; duckdb and pandas leave pandas as-is.
    Non-DataFrame results pass through unchanged.
    """
    import pandas

    if not isinstance(result, pandas.DataFrame):
        return result
    kind = flavor(template)
    if kind == "polars":
        import polars
        keep_index = not isinstance(result.index, pandas.RangeIndex)
        return polars.from_pandas(result, include_index=keep_index)
    if kind == "pyarrow":
        import pyarrow
        keep_index = not isinstance(result.index, pandas.RangeIndex)
        return pyarrow.Table.from_pandas(result, preserve_index=keep_index)
    return result


def to_return_type(frame, return_type):
    """pandas DataFrame → requested output flavor ("pandas" / "polars" / "arrow")."""
    if return_type == "polars":
        import polars
        return polars.from_pandas(frame)
    if return_type == "arrow":
        import pyarrow
        return pyarrow.Table.from_pandas(frame, preserve_index=False)
    return frame
