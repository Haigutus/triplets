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

logger = logging.getLogger(__name__)


def _auto_engine(data):
    """Pick engine based on DataFrame type."""
    if hasattr(data, '__module__') and 'polars' in type(data).__module__:
        try:
            from . import polars_engine
            return "polars"
        except ImportError:
            pass
    return "pandas"


def _get_engine(engine, data=None):
    """Resolve engine name and return the module."""
    if engine == "auto":
        engine = _auto_engine(data) if data is not None else "pandas"
    if engine == "polars":
        from . import polars_engine
        return polars_engine
    from . import pandas_engine
    return pandas_engine


# ── Dispatcher functions ────────────────────────────────────────────────────
# Each function delegates to the appropriate engine based on input type.

def type_tableview(data, type_name, string_to_number=True, type_key="Type", multivalue=False, engine="auto"):
    return _get_engine(engine, data).type_tableview(data, type_name, string_to_number=string_to_number, type_key=type_key, multivalue=multivalue)

def key_tableview(data, key, string_to_number=True, engine="auto"):
    return _get_engine(engine, data).key_tableview(data, key, string_to_number=string_to_number)

def id_tableview(data, id, string_to_number=True, engine="auto"):
    return _get_engine(engine, data).id_tableview(data, id, string_to_number=string_to_number)

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

def filter_by_type(data, type_name, type_key="Type", engine="auto"):
    return _get_engine(engine, data).filter_by_type(data, type_name, type_key=type_key)

def filter_by_triplet(data, filter_triplet, engine="auto"):
    return _get_engine(engine, data).filter_by_triplet(data, filter_triplet)

def set_VALUE_at_KEY(data, key, value, engine="auto"):
    return _get_engine(engine, data).set_VALUE_at_KEY(data, key, value)

def set_VALUE_at_KEY_and_ID(data, key, value, id, engine="auto"):
    return _get_engine(engine, data).set_VALUE_at_KEY_and_ID(data, key, value, id)

def triplet_to_tableviews(triplet_df, multivalue=False, engine="auto"):
    return _get_engine(engine, triplet_df).triplet_to_tableviews(triplet_df, multivalue=multivalue)

def tableviews_to_triplet(tableviews, multivalue=False, engine="auto"):
    # tableviews is a dict — detect engine from first value if available
    data = next(iter(tableviews.values()), None) if tableviews else None
    return _get_engine(engine, data).tableviews_to_triplet(tableviews, multivalue=multivalue)

def tableview_to_triplet(data, multivalue=False, engine="auto"):
    return _get_engine(engine, data).tableview_to_triplet(data, multivalue=multivalue)

def get_object_data(data, object_UUID, engine="auto"):
    return _get_engine(engine, data).get_object_data(data, object_UUID)

def update_triplet_from_triplet(data, update_data, update=True, add=True, engine="auto"):
    return _get_engine(engine, data).update_triplet_from_triplet(data, update_data, update=update, add=add)

def update_triplet_from_tableview(data, tableview, update=True, add=True, instance_id=None, engine="auto"):
    return _get_engine(engine, data).update_triplet_from_tableview(data, tableview, update=update, add=add, instance_id=instance_id)

def remove_triplet_from_triplet(from_triplet, what_triplet, columns=["ID", "KEY", "VALUE"], engine="auto"):
    return _get_engine(engine, from_triplet).remove_triplet_from_triplet(from_triplet, what_triplet, columns=columns)

def diff_between_triplet(old_data, new_data, engine="auto"):
    return _get_engine(engine, old_data).diff_between_triplet(old_data, new_data)

def diff_between_INSTANCE(data, INSTANCE_ID_1, INSTANCE_ID_2, engine="auto"):
    return _get_engine(engine, data).diff_between_INSTANCE(data, INSTANCE_ID_1, INSTANCE_ID_2)

def print_triplet_diff(old_data, new_data, file_id_object="Distribution", file_id_key="label", exclude_objects=None, engine="auto"):
    return _get_engine(engine, old_data).print_triplet_diff(old_data, new_data, file_id_object=file_id_object, file_id_key=file_id_key, exclude_objects=exclude_objects)


# ── Register on pandas DataFrames (backwards compat) ────────────────────────
import pandas
pandas.DataFrame.type_tableview = lambda self, *a, **kw: type_tableview(self, *a, **kw)
pandas.DataFrame.key_tableview = lambda self, *a, **kw: key_tableview(self, *a, **kw)
pandas.DataFrame.id_tableview = lambda self, *a, **kw: id_tableview(self, *a, **kw)
pandas.DataFrame.types_dict = lambda self, **kw: types_dict(self, **kw)
pandas.DataFrame.get_object_data = lambda self, *a, **kw: get_object_data(self, *a, **kw)
pandas.DataFrame.references_to_simple = lambda self, *a, **kw: references_to_simple(self, *a, **kw)
pandas.DataFrame.references_to = lambda self, *a, **kw: references_to(self, *a, **kw)
pandas.DataFrame.references_from_simple = lambda self, *a, **kw: references_from_simple(self, *a, **kw)
pandas.DataFrame.references_from = lambda self, *a, **kw: references_from(self, *a, **kw)
pandas.DataFrame.references_all = lambda self, **kw: references_all(self, **kw)
pandas.DataFrame.references_simple = lambda self, *a, **kw: references_simple(self, *a, **kw)
pandas.DataFrame.references = lambda self, *a, **kw: references(self, *a, **kw)
pandas.DataFrame.filter_by_type = lambda self, *a, **kw: filter_by_type(self, *a, **kw)
pandas.DataFrame.filter_by_triplet = lambda self, *a, **kw: filter_by_triplet(self, *a, **kw)
pandas.DataFrame.set_VALUE_at_KEY = lambda self, *a, **kw: set_VALUE_at_KEY(self, *a, **kw)
pandas.DataFrame.set_VALUE_at_KEY_and_ID = lambda self, *a, **kw: set_VALUE_at_KEY_and_ID(self, *a, **kw)
pandas.DataFrame.tableview_to_triplet = lambda self, *a, **kw: tableview_to_triplet(self, *a, **kw)
pandas.DataFrame.update_triplet_from_triplet = lambda self, *a, **kw: update_triplet_from_triplet(self, *a, **kw)
pandas.DataFrame.update_triplet_from_tableview = lambda self, *a, **kw: update_triplet_from_tableview(self, *a, **kw)
pandas.DataFrame.diff_between_INSTANCE = lambda self, *a, **kw: diff_between_INSTANCE(self, *a, **kw)
pandas.DataFrame.changes = pandas.DataFrame()
