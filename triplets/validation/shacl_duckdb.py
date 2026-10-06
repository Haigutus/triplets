"""SHACL DuckDB engine — compiled-IR executor for larger-than-memory data.

Every constraint compiles to one SQL query against the connection's configured triplets table
(``[ID, KEY, VALUE, INSTANCE_ID]``), so validation streams through DuckDB's
vectorized executor and spills to disk instead of requiring the dataset in
RAM. Input is a DuckDB connection holding the table (``con.read_rdf(...)``);
any other flavor (pandas/polars/arrow) is registered into an in-memory
connection, which makes the engine uniformly testable.

Semantics are identical to the pandas/polars engines (same IR, same canonical
violations schema, same lexical-form datatype deviation). The logical
components (sh:and, sh:or, sh:not, sh:xone) compose their nested rows' SQL;
nested rows judge the parent's focus, or an explicit ID list bound through
``UNNEST(?)`` (sh:node: the referenced value nodes, resolved with one query).
sh:sparql and SPARQL targets run through shacl_sparql on one materialized copy
of the (scoped) table — an rdflib/SPARQL store needs the data anyway.

Explicitly selected (``engine="duckdb"``), not in the auto order: polars owns
the in-memory fast path; this engine is the deliberate choice when the data
does not fit.
"""
import functools
import logging

from types import SimpleNamespace

import pandas

from .._engine_detect import flavor
from . import shacl_sparql
from .shacl_ir import split_rules
from .shacl_report import VIOLATION_COLUMNS
from ..iri import CIM_NS, REFERENCE_LIKE, TYPE_KEY, iri_duckdb, load_rdf_map, node_kind, value_types
from .shacl_pandas import DATATYPES

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100  # constraints per UNION ALL statement


def _class_sql(table, context):
    """Instances of a class (bound with one class-name parameter).

    ``context.types_table`` is a precomputed (ID, VALUE) relation that already
    includes ancestor names when rdf_map was passed; otherwise Type rows.
    """
    types_table = getattr(context, "types_table", None)
    if types_table is not None:
        return f"SELECT ID FROM {types_table} WHERE VALUE = ?"
    return f"SELECT ID FROM {table} WHERE KEY = '{TYPE_KEY}' AND VALUE = ?"


def _focus_param(rule, context):
    """The one parameter _focus_sql binds: an ID list (nested override or SPARQL
    target), else the target class / KEY / node."""
    if getattr(rule, "focus_ids", None) is not None:
        return list(rule.focus_ids)
    if getattr(rule, "target_kind", "class") == "sparql":
        return context.sparql_targets(rule.target_class)
    return rule.target_class


def _focus_sql(rule, table, context):
    """The rule's focus nodes (bound with the one _focus_param parameter)."""
    kind = getattr(rule, "target_kind", "class")
    if getattr(rule, "focus_ids", None) is not None or kind == "sparql":
        return "SELECT UNNEST(CAST(? AS VARCHAR[])) AS ID"
    if kind == "subjectsOf":
        return f"SELECT DISTINCT ID FROM {table} WHERE KEY = ?"
    if kind == "objectsOf":
        return f"SELECT DISTINCT VALUE AS ID FROM {table} WHERE KEY = ?"
    if kind == "node":
        return "SELECT ? AS ID"
    return _class_sql(table, context)


def _rows_sql(rule, table, context):
    """The rule's path as (FOCUS, PV) rows — inverse- and via_type-aware, like
    the other engines (via_type: PV = the referenced object's type; a target
    without a Type row yields no value node, hence the inner join)."""
    if rule.inverse:
        sql = (f"SELECT VALUE AS FOCUS, ID AS PV FROM {table} "
               f"WHERE KEY = ? AND VALUE IN ({_focus_sql(rule, table, context)})")
    else:
        sql = (f"SELECT ID AS FOCUS, VALUE AS PV FROM {table} "
               f"WHERE KEY = ? AND ID IN ({_focus_sql(rule, table, context)})")
    if getattr(rule, "via_type", False):
        sql = (f"SELECT r.FOCUS AS FOCUS, t.VALUE AS PV FROM ({sql}) r "
               f"JOIN {table} t ON t.ID = r.PV AND t.KEY = '{TYPE_KEY}'")
    return sql, [rule.path, _focus_param(rule, context)]


