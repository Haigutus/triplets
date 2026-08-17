"""SHACL polars engine — compiled-IR executor (performance).

Lazy by construction: every constraint becomes one LazyFrame plan against a
shared base, and everything executes in a single ``polars.collect_all`` —
parallel across plans, with common subplans (the per-path filters, the Type
scan) eliminated once. Shared indices (per-class focus IDs, the set of all
IDs) are materialized eagerly once and reused across plans — speed over
memory, per the engine guidance in docs/validation.md. No Python UDFs, no
per-constraint collects, no streaming.

Semantics are identical to the pandas engine (same IR, same canonical
violations schema, same lexical-form datatype deviation — see shacl_pandas).
The rare nested/query components (sh:or, sh:and, sh:not, sh:node, sh:sparql —
a handful of rows even in the real profiles) are delegated to the pandas
implementations, so coverage is complete while the hot path stays lazy.

Per-rule plan builders are cached in ``CompiledShapes.plans["polars"]`` — the
IR is split and normalized once per compiled shapes, then only re-bound to
new data.

Note: sh:pattern runs on Rust regex (no lookarounds). The ENTSO-E profiles
use plain character-class patterns; exotic patterns belong to pyshacl.
"""
import logging

import pandas
import polars

from .shacl_report import VIOLATION_COLUMNS
from .shacl_ir import split_rules, FALLBACK_COMPONENTS  # noqa: F401 — re-exported
from .shacl_pandas import DATATYPES, _REFERENCE_LIKE, SchemaKind

logger = logging.getLogger(__name__)

_COLUMNS = ("ID", "KEY", "VALUE", "INSTANCE_ID")


class _Context(SchemaKind):
    """Lazy base + eagerly materialized shared indices (built once per validate)."""

    def __init__(self, frame, rdf_map=None):
        self.base = frame.lazy()
        self.rdf_map = rdf_map
        type_rows = frame.filter(polars.col("KEY") == "Type").select("VALUE", "ID")
        # (ID, CLASS) pairs — the class-membership side of the batched joins
        self.membership = type_rows.rename({"VALUE": "CLASS"}).lazy()
        self._class_ids = {key[0]: part["ID"] for key, part
                           in type_rows.partition_by("VALUE", as_dict=True).items()}
        # is_in wants the membership collection as one list value (imploded);
        # precompute per class so thousands of rule plans share them.
        self._class_ids_imploded = {key: ids.implode()
                                    for key, ids in self._class_ids.items()}
        self._all_ids = frame["ID"].unique().implode()
        self._frame = frame
        self._subjects = {}   # KEY → (ids, imploded) for sh:targetSubjectsOf focus

    def class_ids(self, target_class):
        """Flat ID Series (plan *data*, e.g. focus_frame)."""
        return self._class_ids.get(target_class, _NO_IDS)

    def class_ids_in(self, target_class):
        """Imploded ID Series for is_in membership tests."""
        return self._class_ids_imploded.get(target_class, _NO_IDS_IMPLODED)

    @property
    def all_ids(self):
        return self._all_ids

    def _subjects_of(self, key):
        if key not in self._subjects:
            ids = self._frame.filter(polars.col("KEY") == key)["ID"].unique()
            self._subjects[key] = (ids, ids.implode())
        return self._subjects[key]

    def focus_ids(self, rule):
        """Flat focus ID Series (plan data, e.g. focus_frame)."""
        if getattr(rule, "target_kind", "class") == "subjectsOf":
            return self._subjects_of(rule.target_class)[0]
        return self.class_ids(rule.target_class)

    def focus_ids_in(self, rule):
        """Imploded focus IDs for is_in membership tests."""
        if getattr(rule, "target_kind", "class") == "subjectsOf":
            return self._subjects_of(rule.target_class)[1]
        return self.class_ids_in(rule.target_class)

    def path_rows(self, rule):
        """The rule's path as a lazy (FOCUS, PATH_VALUE) plan, restricted to the rule's focus."""
        rows = self.base.filter(polars.col("KEY") == rule.path)
        ids = self.focus_ids_in(rule)
        if rule.inverse:
            plan = rows.filter(polars.col("VALUE").is_in(ids)).select(
                polars.col("VALUE").alias("FOCUS"), polars.col("ID").alias("PATH_VALUE"))
        else:
            plan = rows.filter(polars.col("ID").is_in(ids)).select(
                polars.col("ID").alias("FOCUS"), polars.col("VALUE").alias("PATH_VALUE"))
        if getattr(rule, "via_type", False):
            # ( assoc rdf:type ) sequence path: PATH_VALUE = referenced object's
            # type; a target without a Type row yields no value node (inner join)
            plan = (plan.join(self.membership, left_on="PATH_VALUE", right_on="ID")
                    .select("FOCUS", polars.col("CLASS").alias("PATH_VALUE")))
        return plan

    def focus_frame(self, rule):
        return polars.LazyFrame({"FOCUS": self.focus_ids(rule)},
                                schema={"FOCUS": polars.Utf8})


