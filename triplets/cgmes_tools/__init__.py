"""CGMES tools — metadata, visualization, and data quality utilities.

Functions accept triplet data in any supported flavor: pandas, polars, pyarrow
Table/RecordBatch, or a DuckDB connection holding a `triplets` table. A dispatcher
routes each call by input type:

- polars input → native polars engine (polars_engine.py), no pandas round-trip;
- pandas input → pandas engine (pandas_engine.py);
- pyarrow / DuckDB input → converted to pandas at the boundary (Arrow-backed,
  ~10 ms per million rows), run on the pandas engine, result converted back.

Functions without a polars implementation always take the pandas-boundary path;
the draw_references_* visualizers have native polars engines that query references
in polars and render via the shared pandas graph helper.
"""
import inspect
import functools
import logging

import pandas

from . import pandas_engine
from .._engine_detect import is_polars, match_flavor, to_pandas
from ..tools import _engine_functions, _deprecated_alias
from .pandas_engine import (  # noqa: F401 — no triplet-data argument, re-exported as-is
    dependencies,
    default_filename_mask,
    generate_instance_ids,
    get_metadata_from_filename,
    get_filename_from_metadata,
    get_metadata_from_xml,
)

try:
    from . import polars_engine
except ImportError:                                   # polars not installed
    polars_engine = None

logger = logging.getLogger(__name__)

# Functions taking a triplet dataset as first argument — wrapped below so any
# supported data flavor can be passed directly. Derived from the pandas engine
# (the reference surface): a data function is one whose first parameter is
# named ``data``; the filename/metadata helpers above take other inputs and
# are re-exported as-is.
DATA_FUNCTIONS = sorted(
    name for name, fn in _engine_functions(pandas_engine).items()
    if next(iter(inspect.signature(fn).parameters)) == "data")

# Old name → new name; old names keep working but emit DeprecationWarning
DEPRECATED_ALIASES = {
    "statistics_GeneratingUnit_types": "count_GeneratingUnit_types",
    "generate_instances_ID": "generate_instance_ids",
    "get_model_data": "get_model_triplets",
    "draw_relations_to": "draw_references_to",
    "draw_relations_from": "draw_references_from",
    "draw_relations": "draw_references",
}


def _to_pandas(data):
    """Any flavor → plain pandas (safe for in-place VALUE mutation in the engine)."""
    return to_pandas(data, plain=True)


def _match_input_flavor(result, data):
    """Convert a pandas DataFrame result back to the flavor of the input data."""
    return match_flavor(result, data)


def _resolve_engine(engine, data):
    if engine != "auto":
        return engine
    if is_polars(data):
        return "polars"
    if isinstance(data, pandas.DataFrame):
        return "pandas"
    return "other"                                    # pyarrow / duckdb → pandas boundary


def _data_dispatch(name):
    """Route a cgmes data function by input flavor.

    With ``engine="auto"``: native polars for polars input (pandas boundary when no
    polars implementation exists), native pandas for pandas input, pandas boundary
    for anything else (pyarrow / DuckDB). An explicit ``engine="polars"`` requires
    polars input and a polars implementation; an explicit ``engine="pandas"`` forces
    the pandas implementation, through the boundary for non-pandas input.
    """
    pandas_fn = getattr(pandas_engine, name)

    @functools.wraps(pandas_fn)
    def wrapper(data, *args, engine="auto", **kwargs):
        eng = _resolve_engine(engine, data)
        native_polars = polars_engine is not None and hasattr(polars_engine, name)
        if eng == "polars" and engine != "auto":
            if not is_polars(data):
                raise TypeError("engine='polars' but the input is not a polars DataFrame")
            if not native_polars:
                raise NotImplementedError(f"cgmes_tools.{name} has no polars engine")
        if eng == "polars" and native_polars:
            return getattr(polars_engine, name)(data, *args, **kwargs)
        if eng == "pandas" and isinstance(data, pandas.DataFrame):
            return pandas_fn(data, *args, **kwargs)
        return _match_input_flavor(pandas_fn(_to_pandas(data), *args, **kwargs), data)

    wrapper.__module__ = __name__  # else pydoc/Sphinx hide it as an imported name
    signature = inspect.signature(pandas_fn)
    wrapper.__signature__ = signature.replace(parameters=[
        *signature.parameters.values(),
        inspect.Parameter("engine", inspect.Parameter.KEYWORD_ONLY, default="auto")])
    return wrapper


for _name in DATA_FUNCTIONS:
    globals()[_name] = _data_dispatch(_name)

for _old, _new in DEPRECATED_ALIASES.items():
    globals()[_old] = _deprecated_alias(_old, _new, globals()[_new])

__all__ = [
    "dependencies", "default_filename_mask", "generate_instance_ids",
    "get_metadata_from_filename", "get_filename_from_metadata",
    "get_metadata_from_xml",
] + DATA_FUNCTIONS + list(DEPRECATED_ALIASES)