def _wrap(rule, message, from_sql, from_params, where_sql, where_params, value_expr="PV"):
    """Canonical violation SELECT around a (FOCUS, PV) source and a violation condition.

    Parameter order follows placeholder order in the SQL text:
    constants (select list) → source subquery → WHERE condition.
    """
    sql = (f"SELECT FOCUS AS ID, ? AS KEY, {value_expr} AS VALUE, ? AS VIOLATION_TYPE, "
           f"? AS MESSAGE, ? AS SEVERITY, ? AS SOURCE_SHAPE FROM ({from_sql}) WHERE {where_sql}")
    constants = [rule.path, rule.component, rule.message or message, rule.severity, rule.shape_id]
    return sql, constants + list(from_params) + list(where_params)


_NULL_VALUE = "CAST(NULL AS VARCHAR)"


# ── SQL builders: (rule, table, context) → (sql, params) ─────────────────────

def _min_count(rule, table, context):
    rows, rows_params = _rows_sql(rule, table, context)
    from_sql = (f"SELECT f.ID AS FOCUS FROM ({_focus_sql(rule, table, context)}) f "
                f"LEFT JOIN (SELECT FOCUS, COUNT(*) AS n FROM ({rows}) GROUP BY FOCUS) c "
                f"ON f.ID = c.FOCUS WHERE COALESCE(c.n, 0) < ?")
    return _wrap(rule, f"{rule.path} occurs fewer than {rule.params} time(s)",
                 from_sql, [_focus_param(rule, context), *rows_params, rule.params], "TRUE", [],
                 value_expr=_NULL_VALUE)


def _max_count(rule, table, context):
    rows, rows_params = _rows_sql(rule, table, context)
    from_sql = f"SELECT FOCUS FROM ({rows}) GROUP BY FOCUS HAVING COUNT(*) > ?"
    return _wrap(rule, f"{rule.path} occurs more than {rule.params} time(s)",
                 from_sql, [*rows_params, rule.params], "TRUE", [], value_expr=_NULL_VALUE)


def _datatype(rule, table, context):
    """Two-level lexical check (same deviation as the other vectorized engines)."""
    spec = DATATYPES.get(str(rule.params).removeprefix("xsd:"))
    if spec is None:
        return None
    valid_pattern, warn_pattern = spec
    rows, rows_params = _rows_sql(rule, table, context)

    warn_sql = "regexp_full_match(PV, ?)" if warn_pattern else "FALSE"
    warn_params = [warn_pattern] if warn_pattern else []
    sql = (f"SELECT FOCUS AS ID, ? AS KEY, PV AS VALUE, "
           f"CASE WHEN inv THEN 'sh:datatype' ELSE 'triplets:lexicalForm' END AS VIOLATION_TYPE, "
           f"CASE WHEN inv THEN ? ELSE ? END AS MESSAGE, "
           f"CASE WHEN inv THEN ? ELSE 'Warning' END AS SEVERITY, "
           f"? AS SOURCE_SHAPE "
           f"FROM (SELECT FOCUS, PV, NOT regexp_full_match(PV, ?) AS inv, {warn_sql} AS wrn "
           f"      FROM ({rows})) WHERE inv OR wrn")
    params = [rule.path,
              rule.message or f"value is not a valid {rule.params}",
              f"lexical form is narrower than the declared {rule.params} (e.g. integer form for a float)",
              rule.severity, rule.shape_id,
              valid_pattern, *warn_params, *rows_params]
    return sql, params


