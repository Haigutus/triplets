"""DuckDB engine — monkey-patches duckdb.DuckDBPyConnection with triplet operations.

When `import triplets` runs and duckdb is installed, triplet operations are
registered directly on DuckDB connections:

    import duckdb
    import triplets

    con = duckdb.connect()                          # in-memory
    con = duckdb.connect("grid.duckdb")             # persistent

    con.read_rdf(["grid_EQ.xml", "grid_SSH.xml"])   # parse → Arrow → DuckDB (zero-copy)
    con.types_dict()                                 # → dict
    con.type_tableview("ACLineSegment").df()         # → pandas DataFrame
    con.type_tableview("ACLineSegment").pl()         # → polars DataFrame
    con.filter_triplets(KEY="Type").arrow()           # → pyarrow Table
    con.references_to("some-uuid").df()
    con.sql("SELECT * FROM triplets WHERE KEY LIKE 'Model.%'").pl()

All methods except types_dict return DuckDBPyRelation (lazy, chainable).
Call .df(), .pl(), .arrow(), .fetchall() to materialize.
"""

import logging

logger = logging.getLogger(__name__)

TABLE_NAME = "triplets"


def _read_rdf(self, paths, table_name=TABLE_NAME, **kwargs):
    """Parse RDF/XML files and load into DuckDB table via Arrow (zero-copy).

    Parameters
    ----------
    paths : str or list
        XML/ZIP file paths to parse.
    table_name : str, default "triplets"
        Name of the DuckDB table to create.
    **kwargs
        Passed to triplets.parser.parse().
    """
    from triplets.parser import parse
    arrow_table = parse(paths, return_type="arrow", **kwargs)
    self.register("_arrow_import", arrow_table)
    self.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM _arrow_import")
    self.unregister("_arrow_import")
    row_count = self.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    logger.info(f"Loaded {row_count} rows into {table_name}")
    return row_count


def _types_dict(self, table_name=TABLE_NAME):
    """Return dict of {type_name: count}."""
    rows = self.execute(f"""
        SELECT VALUE, COUNT(DISTINCT ID) as count
        FROM {table_name}
        WHERE KEY = 'Type'
        GROUP BY VALUE
        ORDER BY count DESC
    """).fetchall()
    return dict(rows)


def _type_tableview(self, type_name, table_name=TABLE_NAME):
    """Create a pivoted table view of all objects of a specified type.

    Returns DuckDBPyRelation (lazy). Call .df(), .pl(), .arrow() to materialize.
    """
    return self.sql(f"""
        WITH type_ids AS (
            SELECT DISTINCT ID FROM {table_name}
            WHERE KEY = 'Type' AND VALUE = '{type_name}'
        ),
        type_data AS (
            SELECT d.ID, d.KEY, d.VALUE
            FROM {table_name} d
            JOIN type_ids t ON d.ID = t.ID
        )
        PIVOT type_data ON KEY USING FIRST(VALUE) GROUP BY ID
    """)


def _filter_triplets(self, ID=None, KEY=None, VALUE=None, INSTANCE_ID=None,
                     regex=False, table_name=TABLE_NAME):
    """Filter triplets by any combination of columns.

    Parameters
    ----------
    ID, KEY, VALUE, INSTANCE_ID : str, optional
        Filter value. If regex=True, treated as regex pattern (SIMILAR TO).
    regex : bool, default False
        If True, use SIMILAR TO for pattern matching.
    table_name : str, default "triplets"

    Returns DuckDBPyRelation (lazy).
    """
    conditions = []
    for col, val in [("ID", ID), ("KEY", KEY), ("VALUE", VALUE), ("INSTANCE_ID", INSTANCE_ID)]:
        if val is not None:
            if regex:
                conditions.append(f"{col} SIMILAR TO '{val}'")
            else:
                conditions.append(f"{col} = '{val}'")

    where = " AND ".join(conditions) if conditions else "TRUE"
    return self.sql(f"SELECT * FROM {table_name} WHERE {where}")


def _filter_by_type(self, type_name, table_name=TABLE_NAME):
    """Filter to only objects of a specific type.

    Returns DuckDBPyRelation (lazy).
    """
    return self.sql(f"""
        SELECT d.* FROM {table_name} d
        WHERE d.ID IN (
            SELECT ID FROM {table_name}
            WHERE KEY = 'Type' AND VALUE = '{type_name}'
        )
    """)


def _references_to(self, reference_id, table_name=TABLE_NAME):
    """Find objects that reference the given ID.

    Returns DuckDBPyRelation (lazy).
    """
    return self.sql(f"""
        SELECT d.* FROM {table_name} d
        WHERE d.ID IN (
            SELECT ID FROM {table_name}
            WHERE VALUE = '{reference_id}'
        )
    """)


def _references_from(self, reference_id, table_name=TABLE_NAME):
    """Find objects referenced BY the given ID.

    Returns DuckDBPyRelation (lazy).
    """
    return self.sql(f"""
        SELECT d.* FROM {table_name} d
        WHERE d.ID IN (
            SELECT VALUE FROM {table_name}
            WHERE ID = '{reference_id}' AND KEY != 'Type'
        )
    """)


def _export_to_csv(self, path, table_name=TABLE_NAME):
    """Export triplets table to CSV file."""
    self.execute(f"COPY {table_name} TO '{path}' (HEADER, DELIMITER ',')")
    logger.info(f"Exported {table_name} to {path}")


def _export_to_nquads(self, path, table_name=TABLE_NAME):
    """Export triplets table to N-Quads file."""
    CIM = "http://iec.ch/TC57/CIM100#"
    RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

    self.execute(f"""
        COPY (
            SELECT
                '<urn:uuid:' || ID || '>' || ' ' ||
                CASE WHEN KEY = 'Type'
                    THEN '<{RDF_TYPE}>'
                    ELSE '<{CIM}' || KEY || '>'
                END || ' ' ||
                CASE WHEN KEY = 'Type'
                    THEN '<{CIM}' || VALUE || '>'
                    ELSE '"' || VALUE || '"'
                END || ' ' ||
                '<urn:uuid:' || INSTANCE_ID || '>' || ' .' as quad
            FROM {table_name}
        ) TO '{path}' (HEADER false, QUOTE '', DELIMITER '')
    """)
    logger.info(f"Exported N-Quads to {path}")


def _export_to_excel(self, path, table_name=TABLE_NAME):
    """Export triplets table to Excel via pandas + openpyxl."""
    from triplets.export import export_to_excel
    df = self.execute(f"SELECT * FROM {table_name}").df()
    return export_to_excel(df, path=path)


# ── Register on DuckDB connection ────────────────────────────────────────────

def register():
    """Monkey-patch triplet operations onto duckdb.DuckDBPyConnection."""
    import duckdb
    duckdb.DuckDBPyConnection.read_rdf = _read_rdf
    duckdb.DuckDBPyConnection.types_dict = _types_dict
    duckdb.DuckDBPyConnection.type_tableview = _type_tableview
    duckdb.DuckDBPyConnection.filter_triplets = _filter_triplets
    duckdb.DuckDBPyConnection.filter_by_type = _filter_by_type
    duckdb.DuckDBPyConnection.references_to = _references_to
    duckdb.DuckDBPyConnection.references_from = _references_from
    duckdb.DuckDBPyConnection.export_to_csv = _export_to_csv
    duckdb.DuckDBPyConnection.export_to_nquads = _export_to_nquads
    duckdb.DuckDBPyConnection.export_to_excel = _export_to_excel


# Auto-register on import
try:
    register()
except Exception:
    pass
