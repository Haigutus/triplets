"""SHACL DuckDB engine — compiled-IR executor for larger-than-memory data.

Every constraint compiles to one SQL query against the ``triplets`` table
(``[ID, KEY, VALUE, INSTANCE_ID]``), so validation streams through DuckDB's
vectorized executor and spills to disk instead of requiring the dataset in
RAM. Input is a DuckDB connection holding the table (``con.read_rdf(...)``);
any other flavor (pandas/polars/arrow) is registered into an in-memory
connection, which makes the engine uniformly testable.

Semantics are identical to the pandas/polars engines (same IR, same canonical
violations schema, same lexical-form datatype deviation). The nested and
query components (sh:or, sh:and, sh:not, sh:node, sh:sparql) delegate to the
pandas implementations — they materialize the (scoped) table, which is fine:
those are a handful of rows even in the real profiles, and sh:sparql needs an
rdflib graph anyway.

Explicitly selected (``engine="duckdb"``), not in the auto order: polars owns
the in-memory fast path; this engine is the deliberate choice when the data
does not fit.
"""
import logging

import pandas

from .shacl_report import VIOLATION_COLUMNS
from .shacl_pandas import DATATYPES, _REFERENCE_LIKE
from .shacl_polars import FALLBACK_COMPONENTS

logger = logging.getLogger(__name__)

TABLE_NAME = "triplets"
_BATCH_SIZE = 100  # constraints per UNION ALL statement


def _focus_sql(table):
    """Focus nodes of a target class (bound with one target_class parameter)."""
    return f"SELECT ID FROM {table} WHERE KEY = 'Type' AND VALUE = ?"


def _rows_sql(rule, table):
    """The rule's path as (FOCUS, PV) rows — inverse-aware, like the other engines."""
    if rule.inverse:
        sql = (f"SELECT VALUE AS FOCUS, ID AS PV FROM {table} "
               f"WHERE KEY = ? AND VALUE IN ({_focus_sql(table)})")
    else:
        sql = (f"SELECT ID AS FOCUS, VALUE AS PV FROM {table} "
               f"WHERE KEY = ? AND ID IN ({_focus_sql(table)})")
    return sql, [rule.path, rule.target_class]


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
    rows, rows_params = _rows_sql(rule, table)
    from_sql = (f"SELECT f.ID AS FOCUS FROM ({_focus_sql(table)}) f "
                f"LEFT JOIN (SELECT FOCUS, COUNT(*) AS n FROM ({rows}) GROUP BY FOCUS) c "
                f"ON f.ID = c.FOCUS WHERE COALESCE(c.n, 0) < ?")
    return _wrap(rule, f"{rule.path} occurs fewer than {rule.params} time(s)",
                 from_sql, [rule.target_class, *rows_params, rule.params], "TRUE", [],
                 value_expr=_NULL_VALUE)


def _max_count(rule, table, context):
    rows, rows_params = _rows_sql(rule, table)
    from_sql = f"SELECT FOCUS FROM ({rows}) GROUP BY FOCUS HAVING COUNT(*) > ?"
    return _wrap(rule, f"{rule.path} occurs more than {rule.params} time(s)",
                 from_sql, [*rows_params, rule.params], "TRUE", [], value_expr=_NULL_VALUE)


def _datatype(rule, table, context):
    """Two-level lexical check (same deviation as the other vectorized engines)."""
    spec = DATATYPES.get(str(rule.params).removeprefix("xsd:"))
    if spec is None:
        return None
    valid_pattern, warn_pattern = spec
    rows, rows_params = _rows_sql(rule, table)

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
    rows, rows_params = _rows_sql(rule, table)
    return _wrap(rule, f"value does not match pattern '{rule.params}'",
                 rows, rows_params, "NOT regexp_matches(PV, ?)", [rule.params])


def _min_length(rule, table, context):
    rows, rows_params = _rows_sql(rule, table)
    return _wrap(rule, f"value is shorter than {rule.params} characters",
                 rows, rows_params, "length(PV) < ?", [rule.params])


def _max_length(rule, table, context):
    rows, rows_params = _rows_sql(rule, table)
    return _wrap(rule, f"value is longer than {rule.params} characters",
                 rows, rows_params, "length(PV) > ?", [rule.params])


def _range(operator, description):
    """Numeric range builder factory; non-castable values are the datatype check's job."""
    def builder(rule, table, context):
        rows, rows_params = _rows_sql(rule, table)
        return _wrap(rule, f"value is {description} {rule.params}",
                     rows, rows_params, f"TRY_CAST(PV AS DOUBLE) {operator} ?", [rule.params])
    return builder