def _pattern(rule, table, context):
    rows, rows_params = _rows_sql(rule, table, context)
    return _wrap(rule, f"value does not match pattern '{rule.params}'",
                 rows, rows_params, "NOT regexp_matches(PV, ?)", [rule.params])


def _min_length(rule, table, context):
    rows, rows_params = _rows_sql(rule, table, context)
    return _wrap(rule, f"value is shorter than {rule.params} characters",
                 rows, rows_params, "length(PV) < ?", [rule.params])


def _max_length(rule, table, context):
    rows, rows_params = _rows_sql(rule, table, context)
    return _wrap(rule, f"value is longer than {rule.params} characters",
                 rows, rows_params, "length(PV) > ?", [rule.params])


def _range(operator, description):
    """Numeric range builder factory; non-castable values are the datatype check's job."""
    def builder(rule, table, context):
        rows, rows_params = _rows_sql(rule, table, context)
        return _wrap(rule, f"value is {description} {rule.params}",
                     rows, rows_params, f"TRY_CAST(PV AS DOUBLE) {operator} ?", [rule.params])
    return builder


def _in(rule, table, context):
    rows, rows_params = _rows_sql(rule, table, context)
    allowed = [str(value) for value in rule.params]
    local = iri_duckdb.local_value("PV")
    return _wrap(rule, f"value is not one of {sorted(allowed)}",
                 rows, rows_params, f"NOT list_contains(?, {local})", [allowed])


def _has_value(rule, table, context):
    rows, rows_params = _rows_sql(rule, table, context)
    from_sql = (f"SELECT f.ID AS FOCUS FROM ({_focus_sql(rule, table, context)}) f "
                f"WHERE f.ID NOT IN (SELECT FOCUS FROM ({rows}) WHERE PV = ?)")
    return _wrap(rule, f"{rule.path} does not have required value '{rule.params}'",
                 from_sql, [_focus_param(rule, context), *rows_params, str(rule.params)], "TRUE", [],
                 value_expr=_NULL_VALUE)


def _class(rule, table, context):
    rows, rows_params = _rows_sql(rule, table, context)
    return _wrap(rule, f"referenced object is not of class {rule.params}",
                 rows, rows_params, f"PV NOT IN ({_class_sql(table, context)})", [rule.params])


def _schema_range(rule, table, context):
    """triplets:range — ANY of the target's types in the allowed set conforms
    (issue #100); targets without a Type row are silent."""
    rows, rows_params = _rows_sql(rule, table, context)
    placeholders = ", ".join("?" for _ in rule.params)
    condition = (f"EXISTS (SELECT 1 FROM {table} x WHERE x.ID = PV AND x.KEY = '{TYPE_KEY}') "
                 f"AND PV NOT IN (SELECT ID FROM {table} x "
                 f"WHERE x.KEY = '{TYPE_KEY}' AND x.VALUE IN ({placeholders}))")
    return _wrap(rule, f"reference target is not one of {rule.params}",
                 rows, rows_params, condition, list(rule.params))


def _node_kind(rule, table, context):
    if rule.params not in ("IRI", "Literal"):
        logger.debug("sh:nodeKind %s not checkable on triplets — skipped (%s)", rule.params, rule.shape_id)
        return None
    rows, rows_params = _rows_sql(rule, table, context)
    # via_type value nodes are the referenced objects' types — always IRIs
    kind = "iri" if getattr(rule, "via_type", False) else node_kind(rule.path, context.value_types)
    if kind is not None:                                 # schema decides for the whole path
        if (kind == "iri") == (rule.params == "IRI"):
            return None                                  # every value conforms — no query
        condition, condition_params = "TRUE", []         # every value violates
    else:                                                # value-form heuristic
        is_iri = f"(regexp_full_match(PV, ?) OR PV IN (SELECT DISTINCT ID FROM {table}))"
        condition = f"NOT {is_iri}" if rule.params == "IRI" else is_iri
        condition_params = [REFERENCE_LIKE.pattern]
    return _wrap(rule, f"value is not of node kind sh:{rule.params}",
                 rows, rows_params, condition, condition_params)


