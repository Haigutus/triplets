"""Triplet data manipulation tools with pandas/polars engine support.

Provides query, filter, diff, transform, and mutate operations on triplet
DataFrames ([ID, KEY, VALUE, INSTANCE_ID]).

Engines:
- pandas_engine (default, always available)
- polars_engine (optional, uses polars-native operations for speed)

The engine is auto-detected from the input DataFrame type, or can be
specified explicitly with engine="pandas" or engine="polars".
"""

import logging
import functools
import warnings

logger = logging.getLogger(__name__)


def _auto_engine(data):
    """Pick engine based on DataFrame type."""
    if hasattr(data, '__module__') and 'polars' in type(data).__module__:
        try:
            from . import polars_engine
            logger.debug(f"engine auto-selected: polars (input is polars DataFrame)")
            return "polars"
        except ImportError:
            pass
    logger.debug(f"engine auto-selected: pandas")
    return "pandas"


def _get_engine(engine, data=None):
    """Resolve engine name and return the module."""
    if engine == "auto":
        engine = _auto_engine(data) if data is not None else "pandas"
    else:
        logger.debug(f"engine set: {engine}")
    if engine == "polars":
        from . import polars_engine
        return polars_engine
    from . import pandas_engine
    return pandas_engine


# ── Dispatcher functions ────────────────────────────────────────────────────
# Each function delegates to the appropriate engine based on input type.

def type_tableview(data, type_name, string_to_number=True, type_key="Type", multivalue=False, engine="auto"):
    return _get_engine(engine, data).type_tableview(data, type_name, string_to_number=string_to_number, type_key=type_key, multivalue=multivalue)

def key_tableview(data, key, string_to_number=True, multivalue=False, engine="auto"):
    return _get_engine(engine, data).key_tableview(data, key, string_to_number=string_to_number, multivalue=multivalue)

def id_tableview(data, id, string_to_number=True, multivalue=False, engine="auto"):
    return _get_engine(engine, data).id_tableview(data, id, string_to_number=string_to_number, multivalue=multivalue)

def types_dict(data, engine="auto"):
    return _get_engine(engine, data).types_dict(data)

def get_object_data(data, object_UUID, engine="auto"):
    return _get_engine(engine, data).get_object_data(data, object_UUID)

def get_namespace_map(data, engine="auto"):
    return _get_engine(engine, data).get_namespace_map(data)

def references_to_simple(data, reference, columns=["Type"], engine="auto"):
    return _get_engine(engine, data).references_to_simple(data, reference, columns=columns)

def references_to(data, reference, levels=1, engine="auto"):
    return _get_engine(engine, data).references_to(data, reference, levels=levels)

def references_from_simple(data, reference, columns=["Type"], engine="auto"):
    return _get_engine(engine, data).references_from_simple(data, reference, columns=columns)

def references_from(data, reference, levels=1, engine="auto"):
    return _get_engine(engine, data).references_from(data, reference, levels=levels)

def references_all(data, engine="auto"):
    return _get_engine(engine, data).references_all(data)

def references_simple(data, reference, columns=None, levels=1, engine="auto"):
    return _get_engine(engine, data).references_simple(data, reference, columns=columns, levels=levels)

def references(data, ID, levels=1, engine="auto"):
    return _get_engine(engine, data).references(data, ID, levels=levels)

def filter_triplets_by_type(data, type_name, type_key="Type", engine="auto"):
    return _get_engine(engine, data).filter_triplets_by_type(data, type_name, type_key=type_key)

def filter_triplets_by_triplets(data, filter_triplet, engine="auto"):
    return _get_engine(engine, data).filter_triplets_by_triplets(data, filter_triplet)

def filter_triplets(data, ID=None, KEY=None, VALUE=None, INSTANCE_ID=None, regex=False, engine="auto"):
    return _get_engine(engine, data).filter_triplets(data, ID=ID, KEY=KEY, VALUE=VALUE, INSTANCE_ID=INSTANCE_ID, regex=regex)

def set_value_at_key(data, key, value, engine="auto"):
    return _get_engine(engine, data).set_value_at_key(data, key, value)

def set_value_at_key_and_id(data, key, value, id, engine="auto"):
    return _get_engine(engine, data).set_value_at_key_and_id(data, key, value, id)

def triplets_to_tableviews(triplet_df, multivalue=False, engine="auto"):
    return _get_engine(engine, triplet_df).triplets_to_tableviews(triplet_df, multivalue=multivalue)

