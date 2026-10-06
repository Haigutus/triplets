"""SHACL pandas engine — compiled-IR executor (debugging reference for the
vectorized engine family).

Operates directly on the triplet DataFrame's raw string VALUEs — no rdflib,
no rdf_map. That enables the one deliberate deviation from pyshacl: the
datatype check judges the actual lexical form. rdflib reads ``"1"^^xsd:float``
as simply valid; here it is reported, on two levels:

- value outside the declared type's lexical space ("abc" for xsd:float)
  → VIOLATION_TYPE sh:datatype, the shape's declared severity
- value valid but written in a non-canonical / narrower form ("1" for
  xsd:float — integer form; "0" for xsd:boolean)
  → VIOLATION_TYPE triplets:lexicalForm, severity Warning

Structure: one pure function per constraint component
``(context, rule) → violations DataFrame``, registered in
CONSTRAINT_VALIDATORS. Every validator sees the rule's path normalized to
(FOCUS, PATH_VALUE) pairs — inverse paths (sh:inversePath) swap the columns,
so direction is handled once in ``_Context.path_rows``.

sh:sparql constraints are delegated to triplets.sparql (auto engine — qlever
when built, else oxigraph, else rdflib): the engine loads the data once
(content-hash cached), each constraint runs as one SELECT with the focus
nodes bound via VALUES, and ``max_workers`` runs the constraint queries in
parallel processes on the rdflib path only (fork — copy-on-write shares the
dataset; threads don't help GIL-bound rdflib; the embedded engines are
ms-scale sequentially).

Known limits:
- sh:nodeKind — triplets store every value as a string, so term kind is decided
  like the N-Quads exporter decides it: by the schema when rdf_map names the
  path (a datatype key is Literal even when its values look like UUIDs), by
  value form otherwise (known ID / UUID / URI scheme / enum ``PhaseCode.ABC``)
"""
import re
import logging

from types import SimpleNamespace

import numpy
import pandas

from ..iri import CIM_NS, REFERENCE_LIKE, TYPE_KEY, iri_pandas, load_rdf_map, node_kind, value_types
from . import shacl_sparql
from .shacl_report import VIOLATION_COLUMNS

logger = logging.getLogger(__name__)


# ── XSD lexical spaces ───────────────────────────────────────────────────────
# Per type: (valid lexical space, non-canonical subset reported as Warning).
_INTEGER = r"[+-]?[0-9]+"
_DECIMAL = rf"(?:{_INTEGER}|[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+))"
_FLOAT = rf"(?:{_DECIMAL}(?:[eE][+-]?[0-9]+)?|[+-]?INF|NaN)"
_DATE = r"-?[0-9]{4,}-[0-9]{2}-[0-9]{2}"
_TIMEZONE = r"(?:Z|[+-][0-9]{2}:[0-9]{2})?"
_DATETIME = rf"{_DATE}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}(?:\.[0-9]+)?{_TIMEZONE}"
# IRI reference (RFC 3987), relative included: no controls, space, DEL or <>"{}|^`\,
# and "%" only as a %XX escape. Non-ASCII is allowed (IRI, not URI).
_ANY_URI = r'(?:[^\x00-\x20\x7f<>"{}|^`\\%]|%[0-9A-Fa-f]{2})*'

DATATYPES = {
    "integer": (_INTEGER, None),
    "int": (_INTEGER, None),
    "long": (_INTEGER, None),
    "short": (_INTEGER, None),
    "byte": (_INTEGER, None),
    "nonNegativeInteger": (r"\+?[0-9]+", None),
    "positiveInteger": (r"\+?0*[1-9][0-9]*", None),
    "decimal": (_DECIMAL, _INTEGER),
    "float": (_FLOAT, _INTEGER),
    "double": (_FLOAT, _INTEGER),
    "boolean": (r"true|false|1|0", r"1|0"),
    "date": (_DATE + _TIMEZONE, None),
    "dateTime": (_DATETIME, None),
    "anyURI": (_ANY_URI, None),
    # string / unlisted types: every lexical form is valid — no check
}

_NO_IDS = numpy.array([], dtype=object)