def _pair_sql(rule, table, context, other_path):
    """Both paths' values per focus node, for the pair constraints."""
    left, left_params = _rows_sql(rule, table, context)
    right = (f"SELECT ID AS FOCUS, VALUE AS OTHER FROM {table} "
             f"WHERE KEY = ? AND ID IN ({_focus_sql(rule, table, context)})")
    return left, left_params, right, [other_path, _focus_param(rule, context)]


def _equals(rule, table, context):
    """sh:equals is set equality per focus node: a value present at only one
    of the two properties is a violation; matching multi-valued sets conform."""
    left, left_params, right, right_params = _pair_sql(rule, table, context, rule.params)
    from_sql = (f"SELECT a.FOCUS AS FOCUS, a.PV AS PV FROM ({left}) a "
                f"ANTI JOIN ({right}) b ON a.FOCUS = b.FOCUS AND a.PV = b.OTHER "
                f"UNION ALL "
                f"SELECT b.FOCUS AS FOCUS, b.OTHER AS PV FROM ({right}) b "
                f"ANTI JOIN ({left}) a ON a.FOCUS = b.FOCUS AND a.PV = b.OTHER")
    return _wrap(rule, f"{rule.path} does not equal {rule.params}",
                 from_sql, [*left_params, *right_params, *right_params, *left_params], "TRUE", [])


def _disjoint(rule, table, context):
    left, left_params, right, right_params = _pair_sql(rule, table, context, rule.params)
    from_sql = (f"SELECT a.FOCUS AS FOCUS, a.PV AS PV FROM ({left}) a "
                f"JOIN ({right}) b ON a.FOCUS = b.FOCUS WHERE a.PV = b.OTHER")
    return _wrap(rule, f"{rule.path} shares a value with {rule.params}",
                 from_sql, [*left_params, *right_params], "TRUE", [])


def _pair_compare(operator, description):
    """sh:lessThan(-OrEquals): violation when the comparison does not hold
    (incl. non-numeric pairs — null comparisons count as failed, like pandas)."""
    def builder(rule, table, context):
        left, left_params, right, right_params = _pair_sql(rule, table, context, rule.params)
        from_sql = (f"SELECT a.FOCUS AS FOCUS, a.PV AS PV FROM ({left}) a "
                    f"JOIN ({right}) b ON a.FOCUS = b.FOCUS "
                    f"WHERE NOT COALESCE(TRY_CAST(a.PV AS DOUBLE) {operator} TRY_CAST(b.OTHER AS DOUBLE), FALSE)")
        return _wrap(rule, f"{rule.path} is not {description} {rule.params}",
                     from_sql, [*left_params, *right_params], "TRUE", [])
    return builder


def _closed(rule, table, context):
    allowed = list(set(rule.params) | {TYPE_KEY})
    sql = (f"SELECT ID, KEY, VALUE, ? AS VIOLATION_TYPE, ? AS MESSAGE, ? AS SEVERITY, ? AS SOURCE_SHAPE "
           f"FROM {table} WHERE ID IN ({_focus_sql(rule, table, context)}) AND NOT list_contains(?, KEY)")
    params = [rule.component, rule.message or "property is not allowed on a closed shape",
              rule.severity, rule.shape_id, _focus_param(rule, context), allowed]
    return sql, params


# ── nested / query components ────────────────────────────────────────────────
# Logical operators compose their nested rows' SQL. Nested rows judge the
# parent's focus, or an ID list bound through UNNEST(?) (sh:node: the
# referenced value nodes, resolved with one query).

_EMPTY_SQL = ("SELECT " + ", ".join(f"CAST(NULL AS VARCHAR) AS {column}" for column in VIOLATION_COLUMNS)
              + " WHERE FALSE")