_NO_IDS = polars.Series("ID", [], dtype=polars.Utf8)
_NO_IDS_IMPLODED = _NO_IDS.implode()


def _emit(plan, rule, message, violation_type=None, severity=None, value=True):
    """Attach the canonical violation columns to a (FOCUS[, PATH_VALUE]) plan."""
    return plan.select(
        polars.col("FOCUS").alias("ID"),
        polars.lit(rule.path, dtype=polars.Utf8).alias("KEY"),
        (polars.col("PATH_VALUE") if value else polars.lit(None, dtype=polars.Utf8)).alias("VALUE"),
        polars.lit(violation_type or rule.component).alias("VIOLATION_TYPE"),
        polars.lit(rule.message or message, dtype=polars.Utf8).alias("MESSAGE"),
        polars.lit(severity or rule.severity).alias("SEVERITY"),
        polars.lit(rule.shape_id).alias("SOURCE_SHAPE"),
    )


# ── plan builders: (context, rule) → LazyFrame in the violations schema ──────

def _counts(context, rule):
    return context.path_rows(rule).group_by("FOCUS").len()


def _min_count(context, rule):
    plan = (context.focus_frame(rule)
            .join(_counts(context, rule), on="FOCUS", how="left")
            .filter(polars.col("len").fill_null(0) < rule.params))
    return _emit(plan, rule, f"{rule.path} occurs fewer than {rule.params} time(s)", value=False)


def _max_count(context, rule):
    plan = _counts(context, rule).filter(polars.col("len") > rule.params)
    return _emit(plan, rule, f"{rule.path} occurs more than {rule.params} time(s)", value=False)


def _datatype(context, rule):
    """Two-level lexical check in one plan; VIOLATION_TYPE/SEVERITY/MESSAGE are
    conditional columns (invalid form → sh:datatype, narrower form → Warning)."""
    spec = DATATYPES.get(str(rule.params).removeprefix("xsd:"))
    if spec is None:
        return None
    valid_pattern, warn_pattern = spec

    invalid = ~polars.col("PATH_VALUE").str.contains(f"^(?:{valid_pattern})$")
    warned = (polars.col("PATH_VALUE").str.contains(f"^(?:{warn_pattern})$") & ~invalid
              if warn_pattern else polars.lit(False))
    return (context.path_rows(rule)
            .with_columns(invalid.alias("_invalid"), warned.alias("_warned"))
            .filter(polars.col("_invalid") | polars.col("_warned"))
            .select(
                polars.col("FOCUS").alias("ID"),
                polars.lit(rule.path).alias("KEY"),
                polars.col("PATH_VALUE").alias("VALUE"),
                polars.when(polars.col("_invalid")).then(polars.lit("sh:datatype"))
                      .otherwise(polars.lit("triplets:lexicalForm")).alias("VIOLATION_TYPE"),
                polars.when(polars.col("_invalid"))
                      .then(polars.lit(rule.message or f"value is not a valid {rule.params}"))
                      .otherwise(polars.lit(f"lexical form is narrower than the declared {rule.params} "
                                            "(e.g. integer form for a float)")).alias("MESSAGE"),
                polars.when(polars.col("_invalid")).then(polars.lit(rule.severity))
                      .otherwise(polars.lit("Warning")).alias("SEVERITY"),
                polars.lit(rule.shape_id).alias("SOURCE_SHAPE"),
            ))