class _Context:
    """Shared per-validation state: data and memoized lookups.

    Hundreds of IR rules hit the same per-class indices — build them once here
    instead of once per rule (the polars/duckdb compilers follow the same shape).
    """

    def __init__(self, data, rdf_map=None, undefined_namespace=CIM_NS):
        self.data = data
        self.rdf_map = rdf_map
        self.undefined_namespace = undefined_namespace
        self.value_types = value_types(rdf_map)   # sh:nodeKind: schema-driven IRI/literal decision
        self._by_key = None
        self._class_ids = None
        self._all_ids = None
        self.sparql = shacl_sparql.SparqlState(data, rdf_map, undefined_namespace)

    def key_rows(self, key):
        """All rows at *key* — the data is grouped by KEY once, not per rule."""
        if self._by_key is None:
            self._by_key = {group: frame for group, frame
                            in self.data.groupby("KEY", observed=True, sort=False)}
        return self._by_key.get(key, self.data.iloc[0:0])

    def class_ids(self, target_class):
        """IDs of all instances of *target_class* (and, with rdf_map, its descendants)."""
        if self._class_ids is None:
            exact = {value: frame["ID"].unique() for value, frame
                     in self.key_rows(TYPE_KEY).groupby("VALUE", observed=True, sort=False)}
            self._class_ids = self._with_ancestors(exact)
        return self._class_ids.get(target_class, _NO_IDS)

    def _with_ancestors(self, exact):
        """Precompute ancestor → concat(descendant IDs). One pass; then O(1) lookup."""
        if self.rdf_map is None:
            return exact
        from .schema_ir import expand_type_index
        schema = load_rdf_map(self.rdf_map)
        expanded = expand_type_index(exact, schema)
        out = {}
        for name, parts in expanded.items():
            if isinstance(parts, list):
                out[name] = pandas.unique(numpy.concatenate(
                    [numpy.asarray(part, dtype=object) for part in parts]))
            else:
                out[name] = parts
        return out

    @property
    def all_ids(self):
        """Every subject ID in the data (for reference-likeness checks)."""
        if self._all_ids is None:
            self._all_ids = set(self.data["ID"].unique())
        return self._all_ids

    def focus(self, rule):
        """The rule's focus nodes: an explicit ``focus_ids`` (set by sh:node — the
        referenced value nodes), the subjects carrying the target property
        (sh:targetSubjectsOf), or all instances of the rule's target class."""
        focus_ids = getattr(rule, "focus_ids", None)
        if focus_ids is not None:
            return focus_ids
        kind = getattr(rule, "target_kind", "class")
        if kind == "subjectsOf":
            return self.key_rows(rule.target_class)["ID"].unique()
        if kind == "objectsOf":
            return self.key_rows(rule.target_class)["VALUE"].astype(str).unique()
        if kind == "node":
            return numpy.array([rule.target_class], dtype=object)
        if kind == "sparql":
            return numpy.array(shacl_sparql.target_ids(self.sparql, rule.target_class), dtype=object)
        return self.class_ids(rule.target_class)

    def path_rows(self, rule):
        """The rule's path as (FOCUS, PATH_VALUE) pairs, restricted to the rule's focus.

        Normal path:  FOCUS = row ID,   PATH_VALUE = row VALUE.
        Inverse path: FOCUS = row VALUE (the referenced focus object),
                      PATH_VALUE = row ID (the referencing object).
        via_type:     PATH_VALUE = the referenced object's type (the
                      ( assoc rdf:type ) sequence path; SHACL path semantics:
                      a reference whose target has no Type row yields no
                      value node, hence the inner join).
        """
        ids = self.focus(rule)
        rows = self.key_rows(rule.path)
        if rule.inverse:
            rows = rows[rows["VALUE"].isin(ids)]
            frame = pandas.DataFrame({"FOCUS": rows["VALUE"].to_numpy(),
                                      "PATH_VALUE": rows["ID"].to_numpy()})
        else:
            rows = rows[rows["ID"].isin(ids)]
            frame = pandas.DataFrame({"FOCUS": rows["ID"].to_numpy(),
                                      "PATH_VALUE": rows["VALUE"].to_numpy()})
        if getattr(rule, "via_type", False):
            types = self.key_rows(TYPE_KEY)[["ID", "VALUE"]].astype(str)
            frame = (frame.merge(types, left_on="PATH_VALUE", right_on="ID")
                     [["FOCUS", "VALUE"]].rename(columns={"VALUE": "PATH_VALUE"}))
        return frame

    def pair_rows(self, rule, other_path):
        """FOCUS + both paths' values, for the pair constraints (equals/disjoint/lessThan)."""
        left = self.path_rows(rule)
        other = SimpleNamespace(target_class=rule.target_class,
                                target_kind=getattr(rule, "target_kind", "class"),
                                path=other_path, inverse=False,
                                focus_ids=getattr(rule, "focus_ids", None))
        right = self.path_rows(other).rename(columns={"PATH_VALUE": "OTHER_VALUE"})
        return left, right