def _nested_sql(row_dicts, table, context, focus_ids=None):
    """Nested IR rows → one UNION ALL statement of their violations."""
    statements = []
    for row in row_dicts:
        rule = SimpleNamespace(focus_ids=focus_ids, **row)
        builder = SQL_BUILDERS.get(rule.component)
        if builder is None:
            logger.warning("nested %s in %s not implemented — not evaluated", rule.component, rule.shape_id)
        elif (statement := builder(rule, table, context)) is not None:
            statements.append(statement)
    if not statements:
        return _EMPTY_SQL, []
    return (" UNION ALL ".join(f"({sql})" for sql, _ in statements),
            [parameter for _, parameters in statements for parameter in parameters])


def _violating_ids(row_dicts, table, context, focus_ids=None):
    """SQL selecting the IDs the nested rows report (never NULL — NOT IN stays two-valued)."""
    sql, params = _nested_sql(row_dicts, table, context, focus_ids)
    return f"SELECT ID FROM ({sql}) WHERE ID IS NOT NULL", params


def _focus_violations(rule, table, context, alternatives, condition, message):
    """Focus nodes for which *condition* holds; ``{}`` in it is each alternative's violating IDs."""
    focus_ids = getattr(rule, "focus_ids", None)
    parts = [_violating_ids(rows, table, context, focus_ids) for rows in alternatives]
    where = condition([f"FOCUS IN ({sql})" for sql, _ in parts])
    from_sql = f"SELECT ID AS FOCUS FROM ({_focus_sql(rule, table, context)})"
    return _wrap(rule, message, from_sql, [_focus_param(rule, context)],
                 where, [parameter for _, params in parts for parameter in params],
                 value_expr=_NULL_VALUE)


def _and(rule, table, context):
    """sh:and — every nested shape must hold; each nested violation is reported."""
    focus_ids = getattr(rule, "focus_ids", None)
    parts = [_nested_sql(rows, table, context, focus_ids) for rows in rule.params]
    union = " UNION ALL ".join(f"({sql})" for sql, _ in parts)
    columns = ", ".join("'sh:and' AS VIOLATION_TYPE" if column == "VIOLATION_TYPE" else column
                        for column in VIOLATION_COLUMNS)
    return f"SELECT {columns} FROM ({union})", [parameter for _, params in parts for parameter in params]


def _or(rule, table, context):
    """sh:or — a focus node violates only when EVERY alternative is violated."""
    return _focus_violations(rule, table, context, rule.params, " AND ".join,
                             "no sh:or alternative is satisfied")


def _xone(rule, table, context):
    """sh:xone — a focus node violates unless exactly one alternative is satisfied."""
    def exactly_one_fails(conditions):
        satisfied = " + ".join(f"CASE WHEN {condition} THEN 0 ELSE 1 END" for condition in conditions)
        return f"({satisfied}) <> 1"
    return _focus_violations(rule, table, context, rule.params, exactly_one_fails,
                             "exactly one sh:xone alternative must be satisfied")


def _not(rule, table, context):
    """sh:not — a focus node violates when it SATISFIES the negated shape."""
    return _focus_violations(rule, table, context, [rule.params],
                             lambda conditions: f"NOT {conditions[0]}",
                             "node conforms to the negated shape")


def _node(rule, table, context):
    """sh:node — every value at the path must conform to the referenced shape.

    The referenced shape was expanded into nested IR rows at compile time; here
    they run with the referenced value nodes as focus. A value node that
    produces any nested violation makes the referring focus node violate sh:node.
    """
    rows, rows_params = _rows_sql(rule, table, context)
    referenced = [value for (value,) in context.connection.execute(
        f"SELECT DISTINCT PV FROM ({rows}) WHERE PV IS NOT NULL", rows_params).fetchall()]
    if not referenced:
        return None
    non_conforming, params = _violating_ids(rule.params["rows"], table, context, referenced)
    return _wrap(rule, f"value does not conform to shape {rule.params['shape']}",
                 rows, rows_params, f"PV IN ({non_conforming})", params)


