"""DuckDB SQL-based implementation of triplet tools.

Functions are monkey-patched onto duckdb.DuckDBPyConnection.
The *_tableview helpers create a named SQL view and return a relation over it
(named/re-queryable, still lazy); other query/reference helpers return a
DuckDBPyRelation; types_dict and get_namespace_map return python values;
triplets_to_tableviews returns a dict.

The connection holds the whole dataset in a single ``triplets`` table, so the
mutating helpers (set_value_at_key, update_*, remove_*) rewrite that table in
place with CREATE OR REPLACE TABLE and return ``self`` — this keeps chained
calls (con.set_value_at_key(...).type_tableview(...)) operating on the updated
state, unlike the pandas/polars engines which return new frames.
"""

TABLE_NAME = "triplets"


def _lit(value):
    """SQL literal: NULL for None, single-quoted with quotes escaped otherwise."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _in_list(values):
    """Comma-separated SQL literal list from an iterable of values."""
    return ", ".join(_lit(v) for v in values)


def _materialize(self, data, name):
    """Copy an external triplet dataset (pandas DataFrame / relation) into a temp
    table so later SQL is independent of the python object's lifetime."""
    self.register(f"_reg_{name}", data)
    self.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM _reg_{name}")
    self.unregister(f"_reg_{name}")


def _create_view(self, view_name, query):
    """Create (or replace) a named SQL view and return a relation over it.

    The view is named and re-queryable (SELECT * FROM "<view_name>") and stays
    lazy — it reflects later changes to the underlying triplets table.
    """
    safe = view_name.replace('"', '""')
    self.execute(f'CREATE OR REPLACE VIEW "{safe}" AS {query}')
    return self.sql(f'SELECT * FROM "{safe}"')


def _pivot_view(self, view_name, id_predicate, table_name):
    """Create a named view pivoting the triplets of the IDs selected by
    id_predicate (a subquery or literal list usable inside ``ID IN (...)``).

    PIVOT inside a view needs explicit columns, so the distinct KEYs are resolved
    up front: the column set is fixed at creation time, while the values stay lazy.
    """
    keys = [row[0] for row in self.execute(
        f"SELECT DISTINCT KEY FROM {table_name} WHERE ID IN ({id_predicate})").fetchall()]
    if not keys:
        return _create_view(self, view_name, f"SELECT ID FROM {table_name} WHERE ID IN ({id_predicate})")
    in_list = ", ".join(_lit(k) for k in keys)
    return _create_view(self, view_name, f"""
        WITH d AS (SELECT ID, KEY, VALUE FROM {table_name} WHERE ID IN ({id_predicate}))
        PIVOT d ON KEY IN ({in_list}) USING FIRST(VALUE) GROUP BY ID
    """)


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


def type_tableview(self, type_name, table_name=TABLE_NAME, view_name=None):
    """Create a named SQL view pivoting all objects of a type; return a relation
    over it. The view defaults to the type name (override with view_name)."""
    ids = f"SELECT DISTINCT ID FROM {table_name} WHERE KEY = 'Type' AND VALUE = {_lit(type_name)}"
    return _pivot_view(self, view_name or type_name, ids, table_name)


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


def filter_triplets_by_type(self, type_name, table_name=TABLE_NAME):
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


# ── Query / view ─────────────────────────────────────────────────────────────

def key_tableview(self, key, table_name=TABLE_NAME, view_name=None):
    """Create a named SQL view pivoting objects carrying a given KEY; return a
    relation over it. The view defaults to the key name (override with view_name)."""
    ids = f"SELECT DISTINCT ID FROM {table_name} WHERE KEY = {_lit(key)}"
    return _pivot_view(self, view_name or key, ids, table_name)


def id_tableview(self, id, table_name=TABLE_NAME, view_name=None):
    """Create a named SQL view pivoting the given ID(s) — a single id or an
    iterable — and return a relation over it. The view defaults to the id when a
    single one is given, else 'id_tableview' (override with view_name)."""
    ids = [id] if isinstance(id, str) else list(id)
    if view_name is None:
        view_name = ids[0] if len(ids) == 1 else "id_tableview"
    return _pivot_view(self, view_name, _in_list(ids), table_name)