def _pattern(context, rule):
    plan = context.path_rows(rule).filter(~polars.col("PATH_VALUE").str.contains(rule.params))
    return _emit(plan, rule, f"value does not match pattern '{rule.params}'")


def _min_length(context, rule):
    plan = context.path_rows(rule).filter(polars.col("PATH_VALUE").str.len_chars() < rule.params)
    return _emit(plan, rule, f"value is shorter than {rule.params} characters")


def _max_length(context, rule):
    plan = context.path_rows(rule).filter(polars.col("PATH_VALUE").str.len_chars() > rule.params)
    return _emit(plan, rule, f"value is longer than {rule.params} characters")


def _range(comparison, description):
    """Numeric range builder factory; non-castable values are the datatype check's job."""
    def builder(context, rule):
        numeric = polars.col("PATH_VALUE").cast(polars.Float64, strict=False)
        plan = context.path_rows(rule).filter(comparison(numeric, rule.params))  # null → dropped
        return _emit(plan, rule, f"value is {description} {rule.params}")
    return builder


def _in(context, rule):
    local = (polars.col("PATH_VALUE").str.split("#").list.last()
             .str.split("/").list.last())
    allowed = [str(value) for value in rule.params]
    plan = context.path_rows(rule).filter(~local.is_in(allowed))
    return _emit(plan, rule, f"value is not one of {sorted(allowed)}")


def _has_value(context, rule):
    having = (context.path_rows(rule)
              .filter(polars.col("PATH_VALUE") == str(rule.params))
              .select("FOCUS"))
    plan = context.focus_frame(rule).join(having, on="FOCUS", how="anti")
    return _emit(plan, rule, f"{rule.path} does not have required value '{rule.params}'", value=False)


def _class(context, rule):
    plan = context.path_rows(rule).filter(~polars.col("PATH_VALUE").is_in(context.class_ids_in(rule.params)))
    return _emit(plan, rule, f"referenced object is not of class {rule.params}")


def _schema_range(context, rule):
    """triplets:range — ANY of the target's types in the allowed set conforms
    (issue #100); targets without a Type row are silent."""
    allowed = polars.concat([polars.Series(context.class_ids(cls)) for cls in rule.params],
                            rechunk=True).implode()
    typed = context.membership.select(polars.col("ID")).unique().collect()["ID"].implode()
    plan = (context.path_rows(rule)
            .filter(polars.col("PATH_VALUE").is_in(typed)
                    & ~polars.col("PATH_VALUE").is_in(allowed)))
    return _emit(plan, rule, f"reference target is not one of {rule.params}")


def _node_kind(context, rule):
    if rule.params not in ("IRI", "Literal"):
        logger.debug("sh:nodeKind %s not checkable on triplets — skipped (%s)", rule.params, rule.shape_id)
        return None
    # via_type value nodes are the referenced objects' types — always IRIs
    kind = "iri" if getattr(rule, "via_type", False) else context.key_kind(rule.path)
    if kind is not None:                     # schema decides for the whole path
        if (kind == "iri") == (rule.params == "IRI"):
            return None                      # every value conforms — no plan at all
        plan = context.path_rows(rule)       # every value violates
    else:                                    # value-form heuristic
        is_iri = (polars.col("PATH_VALUE").str.contains(f"^(?:{_REFERENCE_LIKE.pattern})$")
                  | polars.col("PATH_VALUE").is_in(context.all_ids))
        plan = context.path_rows(rule).filter(~is_iri if rule.params == "IRI" else is_iri)
    return _emit(plan, rule, f"value is not of node kind sh:{rule.params}")


def _pair(context, rule, other_path):
    left = context.path_rows(rule)
    right = (context.base.filter(polars.col("KEY") == other_path)
             .filter(polars.col("ID").is_in(context.focus_ids_in(rule)))
             .select(polars.col("ID").alias("FOCUS"), polars.col("VALUE").alias("OTHER")))
    return left, right


