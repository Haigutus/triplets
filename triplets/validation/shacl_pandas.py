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

sh:sparql constraints are delegated to triplets.sparql (whichever engine is
available — rdflib today, qlever later): the data is loaded into one rdflib
dataset, each constraint runs as one SELECT with the focus nodes bound via
VALUES, and ``max_workers`` runs the constraint queries in parallel processes
(fork — copy-on-write shares the dataset; threads don't help GIL-bound rdflib).

Known limits:
- sh:node — nested shape references; used 0 times across the entire CGMES
  SHACL library, so deliberately not implemented (pyshacl covers it if ever
  encountered; an IR row is kept and logged)
- sh:nodeKind — implemented as a heuristic: triplets store every value as a
  string, so a value counts as reference-like (IRI) when it is a known ID, a
  UUID, has a URI scheme, or has the enum local-name form (``PhaseCode.ABC``)
"""
import re
import logging
import multiprocessing

from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace

import numpy
import pandas

from ..export.nquads_utils import CIM_NS, make_subject
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
    # string / anyURI / unlisted types: every lexical form is valid — no check
}

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_REFERENCE_LIKE = re.compile(rf"(?:{_UUID}|\w+://\S+|urn:\S+|[A-Za-z]\w*\.\w+)$")

_NO_IDS = numpy.array([], dtype=object)


class _Context:
    """Shared per-validation state: data, IR, and memoized lookups.

    Hundreds of IR rules hit the same per-class indices — build them once here
    instead of once per rule (the polars/duckdb compilers follow the same shape).
    """

    def __init__(self, data, ir, rdf_map=None):
        self.data = data
        self.ir = ir
        self.rdf_map = rdf_map
        self._by_key = None
        self._class_ids = None
        self._all_ids = None
        self._key_metadata = None
        self._dataset = None

    def dataset(self):
        """rdflib dataset for the sh:sparql constraints — loaded once, reused per query."""
        if self._dataset is None:
            from .._rdflib_loader import load_dataset
            self._dataset = load_dataset(self.data, rdf_map=self.rdf_map)
        return self._dataset

    def key_rows(self, key):
        """All rows at *key* — the data is grouped by KEY once, not per rule."""
        if self._by_key is None:
            self._by_key = {group: frame for group, frame
                            in self.data.groupby("KEY", observed=True, sort=False)}
        return self._by_key.get(key, self.data.iloc[0:0])

    def class_ids(self, target_class):
        """IDs of all instances of *target_class*."""
        if self._class_ids is None:
            self._class_ids = {value: frame["ID"].unique() for value, frame
                               in self.key_rows("Type").groupby("VALUE", observed=True, sort=False)}
        return self._class_ids.get(target_class, _NO_IDS)

    @property
    def all_ids(self):
        """Every subject ID in the data (for reference-likeness checks)."""
        if self._all_ids is None:
            self._all_ids = set(self.data["ID"].unique())
        return self._all_ids

    def key_kind(self, key):
        """"literal" / "iri" / None — what the export schema says values at *key* are.

        Mirrors the N-Quads exporter's classification (build_key_metadata): a
        key with a schema datatype (incl. xsd:string) holds literals; an
        enumeration key holds IRIs; anyURI/unknown keys return None (decide by
        value form). Without rdf_map everything is None.
        """
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

    def path_rows(self, rule):
        """The rule's path as (FOCUS, PATH_VALUE) pairs, restricted to the target class.

        Normal path:  FOCUS = row ID,   PATH_VALUE = row VALUE.
        Inverse path: FOCUS = row VALUE (the referenced target-class object),
                      PATH_VALUE = row ID (the referencing object).
        """
        ids = self.class_ids(rule.target_class)
        rows = self.key_rows(rule.path)
        if rule.inverse:
            rows = rows[rows["VALUE"].isin(ids)]
            return pandas.DataFrame({"FOCUS": rows["VALUE"].to_numpy(),
                                     "PATH_VALUE": rows["ID"].to_numpy()})
        rows = rows[rows["ID"].isin(ids)]
        return pandas.DataFrame({"FOCUS": rows["ID"].to_numpy(),
                                 "PATH_VALUE": rows["VALUE"].to_numpy()})

    def pair_rows(self, rule, other_path):
        """FOCUS + both paths' values, for the pair constraints (equals/disjoint/lessThan)."""
        left = self.path_rows(rule)
        other = SimpleNamespace(target_class=rule.target_class, path=other_path, inverse=False)
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
            .reindex(context.class_ids(rule.target_class), fill_value=0))


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
    local = rows["PATH_VALUE"].astype(str).str.split("#").str[-1].str.split("/").str[-1]
    bad = ~local.isin(allowed)
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"value is not one of {sorted(allowed)}")