def get_object_data(self, object_UUID, table_name=TABLE_NAME):
    """All (KEY, VALUE) rows for one object. Returns DuckDBPyRelation (lazy)."""
    return self.sql(f"SELECT KEY, VALUE FROM {table_name} WHERE ID = {_lit(object_UUID)}")


def get_namespace_map(self, table_name=TABLE_NAME):
    """Return (namespace_map dict, xml_base) from the NamespaceMap object."""
    rows = self.execute(f"""
        SELECT KEY, VALUE FROM {table_name}
        WHERE KEY != 'Type' AND ID IN (
            SELECT ID FROM {table_name} WHERE KEY = 'Type' AND VALUE = 'NamespaceMap'
        )
    """).fetchall()
    namespace_map = dict(rows)
    xml_base = namespace_map.pop("xml_base", "")
    return namespace_map, xml_base


def triplets_to_tableviews(self, table_name=TABLE_NAME):
    """Return {type_name: tableview relation} for every type in the dataset."""
    return {name: type_tableview(self, name, table_name=table_name)
            for name in types_dict(self, table_name=table_name)}


# ── References ───────────────────────────────────────────────────────────────

def _pivot_ids(self, id_query, columns=None, table_name=TABLE_NAME):
    """Pivot the triplets of the IDs produced by id_query; optionally keep columns."""
    relation = self.sql(f"""
        WITH ids AS ({id_query}),
        d AS (SELECT t.ID, t.KEY, t.VALUE FROM {table_name} t JOIN ids ON t.ID = ids.ID)
        PIVOT d ON KEY USING FIRST(VALUE) GROUP BY ID
    """)
    if columns is None:
        return relation
    keep = ["ID"] + [c for c in columns if c in relation.columns]
    return relation.select(", ".join(f'"{c}"' for c in keep))


def references_to_simple(self, reference, columns=["Type"], table_name=TABLE_NAME):
    """Pivot of objects that reference `reference`, limited to `columns`."""
    return _pivot_ids(self, f"SELECT DISTINCT ID FROM {table_name} WHERE VALUE = {_lit(reference)}",
                      columns=columns, table_name=table_name)


def references_from_simple(self, reference, columns=["Type"], table_name=TABLE_NAME):
    """Pivot of objects referenced BY `reference`, limited to `columns`."""
    return _pivot_ids(self, f"SELECT DISTINCT VALUE AS ID FROM {table_name} "
                            f"WHERE ID = {_lit(reference)} AND KEY != 'Type'",
                      columns=columns, table_name=table_name)


def references(self, ID, levels=1, table_name=TABLE_NAME):
    """All triplets of the object plus objects linked to/from it (single level)."""
    return self.sql(f"""
        SELECT d.* FROM {table_name} d
        WHERE d.ID = {_lit(ID)}
           OR d.ID IN (SELECT ID FROM {table_name} WHERE VALUE = {_lit(ID)})
           OR d.ID IN (SELECT VALUE FROM {table_name} WHERE ID = {_lit(ID)} AND KEY != 'Type')
    """)


def references_simple(self, reference, columns=None, levels=1, table_name=TABLE_NAME):
    """Pivot of the object and everything linked to/from it. Defaults to keeping
    Type and IdentifiedObject.name when present."""
    id_query = f"""
        SELECT {_lit(reference)} AS ID
        UNION SELECT ID FROM {table_name} WHERE VALUE = {_lit(reference)}
        UNION SELECT VALUE FROM {table_name} WHERE ID = {_lit(reference)} AND KEY != 'Type'
    """
    relation = _pivot_ids(self, id_query, columns=None, table_name=table_name)
    if columns is None:
        columns = [c for c in ("Type", "IdentifiedObject.name") if c in relation.columns]
    keep = ["ID"] + [c for c in columns if c in relation.columns]
    return relation.select(", ".join(f'"{c}"' for c in keep))


def references_all(self, table_name=TABLE_NAME):
    """All reference links as (ID_FROM, KEY, ID_TO). Returns DuckDBPyRelation."""
    return self.sql(f"""
        SELECT DISTINCT a.ID AS ID_FROM, a.KEY, a.VALUE AS ID_TO
        FROM {table_name} a
        JOIN (SELECT DISTINCT ID FROM {table_name}) b ON a.VALUE = b.ID
    """)


