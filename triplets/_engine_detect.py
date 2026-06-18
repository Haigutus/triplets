"""Shared input-flavor detection.

Used by the tools dispatcher (`tools._auto_engine`/`_get_engine`) and the export
auto-select (`export._is_polars`) so the "is this a polars DataFrame?" test lives in one
place. Prefers a real isinstance check when polars is installed.
"""


def is_polars(data) -> bool:
    """True if *data* is a polars DataFrame."""
    try:
        import polars
    except ImportError:
        return False
    return isinstance(data, polars.DataFrame)
