"""DuckDB SQL-based implementation of triplet tools.

Functions are monkey-patched onto duckdb.DuckDBPyConnection.
The *_tableview helpers create a named SQL view and return a relation over it
(named/re-queryable, still lazy); other query/reference helpers return a
DuckDBPyRelation; types_dict and get_namespace_map return python values;
triplets_to_tableviews returns a dict.

The connection holds the dataset in one table (default ``triplets``; per-
connection ``table``/``schema`` from ``duckdb.connect`` / ``set_triplets_table``).
Mutating helpers rewrite that table in place and return the connection for chaining.

Per-connection defaults live in a WeakKeyDictionary (DuckDBPyConnection has no
``__dict__``) and, once explicitly configured, in a tiny ``main."_triplets_config"``
key/value table inside the database itself — so a persisted file remembers its
table/schema across reopen, and cursors/duplicates resolve the same config.
A bare ``duckdb.connect()`` never writes anything (keeps :memory: clean);
read-only connections update the in-process config only. Resolution order:
call kwargs → in-process config → DB-stored config → package defaults; SQL
always uses double-quoted identifiers. ATTACHed extra catalogs are out of
scope — the config lives in the default catalog.
"""
from weakref import WeakKeyDictionary

_DEFAULT_TABLE = "triplets"
_DEFAULT_SCHEMA = None
_UNSET = object()              # "caller passed nothing" (None is a real schema value)
_config = WeakKeyDictionary()  # connection → (schema, table)

_CONFIG_TABLE = 'main."_triplets_config"'


def _quote(name):
    """Quote a DuckDB identifier (double quotes; escape by doubling)."""
    return '"' + str(name).replace('"', '""') + '"'


def _sql_name(schema, table):
    if schema is None:
        return _quote(table)
    return f"{_quote(schema)}.{_quote(table)}"


def _is_read_only(connection):
    row = connection.execute(
        "SELECT value FROM duckdb_settings() WHERE name = 'access_mode'").fetchone()
    return row is not None and str(row[0]).lower() == "read_only"


def _load_config(connection):
    """(schema, table) stored in the database, or None when never configured."""
    exists = connection.execute(
        "SELECT 1 FROM duckdb_tables() "
        "WHERE schema_name = 'main' AND table_name = '_triplets_config'").fetchone()
    if exists is None:
        return None
    stored = dict(connection.execute(f"SELECT key, value FROM {_CONFIG_TABLE}").fetchall())
    return stored.get("schema"), stored.get("table", _DEFAULT_TABLE)


def _store_config(connection, schema, table):
    connection.execute(f'CREATE TABLE IF NOT EXISTS {_CONFIG_TABLE} '
                       f'("key" VARCHAR PRIMARY KEY, "value" VARCHAR)')
    connection.execute(f"INSERT OR REPLACE INTO {_CONFIG_TABLE} "
                       f"VALUES ('table', ?), ('schema', ?)", [table, schema])


def _get_table(connection):
    """``(schema, table)`` for *connection*: in-process config → DB-stored →
    package defaults. The DB probe runs once per connection (cached)."""
    if connection in _config:
        return _config[connection]
    resolved = _load_config(connection) or (_DEFAULT_SCHEMA, _DEFAULT_TABLE)
    _config[connection] = resolved
    return resolved


def _set_table(connection, table=_DEFAULT_TABLE, schema=_DEFAULT_SCHEMA, persist=False):
    _config[connection] = (schema, table)
    if persist and not _is_read_only(connection):
        _store_config(connection, schema, table)


def _resolve_table(connection, table=None, schema=None, table_name=None):
    """Quoted SQL table ref: call kwargs → connection → package defaults.

    ``table_name`` is a legacy alias for a bare table name (no schema).
    """
    cfg_schema, cfg_table = _get_table(connection)
    if table is None:
        table = table_name if table_name is not None else cfg_table
    if schema is None:
        schema = cfg_schema
    return _sql_name(schema, table)


def _set_triplets_table(connection, table=_DEFAULT_TABLE, schema=_DEFAULT_SCHEMA):
    """Set this connection's default triplets table/schema (persisted in the
    database for file-backed connections). Returns the connection."""
    _set_table(connection, table=table, schema=schema, persist=True)
    return connection


