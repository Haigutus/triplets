"""DuckDB SQL-based implementation of triplet tools.

Functions are monkey-patched onto duckdb.DuckDBPyConnection.
All return DuckDBPyRelation (lazy) except types_dict which returns dict.
"""

TABLE_NAME = "triplets"


def types_dict(self, table_name=TABLE_NAME):
    """Return dict of {type_name: count}."""
    rows = self.execute(f"""
        SELECT VALUE, COUNT(DISTINCT ID) as count
        FROM {table_name}
        WHERE KEY = 'Type'
        GROUP BY VALUE
        ORDER BY count DESC
    """).fetchall()
    return dict(rows)


def type_tableview(self, type_name, table_name=TABLE_NAME):
    """Create a pivoted table view. Returns DuckDBPyRelation (lazy)."""
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


def filter_triplets(self, ID=None, KEY=None, VALUE=None, INSTANCE_ID=None,
                    regex=False, table_name=TABLE_NAME):
    """Filter triplets by any combination of columns. Returns DuckDBPyRelation (lazy)."""
    conditions = []
    for col, val in [("ID", ID), ("KEY", KEY), ("VALUE", VALUE), ("INSTANCE_ID", INSTANCE_ID)]:
        if val is not None:
            if regex:
                conditions.append(f"{col} SIMILAR TO '{val}'")
            else:
                conditions.append(f"{col} = '{val}'")
    where = " AND ".join(conditions) if conditions else "TRUE"
    return self.sql(f"SELECT * FROM {table_name} WHERE {where}")


def filter_by_type(self, type_name, table_name=TABLE_NAME):
    """Filter to only objects of a specific type. Returns DuckDBPyRelation (lazy)."""
    return self.sql(f"""
        SELECT d.* FROM {table_name} d
        WHERE d.ID IN (
            SELECT ID FROM {table_name}
            WHERE KEY = 'Type' AND VALUE = '{type_name}'
        )
    """)


def references_to(self, reference_id, table_name=TABLE_NAME):
    """Find objects that reference the given ID. Returns DuckDBPyRelation (lazy)."""
    return self.sql(f"""
        SELECT d.* FROM {table_name} d
        WHERE d.ID IN (
            SELECT ID FROM {table_name}
            WHERE VALUE = '{reference_id}'
        )
    """)


def references_from(self, reference_id, table_name=TABLE_NAME):
    """Find objects referenced BY the given ID. Returns DuckDBPyRelation (lazy)."""
    return self.sql(f"""
        SELECT d.* FROM {table_name} d
        WHERE d.ID IN (
            SELECT VALUE FROM {table_name}
            WHERE ID = '{reference_id}' AND KEY != 'Type'
        )
    """)