def _equals(context, rule):
    """sh:equals is set equality per focus node: a value present at only one
    of the two properties is a violation; matching multi-valued sets conform."""
    left, right = _pair(context, rule, rule.params)
    right = right.rename({"OTHER": "PATH_VALUE"})
    plan = polars.concat([left.join(right, on=["FOCUS", "PATH_VALUE"], how="anti"),
                          right.join(left, on=["FOCUS", "PATH_VALUE"], how="anti")])
    return _emit(plan, rule, f"{rule.path} does not equal {rule.params}")


def _disjoint(context, rule):
    left, right = _pair(context, rule, rule.params)
    plan = (left.join(right, on="FOCUS")
            .filter(polars.col("PATH_VALUE") == polars.col("OTHER")))
    return _emit(plan, rule, f"{rule.path} shares a value with {rule.params}")


def _pair_compare(operator, description):
    """sh:lessThan(-OrEquals): violation when the comparison does not hold
    (incl. non-numeric pairs — null comparisons count as failed, like pandas)."""
    def builder(context, rule):
        left, right = _pair(context, rule, rule.params)
        a = polars.col("PATH_VALUE").cast(polars.Float64, strict=False)
        b = polars.col("OTHER").cast(polars.Float64, strict=False)
        plan = left.join(right, on="FOCUS").filter(~operator(a, b).fill_null(False))
        return _emit(plan, rule, f"{rule.path} is not {description} {rule.params}")
    return builder


def _closed(context, rule):
    allowed = list(set(rule.params) | {"Type"})
    plan = (context.base
            .filter(polars.col("ID").is_in(context.focus_ids_in(rule))
                    & ~polars.col("KEY").is_in(allowed))
            .select(
                polars.col("ID"),
                polars.col("KEY"),
                polars.col("VALUE"),
                polars.lit(rule.component).alias("VIOLATION_TYPE"),
                polars.lit(rule.message or "property is not allowed on a closed shape").alias("MESSAGE"),
                polars.lit(rule.severity).alias("SEVERITY"),
                polars.lit(rule.shape_id).alias("SOURCE_SHAPE"),
            ))
    return plan


# component → lazy plan builder (FALLBACK_COMPONENTS run via shacl_pandas)
PLAN_BUILDERS = {
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
    "sh:lessThan": _pair_compare(lambda a, b: a < b, "less than"),
    "sh:lessThanOrEquals": _pair_compare(lambda a, b: a <= b, "less than or equal to"),
    "sh:closed": _closed,
}


# ── batched builders: (context, rules) → [LazyFrame] ─────────────────────────
# One join-plan per component instead of one plan per rule: the rules become
# plan *data* (a frame joined on KEY + class membership) and the per-rule
# metadata rides along as columns, so the output rows are identical to the
# per-rule builders'. On the real Equipment profiles this turns ~4,300 plans
# into a handful (~6x less time in polars, plan construction O(components)).
# Inverse rules and the tail components keep the per-rule path.