def _install_connect(duckdb_module):
    """Wrap ``duckdb.connect`` so it accepts ``table=`` / ``schema=``."""
    original = duckdb_module.connect

    def connect(*args, table=_UNSET, schema=_UNSET, **kwargs):
        connection = original(*args, **kwargs)
        if table is not _UNSET or schema is not _UNSET:
            _set_table(connection,
                       table=table if table is not _UNSET else _DEFAULT_TABLE,
                       schema=schema if schema is not _UNSET else _DEFAULT_SCHEMA,
                       persist=True)
        return connection

    duckdb_module.connect = connect
    return connect


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
    # Multi-valued keys take the load-order-first value (arg_min by rowid) so the
    # single-value pick matches pandas/polars; this also makes the view deterministic.
    return _create_view(self, view_name, f"""
        WITH d AS (SELECT ID, KEY, arg_min(VALUE, rowid) AS VALUE FROM {table_name}
                   WHERE ID IN ({id_predicate}) GROUP BY ID, KEY)
        PIVOT d ON KEY IN ({in_list}) USING FIRST(VALUE) GROUP BY ID
    """)


def types_dict(self, contains=None, case_insensitive=True, table=None, schema=None, table_name=None):
    """Return dict of {type_name: count}.

    With ``contains``, keep only types whose name contains that substring
    (case-insensitive unless ``case_insensitive=False``).
    """
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    rows = self.execute(f"""
        SELECT VALUE, COUNT(DISTINCT ID) as count
        FROM {table_name}
        WHERE KEY = 'Type'
        GROUP BY VALUE
        ORDER BY count DESC
    """).fetchall()
    types_dictionary = dict(rows)

    if contains is None:
        return types_dictionary
    needle = contains.casefold() if case_insensitive else contains
    key = (lambda t: t.casefold()) if case_insensitive else (lambda t: t)
    return {t: n for t, n in types_dictionary.items() if needle in key(t)}


