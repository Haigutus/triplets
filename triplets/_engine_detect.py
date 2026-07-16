"""Shared input-flavor detection and output-flavor conversion.

Used by the tools dispatcher (`tools._auto_engine`/`_get_engine`), the export
auto-select (`export._is_polars`), the SPARQL/validation engines' input
handling (`flavor`) and their result finalization (`to_return_type`) so the
"which flavor is this?" tests live in one place. `is_polars` prefers a real
isinstance check when polars is installed.
"""


def is_polars(data) -> bool:
    """True if *data* is a polars DataFrame."""
    try:
        import polars
    except ImportError:
        return False
    return isinstance(data, polars.DataFrame)


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


def to_return_type(frame, return_type):
    """pandas DataFrame → requested output flavor ("pandas" / "polars" / "arrow")."""
    if return_type == "polars":
        import polars
        return polars.from_pandas(frame)
    if return_type == "arrow":
        import pyarrow
        return pyarrow.Table.from_pandas(frame, preserve_index=False)
    return frame
