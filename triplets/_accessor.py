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

import pandas
from . import tools, export


@pandas.api.extensions.register_dataframe_accessor("triplets")
class TripletsAccessor:
    """Triplet operations on pandas DataFrames via df.triplets.* namespace."""

    def __init__(self, df):
        self._df = df

    # ── Query ────────────────────────────────────────────────────────────
    def type_tableview(self, *a, **kw):
        return tools.type_tableview(self._df, *a, **kw)

    def key_tableview(self, *a, **kw):
        return tools.key_tableview(self._df, *a, **kw)

    def id_tableview(self, *a, **kw):
        return tools.id_tableview(self._df, *a, **kw)

    def types_dict(self, **kw):
        return tools.types_dict(self._df, **kw)

    def get_object_data(self, *a, **kw):
        return tools.get_object_data(self._df, *a, **kw)

    def get_namespace_map(self, **kw):
        return tools.get_namespace_map(self._df, **kw)

    # ── References ───────────────────────────────────────────────────────
    def references_to_simple(self, *a, **kw):
        return tools.references_to_simple(self._df, *a, **kw)

    def references_to(self, *a, **kw):
        return tools.references_to(self._df, *a, **kw)

    def references_from_simple(self, *a, **kw):
        return tools.references_from_simple(self._df, *a, **kw)

    def references_from(self, *a, **kw):
        return tools.references_from(self._df, *a, **kw)

    def references_all(self, **kw):
        return tools.references_all(self._df, **kw)

    def references_simple(self, *a, **kw):
        return tools.references_simple(self._df, *a, **kw)

    def references(self, *a, **kw):
        return tools.references(self._df, *a, **kw)

    # ── Filter ───────────────────────────────────────────────────────────
    def filter_by_type(self, *a, **kw):
        return tools.filter_by_type(self._df, *a, **kw)

    def filter_by_triplet(self, *a, **kw):
        return tools.filter_by_triplet(self._df, *a, **kw)

    def filter_triplets(self, *a, **kw):
        return tools.filter_triplets(self._df, *a, **kw)

    # ── Mutate ───────────────────────────────────────────────────────────
    def set_VALUE_at_KEY(self, *a, **kw):
        return tools.set_VALUE_at_KEY(self._df, *a, **kw)

    def set_VALUE_at_KEY_and_ID(self, *a, **kw):
        return tools.set_VALUE_at_KEY_and_ID(self._df, *a, **kw)

    def update_triplet_from_triplet(self, *a, **kw):
        return tools.update_triplet_from_triplet(self._df, *a, **kw)

    def update_triplet_from_tableview(self, *a, **kw):
        return tools.update_triplet_from_tableview(self._df, *a, **kw)

    # ── Transform ────────────────────────────────────────────────────────
    def triplet_to_tableviews(self, **kw):
        return tools.triplet_to_tableviews(self._df, **kw)

    def tableview_to_triplet(self, **kw):
        return tools.tableview_to_triplet(self._df, **kw)

    # ── Diff ─────────────────────────────────────────────────────────────
    def diff_between_INSTANCE(self, *a, **kw):
        return tools.diff_between_INSTANCE(self._df, *a, **kw)

    # ── Export ───────────────────────────────────────────────────────────
    def export_to_excel(self, *a, **kw):
        return export.export_to_excel(self._df, *a, **kw)

    def export_to_csv(self, *a, **kw):
        return export.export_to_csv(self._df, *a, **kw)

    def export_to_cimxml(self, *a, **kw):
        return export.export_to_cimxml(self._df, *a, **kw)

    def export_to_nquads(self, *a, **kw):
        return export.export_to_nquads(self._df, *a, **kw)

    def export_to_networkx(self, **kw):
        return export.export_to_networkx(self._df, **kw)