def type_tableview(self, type_name, table=None, schema=None, table_name=None, view_name=None):
    """Create a named SQL view pivoting all objects of a type; return a relation
    over it. The view defaults to the type name (override with view_name)."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    ids = f"SELECT DISTINCT ID FROM {table_name} WHERE KEY = 'Type' AND VALUE = {_lit(type_name)}"
    return _pivot_view(self, view_name or type_name, ids, table_name)


def filter_triplets(self, ID=None, KEY=None, VALUE=None, INSTANCE_ID=None,
                    regex=False, table=None, schema=None, table_name=None):
    """Filter triplets by any combination of columns. Returns DuckDBPyRelation (lazy).

    A list value keeps rows matching any of its values; with regex=True the
    value(s) are regex patterns matched anywhere in the value, like pandas
    str.contains.
    """
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    conditions = []
    for col, val in [("ID", ID), ("KEY", KEY), ("VALUE", VALUE), ("INSTANCE_ID", INSTANCE_ID)]:
        if val is None:
            continue
        if isinstance(val, (list, tuple, set)):
            values = list(val)
            if not values:
                conditions.append("FALSE")
            elif regex:
                conditions.append("(" + " OR ".join(f"regexp_matches({col}, {_lit(v)})" for v in values) + ")")
            else:
                conditions.append(f"{col} IN ({_in_list(values)})")
        elif regex:
            conditions.append(f"regexp_matches({col}, {_lit(val)})")
        else:
            conditions.append(f"{col} = {_lit(val)}")
    where = " AND ".join(conditions) if conditions else "TRUE"
    return self.sql(f"SELECT * FROM {table_name} WHERE {where}")


def filter_triplets_by_type(self, type_name, table=None, schema=None, table_name=None):
    """Filter to only objects of a specific type. Returns DuckDBPyRelation (lazy)."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    return self.sql(f"""
        SELECT d.* FROM {table_name} d
        WHERE d.ID IN (
            SELECT ID FROM {table_name}
            WHERE KEY = 'Type' AND VALUE = {_lit(type_name)}
        )
    """)


def filter_triplets_by_value(self, VALUE, detailed=False, type_key="Type",
                             regex=False, table=None, schema=None, table_name=None):
    """Filter to objects that have any triplet matching VALUE. Returns DuckDBPyRelation.

    Selects every object (ID) with at least one triplet whose VALUE matches
    `VALUE` (with regex=True, a regex pattern matched anywhere in the value,
    like pandas str.contains). With detailed=True, returns all their triplets;
    otherwise the matching rows plus each matched object's `type_key` row, type
    row first within each ID.
    """
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    match = f"regexp_matches(VALUE, {_lit(VALUE)})" if regex else f"VALUE = {_lit(VALUE)}"
    probe_ids = f"SELECT DISTINCT ID FROM {table_name} WHERE {match}"
    if detailed:
        return self.sql(f"SELECT * FROM {table_name} WHERE ID IN ({probe_ids})")
    return self.sql(f"""
        SELECT * FROM (
            SELECT * FROM {table_name} WHERE {match}
            UNION
            SELECT * FROM {table_name} WHERE ID IN ({probe_ids}) AND KEY = {_lit(type_key)}
        )
        ORDER BY ID, (KEY = {_lit(type_key)}) DESC
    """)


def _refs_to_sql(reference, levels, table_name):
    """SQL for references_to: object (level 0) + multi-level referrers, with
    level/ID_TO/ID_FROM — matches the pandas engine."""
    return f"""
        WITH RECURSIVE nodes(node, lvl, id_to, id_from) AS (
            SELECT {_lit(reference)}, 0, CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR)
            UNION
            SELECT t.ID, n.lvl + 1, n.node, t.ID
            FROM {table_name} t JOIN nodes n ON t.VALUE = n.node
            WHERE n.lvl < {int(levels)}
        )
        SELECT t.ID, t.KEY, t.VALUE, t.INSTANCE_ID, n.lvl AS level,
               n.id_to AS ID_TO, n.id_from AS ID_FROM, t.rowid AS _ord
        FROM nodes n JOIN {table_name} t ON t.ID = n.node
    """


def _refs_from_sql(reference, levels, table_name):
    """SQL for references_from: object (level 0) + multi-level referenced objects."""
    return f"""
        WITH RECURSIVE nodes(node, lvl, id_to, id_from) AS (
            SELECT {_lit(reference)}, 0, CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR)
            UNION
            SELECT t.VALUE, n.lvl + 1, t.VALUE, n.node
            FROM {table_name} t JOIN nodes n ON t.ID = n.node
            WHERE n.lvl < {int(levels)} AND t.VALUE IN (SELECT ID FROM {table_name})
        )
        SELECT t.ID, t.KEY, t.VALUE, t.INSTANCE_ID, n.lvl AS level,
               n.id_to AS ID_TO, n.id_from AS ID_FROM, t.rowid AS _ord
        FROM nodes n JOIN {table_name} t ON t.ID = n.node
    """


def _references_sql(reference, levels, table_name, keep_ord=False):
    """references_from + references_to, deduped on (ID,KEY,VALUE,INSTANCE_ID)
    keeping the FROM side first — matches pandas concat+drop_duplicates. _ord is
    the base-table load order (kept only when a downstream pivot needs it)."""
    drop = "_src, _rn" if keep_ord else "_src, _rn, _ord"
    return f"""
        SELECT * EXCLUDE ({drop}) FROM (
            SELECT *, row_number() OVER (PARTITION BY ID, KEY, VALUE, INSTANCE_ID ORDER BY _src, _ord) AS _rn
            FROM (
                SELECT *, 0 AS _src FROM ({_refs_from_sql(reference, levels, table_name)})
                UNION ALL
                SELECT *, 1 AS _src FROM ({_refs_to_sql(reference, levels, table_name)})
            )
        ) WHERE _rn = 1
    """


def references_to(self, reference, levels=1, table=None, schema=None, table_name=None):
    """Objects that reference the given ID, multi-level. Returns DuckDBPyRelation."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    return self.sql(f"SELECT * EXCLUDE (_ord) FROM ({_refs_to_sql(reference, levels, table_name)})")


def references_from(self, reference, levels=1, table=None, schema=None, table_name=None):
    """Objects referenced BY the given ID, multi-level. Returns DuckDBPyRelation."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    return self.sql(f"SELECT * EXCLUDE (_ord) FROM ({_refs_from_sql(reference, levels, table_name)})")


# ── Query / view ─────────────────────────────────────────────────────────────

def key_tableview(self, key, table=None, schema=None, table_name=None, view_name=None):
    """Create a named SQL view pivoting objects carrying a given KEY; return a
    relation over it. The view defaults to the key name (override with view_name)."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    ids = f"SELECT DISTINCT ID FROM {table_name} WHERE KEY = {_lit(key)}"
    return _pivot_view(self, view_name or key, ids, table_name)