def _frame(rule, focus, values, message, violation_type=None, severity=None):
    """Violations DataFrame in the canonical schema."""
    return pandas.DataFrame({
        "ID": list(focus),
        "KEY": rule.path,
        "VALUE": list(values) if values is not None else None,
        "VIOLATION_TYPE": violation_type or rule.component,
        "MESSAGE": rule.message or message,
        "SEVERITY": severity or rule.severity,
        "SOURCE_SHAPE": rule.shape_id,
    }, columns=VIOLATION_COLUMNS)


def _empty():
    return pandas.DataFrame(columns=VIOLATION_COLUMNS)


# ── cardinality ──────────────────────────────────────────────────────────────

def _counts(context, rule):
    rows = context.path_rows(rule)
    return (rows.groupby("FOCUS").size()
            .reindex(context.focus(rule), fill_value=0))


def _min_count(context, rule):
    counts = _counts(context, rule)
    violating = counts[counts < rule.params]
    return _frame(rule, violating.index, None,
                  f"{rule.path} occurs fewer than {rule.params} time(s)")


def _max_count(context, rule):
    counts = _counts(context, rule)
    violating = counts[counts > rule.params]
    return _frame(rule, violating.index, None,
                  f"{rule.path} occurs more than {rule.params} time(s)")


# ── value tests ──────────────────────────────────────────────────────────────

def _datatype(context, rule):
    """sh:datatype — lexical-form check on the raw VALUE strings.

    Deviates from pyshacl by design: reports invalid lexical forms as
    sh:datatype and valid-but-non-canonical forms as triplets:lexicalForm.
    """
    spec = DATATYPES.get(str(rule.params).removeprefix("xsd:"))
    if spec is None:
        return _empty()
    valid_pattern, warn_pattern = spec

    rows = context.path_rows(rule)
    values = rows["PATH_VALUE"].astype(str)
    invalid = ~values.str.fullmatch(valid_pattern, flags=re.ASCII)
    warned = values.str.fullmatch(warn_pattern, flags=re.ASCII) & ~invalid if warn_pattern else invalid & False

    return pandas.concat([
        _frame(rule, rows.loc[invalid, "FOCUS"], rows.loc[invalid, "PATH_VALUE"],
               f"value is not a valid {rule.params}"),
        _frame(rule, rows.loc[warned, "FOCUS"], rows.loc[warned, "PATH_VALUE"],
               f"lexical form is narrower than the declared {rule.params} (e.g. integer form for a float)",
               violation_type="triplets:lexicalForm", severity="Warning"),
    ], ignore_index=True)


def _pattern(context, rule):
    rows = context.path_rows(rule)
    # SHACL pattern is a partial match (fn:matches), like pyshacl — not anchored
    bad = ~rows["PATH_VALUE"].astype(str).str.contains(rule.params, regex=True)
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"value does not match pattern '{rule.params}'")


def _min_length(context, rule):
    rows = context.path_rows(rule)
    bad = rows["PATH_VALUE"].astype(str).str.len() < rule.params
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"value is shorter than {rule.params} characters")


def _max_length(context, rule):
    rows = context.path_rows(rule)
    bad = rows["PATH_VALUE"].astype(str).str.len() > rule.params
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"value is longer than {rule.params} characters")