# ── Filter ───────────────────────────────────────────────────────────────────

def filter_triplets_by_triplets(self, filter_triplet, table_name=TABLE_NAME):
    """Keep triplets whose ID appears in `filter_triplet`. Returns DuckDBPyRelation."""
    _materialize(self, filter_triplet, "_filter_triplet")
    return self.sql(f"""
        SELECT * FROM {table_name}
        WHERE ID IN (SELECT DISTINCT ID FROM _filter_triplet)
    """)


# ── Mutate (rewrite the triplets table in place, return self for chaining) ─────

def set_value_at_key(self, key, value, table_name=TABLE_NAME):
    """Set VALUE for every row with the given KEY. Mutates the table; returns self."""
    self.execute(f"""
        CREATE OR REPLACE TABLE {table_name} AS
        SELECT ID, KEY,
               CASE WHEN KEY = {_lit(key)} THEN {_lit(value)} ELSE VALUE END AS VALUE,
               INSTANCE_ID
        FROM {table_name}
    """)
    return self


def set_value_at_key_and_id(self, key, value, id, table_name=TABLE_NAME):
    """Set VALUE for the row with the given KEY and ID. Mutates the table; returns self."""
    self.execute(f"""
        CREATE OR REPLACE TABLE {table_name} AS
        SELECT ID, KEY,
               CASE WHEN KEY = {_lit(key)} AND ID = {_lit(id)} THEN {_lit(value)} ELSE VALUE END AS VALUE,
               INSTANCE_ID
        FROM {table_name}
    """)
    return self


def _apply_update(self, has_instance, update, add, table_name):
    """Merge the normalized TEMP TABLE _update_data (ID, KEY, VALUE, INSTANCE_ID)
    into table_name. Merge keys are ID+KEY, plus INSTANCE_ID when has_instance."""
    on = "u.ID = t.ID AND u.KEY = t.KEY" + (" AND u.INSTANCE_ID = t.INSTANCE_ID" if has_instance else "")
    kept = (f"SELECT t.* FROM {table_name} t "
            f"WHERE NOT EXISTS (SELECT 1 FROM _update_data u WHERE {on})")
    if update and add:
        new_table = f"{kept} UNION ALL BY NAME SELECT * FROM _update_data"
    elif update:  # replace matches only — no brand-new rows
        new_table = (f"{kept} UNION ALL BY NAME SELECT u.* FROM _update_data u "
                     f"WHERE EXISTS (SELECT 1 FROM {table_name} t WHERE {on})")
    elif add:  # keep table untouched, append only rows with no existing match
        new_table = (f"SELECT * FROM {table_name} "
                     f"UNION ALL BY NAME SELECT u.* FROM _update_data u "
                     f"WHERE NOT EXISTS (SELECT 1 FROM {table_name} t WHERE {on})")
    else:
        return self
    self.execute(f"CREATE OR REPLACE TABLE {table_name} AS {new_table}")
    return self


def update_triplets_from_triplets(self, update_data, update=True, add=True, table_name=TABLE_NAME):
    """Update existing and/or add new rows from another triplet dataset. Merges on
    ID+KEY (plus INSTANCE_ID when update_data has it). Mutates the table; returns self."""
    self.register("_reg_update", update_data)
    has_instance = "INSTANCE_ID" in self.sql("SELECT * FROM _reg_update").columns
    instance_expr = "INSTANCE_ID" if has_instance else "NULL"
    self.execute(f"""
        CREATE OR REPLACE TEMP TABLE _update_data AS
        SELECT ID, KEY, VALUE, {instance_expr} AS INSTANCE_ID FROM _reg_update
    """)
    self.unregister("_reg_update")
    return _apply_update(self, has_instance, update, add, table_name)