def id_tableview(self, id, table=None, schema=None, table_name=None, view_name=None):
    """Create a named SQL view pivoting the given ID(s) — a single id or an
    iterable — and return a relation over it. The view defaults to the id when a
    single one is given, else 'id_tableview' (override with view_name)."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    ids = [id] if isinstance(id, str) else list(id)
    if view_name is None:
        view_name = ids[0] if len(ids) == 1 else "id_tableview"
    return _pivot_view(self, view_name, _in_list(ids), table_name)


def get_object_data(self, object_UUID, table=None, schema=None, table_name=None):
    """All (KEY, VALUE) rows for one object. Returns DuckDBPyRelation (lazy)."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    return self.sql(f"SELECT KEY, VALUE FROM {table_name} WHERE ID = {_lit(object_UUID)}")


def get_namespace_map(self, table=None, schema=None, table_name=None):
    """Return (namespace_map dict, xml_base) from the NamespaceMap object."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    rows = self.execute(f"""
        SELECT KEY, VALUE FROM {table_name}
        WHERE KEY != 'Type' AND ID IN (
            SELECT ID FROM {table_name} WHERE KEY = 'Type' AND VALUE = 'NamespaceMap'
        )
    """).fetchall()
    namespace_map = dict(rows)
    xml_base = namespace_map.pop("xml_base", "")
    return namespace_map, xml_base


def triplets_to_tableviews(self, table=None, schema=None, table_name=None):
    """Return {type_name: tableview relation} for every type in the dataset."""
    return {name: type_tableview(self, name, table=table, schema=schema, table_name=table_name)
            for name in types_dict(self, table=table, schema=schema, table_name=table_name)}


# ── References ───────────────────────────────────────────────────────────────

def references(self, ID, levels=1, table=None, schema=None, table_name=None):
    """All references to and from an object (both directions). Returns DuckDBPyRelation."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    return self.sql(_references_sql(ID, levels, table_name))


def _pivot_refs(self, refs_sql, index, columns):
    """Pivot a references query (carrying _ord) on KEY, indexed by `index`
    (ID_FROM/ID_TO/ID). Multi-valued keys take the load-order-first value
    (arg_min by _ord) so the pick matches pandas/polars; keep only `columns`."""
    pivoted = self.sql(f"""
        PIVOT (SELECT {index}, KEY, arg_min(VALUE, _ord) AS VALUE
               FROM ({refs_sql}) GROUP BY {index}, KEY)
        ON KEY USING FIRST(VALUE) GROUP BY {index}
    """)
    keep = [index] + [c for c in (columns or []) if c in pivoted.columns]
    return pivoted.select(", ".join(f'"{c}"' for c in keep))


def references_to_simple(self, reference, columns=["Type"], table=None, schema=None, table_name=None):
    """Pivot of objects referencing `reference` (index ID_FROM), limited to `columns`."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    return _pivot_refs(self, _refs_to_sql(reference, 1, table_name), "ID_FROM", columns)


def references_from_simple(self, reference, columns=["Type"], table=None, schema=None, table_name=None):
    """Pivot of objects referenced BY `reference` (index ID_TO), limited to `columns`."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    return _pivot_refs(self, _refs_from_sql(reference, 1, table_name), "ID_TO", columns)


def references_simple(self, reference, columns=None, levels=1, table=None, schema=None, table_name=None):
    """Pivot of the object and everything linked to/from it (index ID), with the
    level/ID_FROM/ID_TO metadata merged back, matching pandas."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    refs_sql = _references_sql(reference, levels, table_name, keep_ord=True)
    pivoted = self.sql(f"""PIVOT (SELECT ID, KEY, arg_min(VALUE, _ord) AS VALUE
                                  FROM ({refs_sql}) GROUP BY ID, KEY)
                           ON KEY USING FIRST(VALUE) GROUP BY ID""")
    if columns is None:
        columns = [c for c in ("Type", "IdentifiedObject.name") if c in pivoted.columns]
    keep = [c for c in columns if c in pivoted.columns]
    sel = "".join(f', p."{c}"' for c in keep)
    return self.sql(f"""
        WITH refs AS ({refs_sql}),
        p AS (PIVOT (SELECT ID, KEY, arg_min(VALUE, _ord) AS VALUE FROM refs GROUP BY ID, KEY)
              ON KEY USING FIRST(VALUE) GROUP BY ID),
        m AS (SELECT ID, ANY_VALUE(level) AS level, ANY_VALUE(ID_FROM) AS ID_FROM,
                     ANY_VALUE(ID_TO) AS ID_TO FROM refs GROUP BY ID)
        SELECT p.ID{sel}, m.level, m.ID_FROM, m.ID_TO
        FROM p LEFT JOIN m ON p.ID = m.ID
        ORDER BY m.level
    """)


def references_all(self, table=None, schema=None, table_name=None):
    """All reference links as (ID_FROM, KEY, ID_TO). Returns DuckDBPyRelation."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    return self.sql(f"""
        SELECT DISTINCT a.ID AS ID_FROM, a.KEY, a.VALUE AS ID_TO
        FROM {table_name} a
        JOIN (SELECT DISTINCT ID FROM {table_name}) b ON a.VALUE = b.ID
    """)


# ── Filter ───────────────────────────────────────────────────────────────────

def filter_triplets_by_triplets(self, filter_triplet, table=None, schema=None, table_name=None):
    """Keep triplets whose ID appears in `filter_triplet`. Returns DuckDBPyRelation."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    _materialize(self, filter_triplet, "_filter_triplet")
    return self.sql(f"""
        SELECT * FROM {table_name}
        WHERE ID IN (SELECT DISTINCT ID FROM _filter_triplet)
    """)