def _has_value(context, rule):
    rows = context.path_rows(rule)
    having = rows.loc[rows["PATH_VALUE"].astype(str) == str(rule.params), "FOCUS"]
    missing = pandas.Index(context.class_ids(rule.target_class)).difference(having)
    return _frame(rule, missing, None, f"{rule.path} does not have required value '{rule.params}'")


def _class(context, rule):
    rows = context.path_rows(rule)
    of_class = context.class_ids(rule.params)
    bad = ~rows["PATH_VALUE"].isin(of_class)
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"referenced object is not of class {rule.params}")


def _node_kind(context, rule):
    """sh:nodeKind — triplets store term kinds nowhere, so kind is decided like the
    N-Quads exporter decides it: by the schema when rdf_map names the path (a
    datatype key is Literal even when its values look like UUIDs — e.g.
    IdentifiedObject.mRID; an enum key is IRI), by value form otherwise."""
    if rule.params not in ("IRI", "Literal"):  # BlankNode & *Or* combinations — not expressible
        logger.debug("sh:nodeKind %s not checkable on triplets — skipped (%s)", rule.params, rule.shape_id)
        return _empty()

    rows = context.path_rows(rule)
    kind = context.key_kind(rule.path)
    if kind is not None:
        is_iri = pandas.Series(kind == "iri", index=rows.index)
    else:
        values = rows["PATH_VALUE"].astype(str)
        is_iri = values.str.fullmatch(_REFERENCE_LIKE) | values.isin(context.all_ids)
    bad = ~is_iri if rule.params == "IRI" else is_iri
    return _frame(rule, rows.loc[bad, "FOCUS"], rows.loc[bad, "PATH_VALUE"],
                  f"value is not of node kind sh:{rule.params}")


# ── property pair constraints ────────────────────────────────────────────────

def _equals(context, rule):
    left, right = context.pair_rows(rule, rule.params)
    merged = left.merge(right, on="FOCUS", how="outer")
    bad = merged[(merged["PATH_VALUE"] != merged["OTHER_VALUE"])
                 | merged["PATH_VALUE"].isna() | merged["OTHER_VALUE"].isna()]
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
    allowed = set(rule.params) | {"Type"}
    ids = context.class_ids(rule.target_class)
    rows = context.data[context.data["ID"].isin(ids) & ~context.data["KEY"].isin(allowed)]
    frame = _frame(rule, rows["ID"], rows["VALUE"], "property is not allowed on a closed shape")
    frame["KEY"] = rows["KEY"].to_numpy()
    return frame


# ── sh:sparql: delegated to triplets.sparql ──────────────────────────────────

def _sparql_query_text(rule, focus_ids):
    """Final executable query: prefixes + SELECT with $PATH substituted and the
    focus nodes bound via VALUES ($this is the SPARQL variable ?this)."""
    select = rule.params["select"]
    if rule.params["path"]:
        select = select.replace("$PATH", f"<{rule.params['path']}>")
    values = " ".join(make_subject(focus) for focus in focus_ids)
    closing = select.rfind("}")
    return rule.params["prefixes"] + f"{select[:closing]} VALUES ?this {{ {values} }} {select[closing:]}"


def _sparql_violations(rule, result):
    """Each SELECT result row is one violation: $this = focus node, ?value = value."""
    if result is None or len(result) == 0 or "this" not in result.columns:
        return _empty()
    focus = result["this"].astype(str).str.removeprefix("urn:uuid:")
    values = (result["value"].astype(str).str.removeprefix("urn:uuid:").str.removeprefix(CIM_NS)
              if "value" in result.columns else None)
    return _frame(rule, focus, values, "sparql constraint violated")


def _sparql(context, rule):
    from .. import sparql
    focus_ids = context.class_ids(rule.target_class)
    if not len(focus_ids):
        return _empty()
    result = sparql.query(context.dataset(), _sparql_query_text(rule, focus_ids))
    return _sparql_violations(rule, result)