def tableviews_to_triplets(tableviews, multivalue=False, engine="auto"):
    # tableviews is a dict — detect engine from first value if available
    data = next(iter(tableviews.values()), None) if tableviews else None
    return _get_engine(engine, data)._tableviews_to_triplets(tableviews, multivalue=multivalue)

def tableview_to_triplets(data, multivalue=False, engine="auto"):
    return _get_engine(engine, data).tableview_to_triplets(data, multivalue=multivalue)

def get_object_data(data, object_UUID, engine="auto"):
    return _get_engine(engine, data).get_object_data(data, object_UUID)

def update_triplets_from_triplets(data, update_data, update=True, add=True, engine="auto"):
    return _get_engine(engine, data).update_triplets_from_triplets(data, update_data, update=update, add=add)

def update_triplets_from_tableview(data, tableview, update=True, add=True, instance_id=None, engine="auto"):
    return _get_engine(engine, data).update_triplets_from_tableview(data, tableview, update=update, add=add, instance_id=instance_id)

def remove_triplets_from_triplets(from_triplet, what_triplet, columns=["ID", "KEY", "VALUE"], engine="auto"):
    return _get_engine(engine, from_triplet).remove_triplets_from_triplets(from_triplet, what_triplet, columns=columns)

def diff_triplets(old_data, new_data, engine="auto"):
    return _get_engine(engine, old_data).diff_triplets(old_data, new_data)

def diff_triplets_by_instance(data, INSTANCE_ID_1, INSTANCE_ID_2, engine="auto"):
    return _get_engine(engine, data).diff_triplets_by_instance(data, INSTANCE_ID_1, INSTANCE_ID_2)

def print_triplets_diff(old_data, new_data, file_id_object="Distribution", file_id_key="label", exclude_objects=None, engine="auto"):
    return _get_engine(engine, old_data).print_triplets_diff(old_data, new_data, file_id_object=file_id_object, file_id_key=file_id_key, exclude_objects=exclude_objects)


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


def _deprecated_alias(old_name, new_name):
    new_function = globals()[new_name]

    @functools.wraps(new_function)
    def wrapper(*args, **kwargs):
        warnings.warn(f"tools.{old_name} is deprecated, use tools.{new_name}()",
                      DeprecationWarning, stacklevel=2)
        return new_function(*args, **kwargs)

    return wrapper


for _old, _new in DEPRECATED_ALIASES.items():
    globals()[_old] = _deprecated_alias(_old, _new)


# ── Auto-registration on objects ────────────────────────────────────────────
import inspect
import pandas

from . import pandas_engine


def _engine_functions(module):
    """Public functions defined in *module* — each takes the df/connection first.

    The registered method surface is derived from this instead of a hand-kept list,
    so it tracks the engine module automatically. `_`-prefixed helpers (e.g.
    `_tableviews_to_triplets`, which takes a dict, not a DataFrame) are excluded.
    """
    return {name: obj for name, obj in inspect.getmembers(module, inspect.isfunction)
            if not name.startswith("_") and obj.__module__ == module.__name__}


def _dataframe_method(function):
    def method(self, *args, **kwargs):
        return function(self, *args, **kwargs)
    return method


def _is_native(target_class, name):
    """True if *name* is already a non-triplets (native) attribute of target_class."""
    existing = getattr(target_class, name, None)
    return existing is not None and not getattr(existing, "__module__", "").startswith("triplets")


def _register_root(target_class, names):
    """Monkey-patch each name (resolved in this module's namespace) onto target_class.

    Skips names that are native attributes so we never clobber them, but still lets
    the current implementation supersede triplets' own legacy patches (e.g. the
    deprecated rdf_parser monkey-patches applied earlier at import time).
    """
    for name in names:
        if _is_native(target_class, name):
            logger.debug("skip %s.%s — native attribute present", target_class.__name__, name)
            continue
        setattr(target_class, name, _dataframe_method(globals()[name]))


# ── Register on pandas DataFrames (backwards compat) ────────────────────────
# Primary methods auto-derived from the pandas engine; alias / deprecated names
# register only when their target is one of those methods.
DATAFRAME_METHODS = sorted(_engine_functions(pandas_engine))

_register_root(pandas.DataFrame, DATAFRAME_METHODS)
_register_root(pandas.DataFrame, [a for a, target in ALIASES.items() if target in DATAFRAME_METHODS])
_register_root(pandas.DataFrame, [a for a, target in DEPRECATED_ALIASES.items() if target in DATAFRAME_METHODS])