# ── Mutate (rewrite the triplets table in place, return self for chaining) ─────

def set_value_at_key(self, key, value, table=None, schema=None, table_name=None):
    """Set VALUE for every row with the given KEY. Mutates the table; returns self."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    self.execute(f"""
        CREATE OR REPLACE TABLE {table_name} AS
        SELECT ID, KEY,
               CASE WHEN KEY = {_lit(key)} THEN {_lit(value)} ELSE VALUE END AS VALUE,
               INSTANCE_ID
        FROM {table_name}
    """)
    return self


def set_value_at_key_and_id(self, key, value, id, table=None, schema=None, table_name=None):
    """Set VALUE for the row with the given KEY and ID. Mutates the table; returns self."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
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
    into table_name. Merge keys are ID+KEY, plus INSTANCE_ID when has_instance.

    Like pandas, update overwrites only VALUE on matched rows (preserving their
    original INSTANCE_ID); add appends rows with no existing match.
    """
    if not update and not add:
        return self
    on = "u.ID = t.ID AND u.KEY = t.KEY" + (" AND u.INSTANCE_ID = t.INSTANCE_ID" if has_instance else "")
    if update:
        base = (f"SELECT t.ID, t.KEY, COALESCE(u.VALUE, t.VALUE) AS VALUE, t.INSTANCE_ID "
                f"FROM {table_name} t LEFT JOIN _update_data u ON {on}")
    else:
        base = f"SELECT * FROM {table_name}"
    addition = ("" if not add else
                f" UNION ALL BY NAME SELECT u.ID, u.KEY, u.VALUE, u.INSTANCE_ID FROM _update_data u "
                f"WHERE NOT EXISTS (SELECT 1 FROM {table_name} t WHERE {on})")
    self.execute(f"CREATE OR REPLACE TABLE {table_name} AS {base}{addition}")
    return self


def update_triplets_from_triplets(self, update_data, update=True, add=True, table=None, schema=None, table_name=None):
    """Update existing and/or add new rows from another triplet dataset. Merges on
    ID+KEY (plus INSTANCE_ID when update_data has it). Mutates the table; returns self."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
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
                                   table=None, schema=None, table_name=None):
    """Unpivot a tableview to triplets, then update/add them. When instance_id is
    None the merge is on ID+KEY only (mirrors the pandas engine). Mutates; returns self."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
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
                                  table=None, schema=None, table_name=None):
    """Remove rows matching `what_triplet` on `columns`. Mutates the table; returns self."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    _materialize(self, what_triplet, "_what_triplet")
    on = " AND ".join(f"t.{c} = w.{c}" for c in columns)
    self.execute(f"""
        CREATE OR REPLACE TABLE {table_name} AS
        SELECT t.* FROM {table_name} t
        WHERE NOT EXISTS (SELECT 1 FROM _what_triplet w WHERE {on})
    """)
    return self


# ── Diff ─────────────────────────────────────────────────────────────────────