def _range(comparison, description):
    """Numeric range validator factory (non-numeric values are the datatype check's job)."""
    def validator(context, rule):
        rows = context.path_rows(rule)
        numeric = pandas.to_numeric(rows["PATH_VALUE"], errors="coerce")
        bad = comparison(numeric, rule.params)   # NaN comparisons are False → skipped
        return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                      f"value is {description} {rule.params}")
    return validator


def _in(context, rule):
    rows = context.path_rows(rule)
    allowed = {str(value) for value in rule.params}
    local = iri_pandas.local_value(rows["PATH_VALUE"].astype(str))
    bad = ~local.isin(allowed)
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"value is not one of {sorted(allowed)}")


def _has_value(context, rule):
    rows = context.path_rows(rule)
    having = rows.loc[rows["PATH_VALUE"].astype(str) == str(rule.params), "FOCUS"]
    missing = pandas.Index(context.focus(rule)).difference(having)
    return _frame(rule, missing, None, f"{rule.path} does not have required value '{rule.params}'")


def _class(context, rule):
    rows = context.path_rows(rule)
    of_class = context.class_ids(rule.params)
    bad = ~rows["PATH_VALUE"].isin(of_class)
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"referenced object is not of class {rule.params}")


def _schema_range(context, rule):
    """triplets:range — the referenced object conforms when ANY of its types
    is in the allowed set (RDF types are cumulative, issue #100); references
    whose target has no Type row are silent (cross-profile datasets)."""
    rows = context.path_rows(rule)
    typed = context.key_rows(TYPE_KEY)["ID"].astype(str).unique()
    allowed = pandas.Index([]).append([pandas.Index(context.class_ids(cls))
                                       for cls in rule.params])
    bad = rows["PATH_VALUE"].isin(typed) & ~rows["PATH_VALUE"].isin(allowed)
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"reference target is not one of {rule.params}")


def _node_kind(context, rule):
    """sh:nodeKind — triplets store term kinds nowhere, so kind is decided like the
    N-Quads exporter decides it: by the schema when rdf_map names the path (a
    datatype key is Literal even when its values look like UUIDs — e.g.
    IdentifiedObject.mRID; an enum key is IRI), by value form otherwise."""
    if rule.params not in ("IRI", "Literal"):  # BlankNode & *Or* combinations — not expressible
        logger.debug("sh:nodeKind %s not checkable on triplets — skipped (%s)", rule.params, rule.shape_id)
        return _empty()

    rows = context.path_rows(rule)
    # via_type value nodes are the referenced objects' types — always IRIs
    kind = "iri" if getattr(rule, "via_type", False) else node_kind(rule.path, context.value_types)
    if kind is not None:
        is_iri = pandas.Series(kind == "iri", index=rows.index)
    else:
        values = rows["PATH_VALUE"].astype(str)
        is_iri = values.str.fullmatch(REFERENCE_LIKE) | values.isin(context.all_ids)
    bad = ~is_iri if rule.params == "IRI" else is_iri
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"value is not of node kind sh:{rule.params}")


# ── property pair constraints ────────────────────────────────────────────────

def _equals(context, rule):
    """sh:equals is set equality per focus node: a value present at only one
    of the two properties is a violation; matching multi-valued sets conform."""
    left, right = context.pair_rows(rule, rule.params)
    merged = left.merge(right.rename(columns={"OTHER_VALUE": "PATH_VALUE"}),
                        on=["FOCUS", "PATH_VALUE"], how="outer", indicator=True)
    bad = merged[merged["_merge"] != "both"]
    return _frame(rule, bad["FOCUS"], bad["PATH_VALUE"],
                  f"{rule.path} does not equal {rule.params}")


def _disjoint(context, rule):
    left, right = context.pair_rows(rule, rule.params)
    merged = left.merge(right, on="FOCUS")
    bad = merged[merged["PATH_VALUE"] == merged["OTHER_VALUE"]]
    return _frame(rule, bad["FOCUS"], bad["PATH_VALUE"],
                  f"{rule.path} shares a value with {rule.params}")