def update_triplets_from_tableview(self, tableview, update=True, add=True, instance_id=None,
                                   table_name=TABLE_NAME):
    """Unpivot a tableview to triplets, then update/add them. When instance_id is
    None the merge is on ID+KEY only (mirrors the pandas engine). Mutates; returns self."""
    self.register("_reg_tv", tableview)
    has_instance = instance_id is not None
    instance_expr = _lit(instance_id) if has_instance else "NULL"
    self.execute(f"""
        CREATE OR REPLACE TEMP TABLE _update_data AS
        SELECT ID, KEY, VALUE, {instance_expr} AS INSTANCE_ID
        FROM (UNPIVOT _reg_tv ON COLUMNS(* EXCLUDE (ID)) INTO NAME KEY VALUE VALUE)
        WHERE VALUE IS NOT NULL
    """)
    self.unregister("_reg_tv")
    return _apply_update(self, has_instance, update, add, table_name)


def remove_triplets_from_triplets(self, what_triplet, columns=["ID", "KEY", "VALUE"],
                                  table_name=TABLE_NAME):
    """Remove rows matching `what_triplet` on `columns`. Mutates the table; returns self."""
    _materialize(self, what_triplet, "_what_triplet")
    on = " AND ".join(f"t.{c} = w.{c}" for c in columns)
    self.execute(f"""
        CREATE OR REPLACE TABLE {table_name} AS
        SELECT t.* FROM {table_name} t
        WHERE NOT EXISTS (SELECT 1 FROM _what_triplet w WHERE {on})
    """)
    return self


# ── Diff ─────────────────────────────────────────────────────────────────────

def diff_triplets(self, new_data, table_name=TABLE_NAME):
    """Rows unique to the table (left_only) or to new_data (right_only), with a
    _merge column. Returns DuckDBPyRelation."""
    _materialize(self, new_data, "_new_data")
    return self.sql(f"""
        SELECT *, 'left_only' AS _merge FROM {table_name}
        WHERE (ID, KEY, VALUE) NOT IN (SELECT ID, KEY, VALUE FROM _new_data)
        UNION ALL BY NAME
        SELECT *, 'right_only' AS _merge FROM _new_data
        WHERE (ID, KEY, VALUE) NOT IN (SELECT ID, KEY, VALUE FROM {table_name})
    """)


def diff_triplets_by_instance(self, INSTANCE_ID_1, INSTANCE_ID_2, table_name=TABLE_NAME):
    """Triplets that differ between two instances in the table. Returns DuckDBPyRelation."""
    scope = f"INSTANCE_ID IN ({_lit(INSTANCE_ID_1)}, {_lit(INSTANCE_ID_2)})"
    return self.sql(f"""
        SELECT * FROM {table_name}
        WHERE {scope} AND (ID, KEY, VALUE) IN (
            SELECT ID, KEY, VALUE FROM {table_name}
            WHERE {scope} GROUP BY ID, KEY, VALUE HAVING COUNT(*) = 1
        )
    """)


def print_triplets_diff(self, new_data, file_id_object="Distribution", file_id_key="label",
                        exclude_objects=None, table_name=TABLE_NAME):
    """Print a simple removed/added diff of the table against new_data."""
    diff = diff_triplets(self, new_data, table_name=table_name).df()
    removed = diff[diff["_merge"] == "left_only"]
    added = diff[diff["_merge"] == "right_only"]
    print(f"--- removed ({len(removed)} triplets) / +++ added ({len(added)} triplets) ---")
    for _, row in removed.iterrows():
        print(f"- {row['ID']} {row['KEY']} {row['VALUE']}")
    for _, row in added.iterrows():
        print(f"+ {row['ID']} {row['KEY']} {row['VALUE']}")


# ── Transform ────────────────────────────────────────────────────────────────

def tableview_to_triplets(self, table_name=TABLE_NAME, multivalue=False):
    """Unpivot a *wide tableview* table back to triplets (ID, KEY, VALUE).

    Point ``table_name`` at a tableview table (the default ``triplets`` table is
    already long-form). Returns DuckDBPyRelation.
    """
    return self.sql(f"""
        SELECT ID, KEY, VALUE FROM (
            UNPIVOT {table_name} ON COLUMNS(* EXCLUDE (ID)) INTO NAME KEY VALUE VALUE
        ) WHERE VALUE IS NOT NULL
    """)