def diff_triplets(self, new_data, table=None, schema=None, table_name=None):
    """Rows unique to the table (left_only) or to new_data (right_only), with a
    _merge column. Returns DuckDBPyRelation."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    _materialize(self, new_data, "_new_data")
    return self.sql(f"""
        SELECT ID, KEY, VALUE, INSTANCE_ID AS INSTANCE_ID_OLD, NULL AS INSTANCE_ID_NEW,
               'left_only' AS _merge
        FROM {table_name}
        WHERE (ID, KEY, VALUE) NOT IN (SELECT ID, KEY, VALUE FROM _new_data)
        UNION ALL BY NAME
        SELECT ID, KEY, VALUE, NULL AS INSTANCE_ID_OLD, INSTANCE_ID AS INSTANCE_ID_NEW,
               'right_only' AS _merge
        FROM _new_data
        WHERE (ID, KEY, VALUE) NOT IN (SELECT ID, KEY, VALUE FROM {table_name})
    """)


def diff_triplets_by_instance(self, INSTANCE_ID_1, INSTANCE_ID_2, table=None, schema=None, table_name=None):
    """Triplets that differ between two instances in the table. Returns DuckDBPyRelation."""
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    scope = f"INSTANCE_ID IN ({_lit(INSTANCE_ID_1)}, {_lit(INSTANCE_ID_2)})"
    return self.sql(f"""
        SELECT * FROM {table_name}
        WHERE {scope} AND (ID, KEY, VALUE) IN (
            SELECT ID, KEY, VALUE FROM {table_name}
            WHERE {scope} GROUP BY ID, KEY, VALUE HAVING COUNT(*) = 1
        )
    """)


def print_triplets_diff(self, new_data, file_id_object="Distribution", file_id_key="label",
                        exclude_objects=None, table=None, schema=None, table_name=None):
    """Print a simple removed/added diff of the table against new_data."""
    diff = diff_triplets(self, new_data, table=table, schema=schema, table_name=table_name).df()
    removed = diff[diff["_merge"] == "left_only"]
    added = diff[diff["_merge"] == "right_only"]
    print(f"--- removed ({len(removed)} triplets) / +++ added ({len(added)} triplets) ---")
    for _, row in removed.iterrows():
        print(f"- {row['ID']} {row['KEY']} {row['VALUE']}")
    for _, row in added.iterrows():
        print(f"+ {row['ID']} {row['KEY']} {row['VALUE']}")


# ── Transform ────────────────────────────────────────────────────────────────

def tableview_to_triplets(self, table=None, schema=None, table_name=None, multivalue=False, instance_id=None):
    """Unpivot a *wide tableview* table back to triplets (ID, KEY, VALUE).

    Point ``table_name`` at a tableview table (the default ``triplets`` table is
    already long-form). Empty cells (NULL VALUE) are dropped — not real triplets.
    Pass ``instance_id`` to stamp an ``INSTANCE_ID`` column, the same way
    ``update_triplets_from_tableview`` does. Returns DuckDBPyRelation.
    """
    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    instance_col = f", {_lit(instance_id)} AS INSTANCE_ID" if instance_id is not None else ""
    return self.sql(f"""
        SELECT ID, KEY, VALUE{instance_col} FROM (
            UNPIVOT {table_name} ON COLUMNS(* EXCLUDE (ID)) INTO NAME KEY VALUE VALUE
        ) WHERE VALUE IS NOT NULL
    """)


def content_hash(self, ignore_types=("Distribution", "NamespaceMap", "FullModel"),
                 columns=("ID", "KEY", "VALUE"), order_sensitive=False,
                 table=None, schema=None, table_name=None):
    """Deterministic identity hash of the triplet content (blake2b hex) —
    row-order-invariant by default via in-database hash() combined with
    streaming count/sum/xor aggregates (no sort, no materialization — works
    larger-than-memory). With ``order_sensitive=True`` each row's position
    (insertion order via rowid) is mixed into its hash, so the same rows
    inserted in a different order produce a different digest. Engine-specific:
    not comparable across engines (see pandas_engine)."""
    import hashlib

    table_name = _resolve_table(self, table=table, schema=schema, table_name=table_name)
    # no coalesce: hash() distinguishes NULL from '' natively — coalescing
    # to '' would collide a missing VALUE with an empty one
    hashed = ", ".join(f"CAST({column} AS VARCHAR)" for column in columns)
    if order_sensitive:
        hashed = f"row_number() OVER (ORDER BY rowid), {hashed}"
    source = f"SELECT hash({hashed}) AS h FROM {table_name}"
    if ignore_types:
        source += (f" WHERE ID NOT IN (SELECT ID FROM {table_name}"
                   f" WHERE KEY = 'Type' AND VALUE IN ({_in_list(ignore_types)}))")
    row = self.execute(f"SELECT count(*), sum(h)::VARCHAR, bit_xor(h)::VARCHAR"
                       f" FROM ({source})").fetchone()
    return hashlib.blake2b(repr(row).encode(), digest_size=16).hexdigest()