# ── Polars namespace ─────────────────────────────────────────────────────────
try:
    import polars as pl

    @pl.api.register_dataframe_namespace("triplets")
    class PolarsTripletsAccessor:
        """Triplet operations on polars DataFrames via df.triplets.* namespace."""

        def __init__(self, df):
            self._df = df

        # ── Query ────────────────────────────────────────────────────────
        def type_tableview(self, *a, **kw):
            return tools.type_tableview(self._df, *a, **kw)

        def key_tableview(self, *a, **kw):
            return tools.key_tableview(self._df, *a, **kw)

        def id_tableview(self, *a, **kw):
            return tools.id_tableview(self._df, *a, **kw)

        def types_dict(self, **kw):
            return tools.types_dict(self._df, **kw)

        def get_object_data(self, *a, **kw):
            return tools.get_object_data(self._df, *a, **kw)

        def get_namespace_map(self, **kw):
            return tools.get_namespace_map(self._df, **kw)

        # ── References ───────────────────────────────────────────────────
        def references_to(self, *a, **kw):
            return tools.references_to(self._df, *a, **kw)

        def references_from(self, *a, **kw):
            return tools.references_from(self._df, *a, **kw)

        def references(self, *a, **kw):
            return tools.references(self._df, *a, **kw)

        # ── Filter ───────────────────────────────────────────────────────
        def filter_by_type(self, *a, **kw):
            return tools.filter_by_type(self._df, *a, **kw)

        def filter_triplets(self, *a, **kw):
            return tools.filter_triplets(self._df, *a, **kw)

        # ── Transform ────────────────────────────────────────────────────
        def triplet_to_tableviews(self, **kw):
            return tools.triplet_to_tableviews(self._df, **kw)

        def tableview_to_triplet(self, **kw):
            return tools.tableview_to_triplet(self._df, **kw)

        # ── Diff ─────────────────────────────────────────────────────────
        def diff_between_INSTANCE(self, *a, **kw):
            return tools.diff_between_INSTANCE(self._df, *a, **kw)

        # ── Export ───────────────────────────────────────────────────────
        def export_to_excel(self, *a, **kw):
            return export.export_to_excel(self._df, *a, **kw)

        def export_to_csv(self, *a, **kw):
            return export.export_to_csv(self._df, *a, **kw)

        def export_to_cimxml(self, *a, **kw):
            return export.export_to_cimxml(self._df, *a, **kw)

        def export_to_nquads(self, *a, **kw):
            return export.export_to_nquads(self._df, *a, **kw)

        def export_to_networkx(self, **kw):
            return export.export_to_networkx(self._df, **kw)

except ImportError:
    pass


# ── DuckDB connection ───────────────────────────────────────────────────────
try:
    import duckdb
    from .tools import duckdb_engine as _duckdb_tools

    # Tools
    duckdb.DuckDBPyConnection.types_dict = _duckdb_tools.types_dict
    duckdb.DuckDBPyConnection.type_tableview = _duckdb_tools.type_tableview
    duckdb.DuckDBPyConnection.filter_triplets = _duckdb_tools.filter_triplets
    duckdb.DuckDBPyConnection.filter_by_type = _duckdb_tools.filter_by_type
    duckdb.DuckDBPyConnection.references_to = _duckdb_tools.references_to
    duckdb.DuckDBPyConnection.references_from = _duckdb_tools.references_from

    # Export — DuckDB exports go through pandas (fetch .df() then use pandas export)
    def _duckdb_export_to_excel(self, path, table_name="triplets"):
        df = self.execute(f"SELECT * FROM {table_name}").df()
        return export.export_to_excel(df, path=path)

    def _duckdb_export_to_nquads(self, path, rdf_map=None, table_name="triplets"):
        df = self.execute(f"SELECT * FROM {table_name}").df()
        return export.export_to_nquads(df, path, rdf_map=rdf_map)

    def _duckdb_export_to_csv(self, path=None, table_name="triplets", **kwargs):
        df = self.execute(f"SELECT * FROM {table_name}").df()
        return export.export_to_csv(df, path=path, **kwargs)

    duckdb.DuckDBPyConnection.export_to_excel = _duckdb_export_to_excel
    duckdb.DuckDBPyConnection.export_to_nquads = _duckdb_export_to_nquads
    duckdb.DuckDBPyConnection.export_to_csv = _duckdb_export_to_csv
except ImportError:
    pass