# fork inherits this by copy-on-write — the dataset is never pickled per task
_FORK_DATASET = None


def _sparql_worker(query_text):
    from .. import sparql
    return sparql.query(_FORK_DATASET, query_text)


def _sparql_parallel(context, rules, max_workers):
    """Run the sh:sparql constraint queries in parallel processes.

    rdflib query evaluation is GIL-bound pure Python, so threads don't help;
    fork gives copy-on-write sharing of the loaded dataset (Linux). One task
    per constraint query.
    """
    global _FORK_DATASET
    tasks = [(rule, _sparql_query_text(rule, context.class_ids(rule.target_class)))
             for rule in rules if len(context.class_ids(rule.target_class))]
    if not tasks:
        return []
    _FORK_DATASET = context.dataset()
    try:
        with ProcessPoolExecutor(max_workers=max_workers,
                                 mp_context=multiprocessing.get_context("fork")) as pool:
            results = list(pool.map(_sparql_worker, [text for _, text in tasks]))
    finally:
        _FORK_DATASET = None
    return [_sparql_violations(rule, result) for (rule, _), result in zip(tasks, results)]


# ── logical operators (params: nested IR row-dict lists) ────────────────────

def _run_nested(context, row_dicts):
    """Validate nested IR rows; returns their concatenated violations."""
    frames = [CONSTRAINT_VALIDATORS[row["component"]](context, SimpleNamespace(**row))
              for row in row_dicts if row["component"] in CONSTRAINT_VALIDATORS]
    return pandas.concat(frames, ignore_index=True) if frames else _empty()


def _and(context, rule):
    """sh:and — every nested shape must hold; each nested violation is reported."""
    violations = pandas.concat([_run_nested(context, alternative) for alternative in rule.params],
                               ignore_index=True)
    violations["VIOLATION_TYPE"] = "sh:and"
    return violations


def _or(context, rule):
    """sh:or — a focus node violates only when EVERY alternative is violated."""
    violating_sets = [set(_run_nested(context, alternative)["ID"]) for alternative in rule.params]
    focus = sorted(set.intersection(*violating_sets)) if violating_sets else []
    return _frame(rule, focus, None, "no sh:or alternative is satisfied")


def _not(context, rule):
    """sh:not — a focus node violates when it SATISFIES the negated shape."""
    violating = set(_run_nested(context, rule.params)["ID"])
    ids = context.class_ids(rule.target_class)
    conforming = [focus for focus in ids if focus not in violating]
    return _frame(rule, conforming, None, "node conforms to the negated shape")


# component → validator. sh:node is intentionally absent (unused across the
# CGMES SHACL library; pyshacl covers it). The polars and duckdb compilers
# implement this same registry against CompiledShapes.plans.
CONSTRAINT_VALIDATORS = {
    "sh:sparql": _sparql,
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
    "sh:nodeKind": _node_kind,
    "sh:equals": _equals,
    "sh:disjoint": _disjoint,
    "sh:lessThan": _pair_compare(lambda a, b: ~(a < b), "less than"),
    "sh:lessThanOrEquals": _pair_compare(lambda a, b: ~(a <= b), "less than or equal to"),
    "sh:closed": _closed,
    "sh:and": _and,
    "sh:or": _or,
    "sh:not": _not,
}


def validate(data, compiled, rdf_map=None, scope=None, components=None, max_workers=None, **kwargs):
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

    context = _Context(data, compiled.ir, rdf_map)
    selected = [rule for rule in rules.itertuples() if rule.component in CONSTRAINT_VALIDATORS]
    sparql_rules = [rule for rule in selected if rule.component == "sh:sparql"]

    frames = [CONSTRAINT_VALIDATORS[rule.component](context, rule)
              for rule in selected if rule.component != "sh:sparql"]
    if sparql_rules and max_workers:
        frames += _sparql_parallel(context, sparql_rules, max_workers)
    else:
        frames += [_sparql(context, rule) for rule in sparql_rules]

    if not frames:
        return _empty()
    return pandas.concat(frames, ignore_index=True)


def _to_pandas(data):
    """Any supported flavor → pandas triplet DataFrame."""
    from .._rdflib_loader import _to_loadable
    from .._engine_detect import is_polars

    data = _to_loadable(data)  # arrow / duckdb → pandas
    if is_polars(data):
        return data.to_pandas()
    return data