def _in(rule, table, context):
    rows, rows_params = _rows_sql(rule, table)
    allowed = [str(value) for value in rule.params]
    local = "list_extract(string_split(list_extract(string_split(PV, '#'), -1), '/'), -1)"
    return _wrap(rule, f"value is not one of {sorted(allowed)}",
                 rows, rows_params, f"NOT list_contains(?, {local})", [allowed])


def _has_value(rule, table, context):
    rows, rows_params = _rows_sql(rule, table)
    from_sql = (f"SELECT f.ID AS FOCUS FROM ({_focus_sql(table)}) f "
                f"WHERE f.ID NOT IN (SELECT FOCUS FROM ({rows}) WHERE PV = ?)")
    return _wrap(rule, f"{rule.path} does not have required value '{rule.params}'",
                 from_sql, [rule.target_class, *rows_params, str(rule.params)], "TRUE", [],
                 value_expr=_NULL_VALUE)


def _class(rule, table, context):
    rows, rows_params = _rows_sql(rule, table)
    return _wrap(rule, f"referenced object is not of class {rule.params}",
                 rows, rows_params, f"PV NOT IN ({_focus_sql(table)})", [rule.params])


def _node_kind(rule, table, context):
    if rule.params not in ("IRI", "Literal"):
        logger.debug("sh:nodeKind %s not checkable on triplets — skipped (%s)", rule.params, rule.shape_id)
        return None
    rows, rows_params = _rows_sql(rule, table)
    kind = context.key_kind(rule.path)
    if kind is not None:                                 # schema decides for the whole path
        if (kind == "iri") == (rule.params == "IRI"):
            return None                                  # every value conforms — no query
        condition, condition_params = "TRUE", []         # every value violates
    else:                                                # value-form heuristic
        is_iri = f"(regexp_full_match(PV, ?) OR PV IN (SELECT DISTINCT ID FROM {table}))"
        condition = f"NOT {is_iri}" if rule.params == "IRI" else is_iri
        condition_params = [_REFERENCE_LIKE.pattern]
    return _wrap(rule, f"value is not of node kind sh:{rule.params}",
                 rows, rows_params, condition, condition_params)


def _pair_sql(rule, table, other_path):
    """Both paths' values per focus node, for the pair constraints."""
    left, left_params = _rows_sql(rule, table)
    right = (f"SELECT ID AS FOCUS, VALUE AS OTHER FROM {table} "
             f"WHERE KEY = ? AND ID IN ({_focus_sql(table)})")
    return left, left_params, right, [other_path, rule.target_class]


def _equals(rule, table, context):
    """sh:equals is set equality per focus node: a value present at only one
    of the two properties is a violation; matching multi-valued sets conform."""
    left, left_params, right, right_params = _pair_sql(rule, table, rule.params)
    from_sql = (f"SELECT a.FOCUS AS FOCUS, a.PV AS PV FROM ({left}) a "
                f"ANTI JOIN ({right}) b ON a.FOCUS = b.FOCUS AND a.PV = b.OTHER "
                f"UNION ALL "
                f"SELECT b.FOCUS AS FOCUS, b.OTHER AS PV FROM ({right}) b "
                f"ANTI JOIN ({left}) a ON a.FOCUS = b.FOCUS AND a.PV = b.OTHER")
    return _wrap(rule, f"{rule.path} does not equal {rule.params}",
                 from_sql, [*left_params, *right_params, *right_params, *left_params], "TRUE", [])


def _disjoint(rule, table, context):
    left, left_params, right, right_params = _pair_sql(rule, table, rule.params)
    from_sql = (f"SELECT a.FOCUS AS FOCUS, a.PV AS PV FROM ({left}) a "
                f"JOIN ({right}) b ON a.FOCUS = b.FOCUS WHERE a.PV = b.OTHER")
    return _wrap(rule, f"{rule.path} shares a value with {rule.params}",
                 from_sql, [*left_params, *right_params], "TRUE", [])


def _pair_compare(operator, description):
    """sh:lessThan(-OrEquals): violation when the comparison does not hold
    (incl. non-numeric pairs — null comparisons count as failed, like pandas)."""
    def builder(rule, table, context):
        left, left_params, right, right_params = _pair_sql(rule, table, rule.params)
        from_sql = (f"SELECT a.FOCUS AS FOCUS, a.PV AS PV FROM ({left}) a "
                    f"JOIN ({right}) b ON a.FOCUS = b.FOCUS "
                    f"WHERE NOT COALESCE(TRY_CAST(a.PV AS DOUBLE) {operator} TRY_CAST(b.OTHER AS DOUBLE), FALSE)")
        return _wrap(rule, f"{rule.path} is not {description} {rule.params}",
                     from_sql, [*left_params, *right_params], "TRUE", [])
    return builder