def _focus_list(rule, table, context):
    return [value for (value,) in context.connection.execute(
        _focus_sql(rule, table, context), [_focus_param(rule, context)]).fetchall()]


def _sparql(rule, table, context):
    return context.frame_sql(shacl_sparql.run(context.sparql, rule, _focus_list(rule, table, context)))



# component → SQL builder
SQL_BUILDERS = {
    "sh:minCount": _min_count,
    "sh:maxCount": _max_count,
    "sh:datatype": _datatype,
    "sh:pattern": _pattern,
    "sh:minLength": _min_length,
    "sh:maxLength": _max_length,
    "sh:minInclusive": _range("<", "less than the minimum"),
    "sh:maxInclusive": _range(">", "greater than the maximum"),
    "sh:minExclusive": _range("<=", "not greater than the exclusive minimum"),
    "sh:maxExclusive": _range(">=", "not less than the exclusive maximum"),
    "sh:in": _in,
    "sh:hasValue": _has_value,
    "sh:class": _class,
    "triplets:range": _schema_range,
    "sh:nodeKind": _node_kind,
    "sh:equals": _equals,
    "sh:disjoint": _disjoint,
    "sh:lessThan": _pair_compare("<", "less than"),
    "sh:lessThanOrEquals": _pair_compare("<=", "less than or equal to"),
    "sh:closed": _closed,
    "sh:and": _and,
    "sh:or": _or,
    "sh:not": _not,
    "sh:xone": _xone,
    "sh:node": _node,
    "sh:sparql": _sparql,
}


class _Context:
    """Per-validation state: the connection, schema-driven term-kind decisions,
    SPARQL state and the temporary relations registered for this run."""

    def __init__(self, connection, table, rdf_map, undefined_namespace=CIM_NS, types_table=None):
        self.connection = connection
        self.table = table
        self.rdf_map = rdf_map
        self.undefined_namespace = undefined_namespace
        self.value_types = value_types(rdf_map)   # sh:nodeKind: schema-driven IRI/literal decision
        self.types_table = types_table
        self.registered = []
        self._targets = {}

    @functools.cached_property
    def sparql(self):
        """SPARQL state over one materialized copy of the (scoped) table — built on
        first use. A frame the SPARQL layer takes as is: it hashes the content once
        per object, and an Arrow table would be converted (and re-hashed) per query."""
        data = self.connection.execute(f"SELECT * FROM {self.table}").df()
        return shacl_sparql.SparqlState(data, self.rdf_map, self.undefined_namespace)

    def sparql_targets(self, query_text):
        """Focus IDs of a SPARQLTarget SELECT, resolved once per run."""
        if query_text not in self._targets:
            self._targets[query_text] = shacl_sparql.target_ids(self.sparql, query_text)
        return self._targets[query_text]

    def frame_sql(self, frame):
        """A violations frame computed outside SQL → a statement over a registered relation."""
        if not len(frame):
            return None
        name = f"_shacl_found_{len(self.registered)}"
        self.connection.register(name, frame)
        self.registered.append(name)
        columns = ", ".join(f"CAST({column} AS VARCHAR) AS {column}" for column in VIOLATION_COLUMNS)
        return f"SELECT {columns} FROM {name}", []


