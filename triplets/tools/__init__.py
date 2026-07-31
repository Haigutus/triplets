"""Triplet data manipulation tools with pandas/polars/duckdb engine support.

Provides query, filter, diff, transform, and mutate operations on triplet
DataFrames ([ID, KEY, VALUE, INSTANCE_ID]).

Engines:
- pandas_engine (default, always available)
- polars_engine (optional, uses polars-native operations for speed)
- duckdb_engine (optional, connection-first: functions take the connection)

Every public function here dispatches by the input object's flavor (pandas /
polars DataFrame or DuckDB connection), or by an explicit engine= name that
must match the input. Frame ops always run in the input's engine — never
auto-hop flavors. Methods registered on DataFrames and connections (see
triplets._accessor) bind the engine functions directly — the object a method
is called on already determines the engine.
"""

import logging
import inspect
import functools
import warnings

from .._engine_detect import flavor
from .._registry import EngineRegistry

logger = logging.getLogger(__name__)

_REGISTRY = EngineRegistry(
    "tools", __package__, policy="input",
    modules={"pandas": ".pandas_engine", "polars": ".polars_engine", "duckdb": ".duckdb_engine"},
    requires={"polars": ("polars",), "duckdb": ("duckdb",)},
    hints={"polars": "Install with: pip install triplets[polars].",
           "duckdb": "Install with: pip install triplets[duckdb]."},
)


def _engine_functions(module):
    """Public functions defined in *module* — each takes the df/connection first.

    The dispatched/registered method surface is derived from this instead of a
    hand-kept list, so it tracks the engine module automatically. `_`-prefixed
    helpers (e.g. `_tableviews_to_triplets`, which takes a dict, not a DataFrame)
    are excluded.
    """
    return {name: obj for name, obj in inspect.getmembers(module, inspect.isfunction)
            if not name.startswith("_") and obj.__module__ == module.__name__}


def _get_engine(engine, data=None):
    """Resolve the engine module: auto = the input object's flavor.

    Frame ops never hop engines — with ``engine="auto"`` the input's flavor
    (pandas/polars DataFrame or DuckDB connection) picks the module; an
    explicit engine must match the input, failing with TypeError at the
    boundary rather than deep inside the engine.
    """
    if isinstance(data, dict):  # tableviews: detect/validate from the first frame
        data = next(iter(data.values()), None)
    kind = flavor(data) if data is not None else None
    if kind == "pyarrow":
        kind = "pandas"                      # arrow input rides the pandas engine
    if engine == "auto":
        engine = kind or "pandas"
        logger.debug("engine auto-selected: %s (input flavor)", engine)
    elif kind is not None and engine in _REGISTRY.modules and engine != kind:
        what = "a DuckDB connection" if kind == "duckdb" else f"a {kind} DataFrame"
        raise TypeError(f"engine={engine!r} but the input is {what}")
    return _REGISTRY.get(engine)[1]


# ── Dispatchers ──────────────────────────────────────────────────────────────
# One dispatcher per engine function, generated from the pandas engine (the
# reference surface — polars_engine is optional so it stays a lazy import).
# Each carries the engine function's signature/doc plus keyword-only engine="auto".
from . import pandas_engine


def _dispatcher(name, target, reference):
    first_param = next(iter(inspect.signature(reference).parameters))

    @functools.wraps(reference)
    def dispatcher(*args, engine="auto", **kwargs):
        data = args[0] if args else kwargs.get(first_param)
        module = _get_engine(engine, data)
        target_fn = getattr(module, target, None)
        if target_fn is None:
            engine_name = module.__name__.rsplit(".", 1)[-1].removesuffix("_engine")
            raise NotImplementedError(f"tools.{name} has no {engine_name} engine")
        return target_fn(*args, **kwargs)

    dispatcher.__name__ = dispatcher.__qualname__ = name
    dispatcher.__module__ = __name__  # else pydoc/Sphinx hide it as an imported name
    signature = inspect.signature(reference)
    dispatcher.__signature__ = signature.replace(parameters=[
        *signature.parameters.values(),
        inspect.Parameter("engine", inspect.Parameter.KEYWORD_ONLY, default="auto")])
    return dispatcher


# public name -> engine-module attribute; special cases are registry entries
DISPATCHED = {name: name for name in _engine_functions(pandas_engine)}
DISPATCHED["tableviews_to_triplets"] = "_tableviews_to_triplets"  # dict-first, private in engines

for _name, _target in DISPATCHED.items():
    globals()[_name] = _dispatcher(_name, _target, getattr(pandas_engine, _target))


# ── Convenience aliases (not deprecated — both names are first-class) ───────
# Alias → target. The aliases group functions by prefix for IDE autocomplete:
# typing "get", "tableview" or "references" surfaces the whole family.
ALIASES = {
    "get_types_count": "types_dict",
    "tableview_by_type": "type_tableview",
    "tableview_by_key": "key_tableview",
    "tableview_by_id": "id_tableview",
}

for _alias, _target in ALIASES.items():
    globals()[_alias] = globals()[_target]


# ── Deprecated names (renamed in 0.1; removal in 0.2) ───────────────────────
# Old name → new name; old names keep working but emit DeprecationWarning.
DEPRECATED_ALIASES = {
    "filter_by_type": "filter_triplets_by_type",
    "filter_by_triplet": "filter_triplets_by_triplets",
    "set_VALUE_at_KEY": "set_value_at_key",
    "set_VALUE_at_KEY_and_ID": "set_value_at_key_and_id",
    "update_triplet_from_triplet": "update_triplets_from_triplets",
    "update_triplet_from_tableview": "update_triplets_from_tableview",
    "remove_triplet_from_triplet": "remove_triplets_from_triplets",
    "triplet_to_tableviews": "triplets_to_tableviews",
    "tableview_to_triplet": "tableview_to_triplets",
    "tableviews_to_triplet": "tableviews_to_triplets",
    "diff_between_triplet": "diff_triplets",
    "diff_between_INSTANCE": "diff_triplets_by_instance",
    "print_triplet_diff": "print_triplets_diff",
}


def _deprecated_alias(old_name, new_name, new_function):
    @functools.wraps(new_function)
    def wrapper(*args, **kwargs):
        warnings.warn(f"{old_name} is deprecated, use {new_name}()",
                      DeprecationWarning, stacklevel=2)
        return new_function(*args, **kwargs)

    return wrapper


for _old, _new in DEPRECATED_ALIASES.items():
    globals()[_old] = _deprecated_alias(_old, _new, globals()[_new])