_RULE_COLUMNS = ["KEY", "CLASS", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"]


def _rules_frame(rules, message, **extra):
    """The rules of one component as plan data (message = default-text builder)."""
    columns = {
        "KEY": [rule.path for rule in rules],
        "CLASS": [rule.target_class for rule in rules],
        "MESSAGE": [rule.message or message(rule) for rule in rules],
        "SEVERITY": [rule.severity for rule in rules],
        "SOURCE_SHAPE": [rule.shape_id for rule in rules],
    }
    columns |= {name: [fn(rule) for rule in rules] for name, fn in extra.items()}
    return polars.LazyFrame(columns)


def _batch_path_rows(context, rules_frame):
    """Every rule's path rows in one plan: (row × matching rule), focus in class."""
    return (context.base.join(rules_frame, on="KEY")
            .join(context.membership, on=["ID", "CLASS"], how="semi"))


def _batch_emit(plan, violation_type, value=True):
    return plan.select(
        polars.col("ID"),
        polars.col("KEY"),
        (polars.col("VALUE") if value else polars.lit(None, dtype=polars.Utf8)).alias("VALUE"),
        polars.lit(violation_type, dtype=polars.Utf8).alias("VIOLATION_TYPE"),
        polars.col("MESSAGE"), polars.col("SEVERITY"), polars.col("SOURCE_SHAPE"))


def _batch_max_count(context, rules):
    rules_frame = _rules_frame(rules, lambda r: f"{r.path} occurs more than {r.params} time(s)",
                               PARAM=lambda r: float(r.params))
    plan = (_batch_path_rows(context, rules_frame)
            .group_by(["ID", *_RULE_COLUMNS, "PARAM"]).len()
            .filter(polars.col("len") > polars.col("PARAM")))
    return [_batch_emit(plan, "sh:maxCount", value=False)]


def _batch_min_count(context, rules):
    rules_frame = _rules_frame(rules, lambda r: f"{r.path} occurs fewer than {r.params} time(s)",
                               PARAM=lambda r: float(r.params))
    keys = ["ID", *_RULE_COLUMNS, "PARAM"]
    counts = _batch_path_rows(context, rules_frame).group_by(keys).len()
    universe = context.membership.join(rules_frame, on="CLASS")   # every focus × its rules
    plan = (universe.join(counts, on=keys, how="left")
            .filter(polars.col("len").fill_null(0) < polars.col("PARAM")))
    return [_batch_emit(plan, "sh:minCount", value=False)]


def _batch_datatype(context, rules):
    """One plan per distinct datatype spec (a handful) — the regex is per spec,
    the two-level message/severity/type stay per rule via conditional columns."""
    groups = {}
    for rule in rules:
        spec = DATATYPES.get(str(rule.params).removeprefix("xsd:"))
        if spec is not None:
            groups.setdefault(spec, []).append(rule)
    plans = []
    for (valid_pattern, warn_pattern), group in groups.items():
        rules_frame = _rules_frame(
            group, lambda r: f"value is not a valid {r.params}",
            MESSAGE_WARN=lambda r: (f"lexical form is narrower than the declared {r.params} "
                                    "(e.g. integer form for a float)"))
        invalid = ~polars.col("VALUE").str.contains(f"^(?:{valid_pattern})$")
        warned = (polars.col("VALUE").str.contains(f"^(?:{warn_pattern})$") & ~invalid
                  if warn_pattern else polars.lit(False))
        plans.append(
            _batch_path_rows(context, rules_frame)
            .with_columns(invalid.alias("_invalid"), warned.alias("_warned"))
            .filter(polars.col("_invalid") | polars.col("_warned"))
            .select(
                polars.col("ID"), polars.col("KEY"), polars.col("VALUE"),
                polars.when(polars.col("_invalid")).then(polars.lit("sh:datatype"))
                      .otherwise(polars.lit("triplets:lexicalForm")).alias("VIOLATION_TYPE"),
                polars.when(polars.col("_invalid")).then(polars.col("MESSAGE"))
                      .otherwise(polars.col("MESSAGE_WARN")).alias("MESSAGE"),
                polars.when(polars.col("_invalid")).then(polars.col("SEVERITY"))
                      .otherwise(polars.lit("Warning")).alias("SEVERITY"),
                polars.col("SOURCE_SHAPE"),
            ))
    return plans


def _batch_node_kind(context, rules):
    """Schema-decided rules collapse to skip / violate-all; heuristic rules
    share one is_iri expression per expected kind (two plans at most)."""
    message = lambda r: f"value is not of node kind sh:{r.params}"   # noqa: E731
    always, heuristic = [], []
    for rule in rules:
        if rule.params not in ("IRI", "Literal"):
            logger.debug("sh:nodeKind %s not checkable on triplets — skipped (%s)",
                         rule.params, rule.shape_id)
            continue
        kind = context.key_kind(rule.path)
        if kind is not None:                 # schema decides for the whole path
            if (kind == "iri") == (rule.params == "IRI"):
                continue                     # every value conforms — no plan at all
            always.append(rule)              # every value violates
        else:
            heuristic.append(rule)
    plans = []
    if always:
        plans.append(_batch_emit(
            _batch_path_rows(context, _rules_frame(always, message)), "sh:nodeKind"))
    for expected in ("IRI", "Literal"):
        group = [rule for rule in heuristic if rule.params == expected]
        if not group:
            continue
        is_iri = (polars.col("VALUE").str.contains(f"^(?:{_REFERENCE_LIKE.pattern})$")
                  | polars.col("VALUE").is_in(context.all_ids))
        plan = (_batch_path_rows(context, _rules_frame(group, message))
                .filter(~is_iri if expected == "IRI" else is_iri))
        plans.append(_batch_emit(plan, "sh:nodeKind"))
    return plans


def _batch_in(context, rules):
    """Anti-join against the exploded allowed values: a path row with no
    (rule, allowed-value) match is a violation."""
    rules_frame = _rules_frame(
        rules, lambda r: f"value is not one of {sorted(str(v) for v in r.params)}")
    allowed = polars.LazyFrame({
        "KEY": [rule.path for rule in rules for _ in rule.params],
        "CLASS": [rule.target_class for rule in rules for _ in rule.params],
        "SOURCE_SHAPE": [rule.shape_id for rule in rules for _ in rule.params],
        "_LOCAL": [str(value) for rule in rules for value in rule.params],
    })
    local = (polars.col("VALUE").str.split("#").list.last()
             .str.split("/").list.last())
    plan = (_batch_path_rows(context, rules_frame)
            .with_columns(local.alias("_LOCAL"))
            .join(allowed, on=["KEY", "CLASS", "SOURCE_SHAPE", "_LOCAL"], how="anti"))
    return [_batch_emit(plan, "sh:in")]


BATCH_BUILDERS = {
    "sh:maxCount": _batch_max_count,
    "sh:minCount": _batch_min_count,
    "sh:datatype": _batch_datatype,
    "sh:nodeKind": _batch_node_kind,
    "sh:in": _batch_in,
}


def validate(data, compiled, rdf_map=None, scope=None, components=None, max_workers=None, **kwargs):
    """Validate triplet data against the compiled constraint table (lazy polars).

    Parameters mirror shacl_pandas.validate. Vectorized components run as one
    ``polars.collect_all`` over per-constraint LazyFrame plans; the nested and
    query components (FALLBACK_COMPONENTS) are delegated to the pandas engine
    so results are identical across both.
    """
    frame = _to_polars(data)
    if scope is not None:
        frame = frame.filter(polars.col("INSTANCE_ID").is_in([str(s) for s in scope]))

    if "polars" not in compiled.plans:   # setdefault would re-split on every call
        compiled.plans["polars"] = split_rules(compiled.ir, PLAN_BUILDERS, FALLBACK_COMPONENTS, "polars")
    vectorized, fallback, _ = compiled.plans["polars"]
    if components is not None:
        vectorized = [rule for rule in vectorized if rule.component in components]
        fallback = [rule for rule in fallback if rule.component in components]

    context = _Context(frame, rdf_map)
    batched, plans = {}, []
    for rule in vectorized:
        # batching joins on class membership — subjectsOf targets and two-hop
        # (via_type) paths take the per-rule path
        if (rule.component in BATCH_BUILDERS and not rule.inverse
                and not getattr(rule, "via_type", False)
                and getattr(rule, "target_kind", "class") == "class"):
            batched.setdefault(rule.component, []).append(rule)
        elif (plan := PLAN_BUILDERS[rule.component](context, rule)) is not None:
            plans.append(plan)
    for component, rules in batched.items():
        plans.extend(BATCH_BUILDERS[component](context, rules))
    results = polars.collect_all(plans)
    frames = [result for result in results if result.height]

    if frames:
        violations = polars.concat(frames).to_pandas()
        # canonical schema convention: object columns, nulls as None (like the other engines)
        violations = violations.astype(object).where(violations.notna(), None)
    else:
        violations = pandas.DataFrame(columns=VIOLATION_COLUMNS)

    if fallback:
        # pass the already-converted-and-scoped frame — the pandas engine
        # would otherwise redo both from the original input
        from . import shacl_pandas
        supplement = shacl_pandas.validate(frame, compiled, rdf_map=rdf_map,
                                           components={rule.component for rule in fallback},
                                           max_workers=max_workers)
        violations = pandas.concat([violations, supplement], ignore_index=True)
    return violations


def _to_polars(data):
    """Any supported flavor → polars DataFrame with Utf8 columns."""
    from .._engine_detect import to_polars
    frame = to_polars(data)
    if list(frame.columns) != list(_COLUMNS):
        frame = frame.select(list(_COLUMNS))
    return frame.with_columns([polars.col(column).cast(polars.Utf8) for column in _COLUMNS])