def _closed(rule, table, context):
    allowed = list(set(rule.params) | {"Type"})
    sql = (f"SELECT ID, KEY, VALUE, ? AS VIOLATION_TYPE, ? AS MESSAGE, ? AS SEVERITY, ? AS SOURCE_SHAPE "
           f"FROM {table} WHERE ID IN ({_focus_sql(table)}) AND NOT list_contains(?, KEY)")
    params = [rule.component, rule.message or "property is not allowed on a closed shape",
              rule.severity, rule.shape_id, rule.target_class, allowed]
    return sql, params


# component → SQL builder (FALLBACK_COMPONENTS run via shacl_pandas)
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
    "sh:nodeKind": _node_kind,
    "sh:equals": _equals,
    "sh:disjoint": _disjoint,
    "sh:lessThan": _pair_compare("<", "less than"),
    "sh:lessThanOrEquals": _pair_compare("<=", "less than or equal to"),
    "sh:closed": _closed,
}


class _Context:
    """Schema-driven term-kind decisions (same as the other vectorized engines)."""

    def __init__(self, rdf_map):
        self.rdf_map = rdf_map
        self._key_metadata = None

    def key_kind(self, key):
        if self.rdf_map is None:
            return None
        if self._key_metadata is None:
            from ..export.nquads_utils import build_key_metadata
            self._key_metadata = build_key_metadata(self.rdf_map)
        enum_keys, _namespaces, key_datatypes = self._key_metadata
        if key in enum_keys:
            return "iri"
        if key in key_datatypes:
            return "literal"
        return None


def validate(data, compiled, rdf_map=None, scope=None, components=None, max_workers=None,
             table_name=TABLE_NAME, **kwargs):
    """Validate triplet data against the compiled constraint table (DuckDB SQL).

    Parameters mirror shacl_pandas.validate, plus:

    table_name : str, default "triplets"
        Table holding the triplets when *data* is a DuckDB connection.
    """
    connection, table = _connection(data, table_name)
    if scope is not None:
        values = ", ".join("'" + str(instance).replace("'", "''") + "'" for instance in scope)
        connection.execute(f"CREATE OR REPLACE TEMP VIEW _shacl_scoped AS "
                           f"SELECT * FROM {table} WHERE INSTANCE_ID IN ({values})")
        table = "_shacl_scoped"

    vectorized, fallback = compiled.plans.setdefault("duckdb", _split_rules(compiled.ir))
    if components is not None:
        vectorized = [rule for rule in vectorized if rule.component in components]
        fallback = [rule for rule in fallback if rule.component in components]

    context = _Context(rdf_map)
    built = [statement for rule in vectorized
             if (statement := SQL_BUILDERS[rule.component](rule, table, context)) is not None]

    # every builder emits the same 7-column shape, so constraints batch into
    # UNION ALL statements — round-trip/planner overhead per rule was the
    # dominant in-memory cost (~4,700 statements on the real profiles)
    frames = []
    for start in range(0, len(built), _BATCH_SIZE):
        batch = built[start:start + _BATCH_SIZE]
        sql = " UNION ALL ".join(f"({statement})" for statement, _ in batch)
        params = [parameter for _, parameters in batch for parameter in parameters]
        result = connection.execute(sql, params).df()
        if len(result):
            frames.append(result)

    violations = (pandas.concat(frames, ignore_index=True) if frames
                  else pandas.DataFrame(columns=VIOLATION_COLUMNS))
    violations = violations.astype(object).where(violations.notna(), None)

    if fallback:
        # nested/query components materialize the (already scoped) table — a
        # handful of rows even in real profiles, and sh:sparql needs rdflib anyway
        from . import shacl_pandas
        frame = connection.execute(f"SELECT * FROM {table}").df()
        supplement = shacl_pandas.validate(frame, compiled, rdf_map=rdf_map,
                                           components={rule.component for rule in fallback},
                                           max_workers=max_workers)
        violations = pandas.concat([violations, supplement], ignore_index=True)
    return violations


def _split_rules(ir):
    """IR → (vectorized rules, fallback rules); cached in CompiledShapes.plans."""
    rules = list(ir.itertuples())
    vectorized = [rule for rule in rules if rule.component in SQL_BUILDERS]
    fallback = [rule for rule in rules if rule.component in FALLBACK_COMPONENTS]
    skipped = {rule.component for rule in rules} - set(SQL_BUILDERS) - FALLBACK_COMPONENTS
    if skipped:
        logger.debug("duckdb engine skips components: %s (pyshacl covers them)", ", ".join(sorted(skipped)))
    return vectorized, fallback


def _connection(data, table_name):
    """DuckDB connection holding the triplets table; other flavors are registered."""
    import duckdb

    if type(data).__module__.startswith(("duckdb", "_duckdb")):
        return data, table_name
    connection = duckdb.connect()
    connection.register(table_name, data)   # pandas / polars / arrow — zero-copy via Arrow
    return connection, table_name