def _pair_compare(operator, description):
    """sh:lessThan / sh:lessThanOrEquals: every path value must compare against
    every value of the other property — violation when the comparison fails."""
    def validator(context, rule):
        left, right = context.pair_rows(rule, rule.params)
        merged = left.merge(right, on="FOCUS")
        a = pandas.to_numeric(merged["PATH_VALUE"], errors="coerce")
        b = pandas.to_numeric(merged["OTHER_VALUE"], errors="coerce")
        bad = merged[operator(a, b)]
        return _frame(rule, bad["FOCUS"], bad["PATH_VALUE"],
                      f"{rule.path} is not {description} {rule.params}")
    return validator


# ── shape-level constraints ──────────────────────────────────────────────────

def _closed(context, rule):
    """sh:closed — params is the compile-time allowed list (this shape's property
    paths + ignoredProperties). 'Type' (rdf:type) is always allowed: every
    triplets object carries it by construction.
    """
    allowed = set(rule.params) | {TYPE_KEY}
    ids = context.focus(rule)
    rows = context.data[context.data["ID"].isin(ids) & ~context.data["KEY"].isin(allowed)]
    frame = _frame(rule, rows["ID"], rows["VALUE"], "property is not allowed on a closed shape")
    frame["KEY"] = rows["KEY"].to_numpy()
    return frame


# ── sh:sparql: shared with the other engines (shacl_sparql) ─────────────────

def _sparql(context, rule):
    return shacl_sparql.run(context.sparql, rule, context.focus(rule))


# ── logical operators (params: nested IR row-dict lists) ────────────────────

def _run_nested(context, row_dicts, focus_ids=None):
    """Validate nested IR rows (inheriting the parent's focus override, so shapes
    nested under sh:node keep judging the referenced value nodes)."""
    rules = [SimpleNamespace(focus_ids=focus_ids, **row) for row in row_dicts]
    frames = [CONSTRAINT_VALIDATORS[rule.component](context, rule)
              for rule in rules if rule.component in CONSTRAINT_VALIDATORS]
    return pandas.concat(frames, ignore_index=True) if frames else _empty()


def _and(context, rule):
    """sh:and — every nested shape must hold; each nested violation is reported."""
    focus_ids = getattr(rule, "focus_ids", None)
    violations = pandas.concat([_run_nested(context, alternative, focus_ids)
                                for alternative in rule.params], ignore_index=True)
    violations["VIOLATION_TYPE"] = "sh:and"
    return violations


def _or(context, rule):
    """sh:or — a focus node violates only when EVERY alternative is violated."""
    focus_ids = getattr(rule, "focus_ids", None)
    violating_sets = [set(_run_nested(context, alternative, focus_ids)["ID"])
                      for alternative in rule.params]
    focus = sorted(set.intersection(*violating_sets)) if violating_sets else []
    return _frame(rule, focus, None, "no sh:or alternative is satisfied")


def _xone(context, rule):
    """sh:xone — a focus node violates unless exactly one alternative is satisfied."""
    focus_ids = getattr(rule, "focus_ids", None)
    ids = list(context.focus(rule) if focus_ids is None else focus_ids)
    violating_sets = [set(_run_nested(context, alternative, focus_ids)["ID"])
                      for alternative in rule.params]
    bad = [focus for focus in ids
           if sum(focus not in violated for violated in violating_sets) != 1]
    return _frame(rule, bad, None, "exactly one sh:xone alternative must be satisfied")


def _node(context, rule):
    """sh:node — every value at the path must conform to the referenced shape.

    The referenced shape was expanded into nested IR rows at compile time; here
    they run with the referenced value nodes as focus. A value node that
    produces any nested violation makes the referring focus node violate sh:node.
    """
    rows = context.path_rows(rule)
    referenced = rows["PATH_VALUE"].unique()
    if not len(referenced):
        return _empty()
    non_conforming = set(_run_nested(context, rule.params["rows"], referenced)["ID"])
    bad = rows[rows["PATH_VALUE"].isin(non_conforming)]
    return _frame(rule, bad["FOCUS"], bad["PATH_VALUE"],
                  f"value does not conform to shape {rule.params['shape']}")