def _register_type_index(connection, table, rdf_map):
    """Temp (ID, VALUE) relation: instance Type rows, plus ancestor names when
    rdf_map is present — so ``VALUE = 'Equipment'`` hits Breaker IDs."""
    types = connection.execute(
        f"SELECT ID, VALUE FROM {table} WHERE KEY = '{TYPE_KEY}'").df()
    if rdf_map is not None and not types.empty:
        from .schema_ir import expand_type_index
        schema = load_rdf_map(rdf_map)
        exact = {value: group["ID"].to_numpy() for value, group
                 in types.groupby("VALUE", sort=False)}
        expanded = expand_type_index(exact, schema)
        rows = []
        for name, parts in expanded.items():
            if isinstance(parts, list):
                ids = pandas.unique(pandas.concat(
                    [pandas.Series(part) for part in parts], ignore_index=True))
            else:
                ids = parts
            for object_id in ids:
                rows.append((object_id, name))
        types = pandas.DataFrame(rows, columns=["ID", "VALUE"])
    connection.register("_shacl_types", types)
    return "_shacl_types"


def validate(data, compiled, rdf_map=None, scope=None, components=None, max_workers=None,
             table=None, schema=None, table_name=None, undefined_namespace=CIM_NS, **kwargs):
    """Validate triplet data against the compiled constraint table (DuckDB SQL).

    Parameters mirror shacl_pandas.validate, plus:

    table / schema / table_name
        Triplets relation when *data* is a DuckDB connection (defaults from
        the connection, else ``triplets``).
    """
    connection, table = _connection(data, table=table, schema=schema, table_name=table_name)
    if scope is not None:
        values = ", ".join("'" + str(instance).replace("'", "''") + "'" for instance in scope)
        connection.execute(f"CREATE OR REPLACE TEMP VIEW _shacl_scoped AS "
                           f"SELECT * FROM {table} WHERE INSTANCE_ID IN ({values})")
        table = '"_shacl_scoped"'

    if "duckdb" not in compiled.plans:   # setdefault would re-split on every call
        compiled.plans["duckdb"] = split_rules(compiled.ir, SQL_BUILDERS, "duckdb")
    rules, _ = compiled.plans["duckdb"]
    if components is not None:
        rules = [rule for rule in rules if rule.component in components]

    context = _Context(connection, table, rdf_map, undefined_namespace,
                       types_table=_register_type_index(connection, table, rdf_map))
    parallel = [rule for rule in rules if max_workers and rule.component == "sh:sparql"]
    built = [statement for rule in rules if not (parallel and rule.component == "sh:sparql")
             if (statement := SQL_BUILDERS[rule.component](rule, table, context)) is not None]

    # every builder emits the same 7-column shape, so constraints batch into
    # UNION ALL statements — round-trip/planner overhead per rule was the
    # dominant in-memory cost (~4,700 statements on the real profiles)
    frames = []
    try:
        for start in range(0, len(built), _BATCH_SIZE):
            batch = built[start:start + _BATCH_SIZE]
            sql = " UNION ALL ".join(f"({statement})" for statement, _ in batch)
            params = [parameter for _, parameters in batch for parameter in parameters]
            result = connection.execute(sql, params).df()
            if len(result):
                frames.append(result)
        if parallel:
            tasks = [(rule, _focus_list(rule, table, context)) for rule in parallel]
            frames += [found for found in shacl_sparql.run_parallel(context.sparql, tasks, max_workers)
                       if len(found)]
    finally:
        for name in context.registered:
            connection.unregister(name)

    if not frames:
        return pandas.DataFrame(columns=VIOLATION_COLUMNS)
    violations = pandas.concat(frames, ignore_index=True)
    violations = violations.astype(object).where(violations.notna(), None)
    return shacl_sparql.dedupe_invalid(violations)


def _connection(data, table=None, schema=None, table_name=None):
    """DuckDB connection holding the triplets table; other flavors are registered."""
    import duckdb
    from ..tools.duckdb_engine import _resolve_table

    if flavor(data) == "duckdb":
        return data, _resolve_table(data, table=table, schema=schema, table_name=table_name)
    connection = duckdb.connect()
    bare = table if table is not None else (table_name if table_name is not None else "triplets")
    connection.register(bare, data)  # pandas / polars / arrow — zero-copy via Arrow
    return connection, _resolve_table(connection, table=bare)