def _not(context, rule):
    """sh:not — a focus node violates when it SATISFIES the negated shape."""
    violating = set(_run_nested(context, rule.params, getattr(rule, "focus_ids", None))["ID"])
    ids = context.focus(rule)
    conforming = [focus for focus in ids if focus not in violating]
    return _frame(rule, conforming, None, "node conforms to the negated shape")


# component → validator. The polars and duckdb compilers implement this same
# registry against CompiledShapes.plans.
CONSTRAINT_VALIDATORS = {
    "sh:sparql": _sparql,
    "sh:node": _node,
    "sh:minCount": _min_count,
    "sh:maxCount": _max_count,
    "sh:datatype": _datatype,
    "sh:pattern": _pattern,
    "sh:minLength": _min_length,
    "sh:maxLength": _max_length,
    "sh:minInclusive": _range(lambda v, limit: v < limit, "less than the minimum"),
    "sh:maxInclusive": _range(lambda v, limit: v > limit, "greater than the maximum"),
    "sh:minExclusive": _range(lambda v, limit: v <= limit, "not greater than the exclusive minimum"),
    "sh:maxExclusive": _range(lambda v, limit: v >= limit, "not less than the exclusive maximum"),
    "sh:in": _in,
    "sh:hasValue": _has_value,
    "sh:class": _class,
    "triplets:range": _schema_range,
    "sh:nodeKind": _node_kind,
    "sh:equals": _equals,
    "sh:disjoint": _disjoint,
    "sh:lessThan": _pair_compare(lambda a, b: ~(a < b), "less than"),
    "sh:lessThanOrEquals": _pair_compare(lambda a, b: ~(a <= b), "less than or equal to"),
    "sh:closed": _closed,
    "sh:and": _and,
    "sh:or": _or,
    "sh:not": _not,
    "sh:xone": _xone,
}


def validate(data, compiled, rdf_map=None, scope=None, components=None, max_workers=None,
             undefined_namespace=CIM_NS, **kwargs):
    """Validate triplet data against the compiled constraint table.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    compiled : CompiledShapes
        From ``triplets.validation.compile`` — this engine executes the IR.
    rdf_map : dict or str, optional
        Used only for the sh:sparql constraints (typed literals in the queried
        graph); every other component reads the raw lexical forms directly.
    scope : iterable of INSTANCE_ID, optional
        Validate only rows of these instances.
    components : iterable of component names, optional
        Restrict to a subset (e.g. ``("sh:datatype",)`` for the lexical
        supplement run next to pyshacl). None = everything implemented.
    max_workers : int, optional
        Run the sh:sparql constraint queries in parallel processes (fork).
        None = sequential.
    """
    data = _to_pandas(data)
    if scope is not None:
        data = data[data["INSTANCE_ID"].isin(list(scope))]

    rules = compiled.ir
    if components is not None:
        rules = rules[rules["component"].isin(components)]
    skipped = set(rules["component"]) - set(CONSTRAINT_VALIDATORS)
    if skipped:
        logger.debug("pandas engine skips components: %s (pyshacl covers them)", ", ".join(sorted(skipped)))

    context = _Context(data, rdf_map, undefined_namespace)
    selected = [rule for rule in rules.itertuples() if rule.component in CONSTRAINT_VALIDATORS]
    sparql_rules = [rule for rule in selected if rule.component == "sh:sparql"]

    frames = [CONSTRAINT_VALIDATORS[rule.component](context, rule)
              for rule in selected if rule.component != "sh:sparql"]
    if sparql_rules and max_workers:
        frames += shacl_sparql.run_parallel(context.sparql, [(rule, context.focus(rule)) for rule in sparql_rules],
                                            max_workers)
    else:
        frames += [_sparql(context, rule) for rule in sparql_rules]

    if not frames:
        return _empty()
    return shacl_sparql.dedupe_invalid(pandas.concat(frames, ignore_index=True))


def _to_pandas(data):
    """Any supported flavor → pandas triplet DataFrame."""
    from .._engine_detect import to_pandas
    return to_pandas(data)
