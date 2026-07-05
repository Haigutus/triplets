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
``(data, rule) → violations DataFrame``, registered in CONSTRAINT_VALIDATORS.
Currently only sh:datatype is implemented — this file doubles as the template
the full pandas engine (phase B) grows into; polars/duckdb compilers follow
the same IR rows.
"""
import re
import logging

import pandas

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


def _datatype(data, rule):
    """sh:datatype — lexical-form check on the raw VALUE strings.

    Deviates from pyshacl by design: reports invalid lexical forms as
    sh:datatype and valid-but-non-canonical forms as triplets:lexicalForm.
    """
    spec = DATATYPES.get(str(rule.params).removeprefix("xsd:"))
    if spec is None:
        return _empty()
    valid_pattern, warn_pattern = spec

    rows = _path_rows(data, rule)
    values = rows["VALUE"].astype(str)
    invalid = ~values.str.fullmatch(valid_pattern, flags=re.ASCII)
    warned = values.str.fullmatch(warn_pattern, flags=re.ASCII) & ~invalid if warn_pattern else False

    return pandas.concat([
        _violations(rows[invalid], rule, "sh:datatype", rule.severity,
                    rule.message or f"value is not a valid {rule.params}"),
        _violations(rows[warned] if warn_pattern else rows.iloc[0:0], rule, "triplets:lexicalForm", "Warning",
                    f"lexical form is narrower than the declared {rule.params} (e.g. integer form for a float)"),
    ], ignore_index=True)


def _path_rows(data, rule):
    """Rows of *data* at the rule's path, restricted to the target class instances."""
    ids = data.loc[(data["KEY"] == "Type") & (data["VALUE"] == rule.target_class), "ID"]
    return data[(data["KEY"] == rule.path) & data["ID"].isin(ids)]


def _violations(rows, rule, violation_type, severity, message):
    return pandas.DataFrame({
        "ID": rows["ID"].to_numpy(),
        "KEY": rule.path,
        "VALUE": rows["VALUE"].to_numpy(),
        "VIOLATION_TYPE": violation_type,
        "MESSAGE": message,
        "SEVERITY": severity,
        "SOURCE_SHAPE": rule.shape_id,
    }, columns=VIOLATION_COLUMNS)


def _empty():
    return pandas.DataFrame(columns=VIOLATION_COLUMNS)


# component → validator; phase B fills this from the dev_shacl prototypes
# (pandas_shacl.py / validators.py) — one entry per IR component.
CONSTRAINT_VALIDATORS = {
    "sh:datatype": _datatype,
}


def validate(data, compiled, rdf_map=None, scope=None, components=None, **kwargs):
    """Validate triplet data against the compiled constraint table.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    compiled : CompiledShapes
        From ``triplets.validation.compile`` — this engine executes the IR.
    rdf_map : ignored
        This engine reads the raw lexical forms; no graph is built.
    scope : iterable of INSTANCE_ID, optional
        Validate only rows of these instances.
    components : iterable of component names, optional
        Restrict to a subset (e.g. ``("sh:datatype",)`` for the lexical
        supplement run next to pyshacl). None = everything implemented.
    """
    data = _to_pandas(data)
    if scope is not None:
        data = data[data["INSTANCE_ID"].isin(list(scope))]

    rules = compiled.ir
    if components is not None:
        rules = rules[rules["component"].isin(components)]

    frames = [CONSTRAINT_VALIDATORS[rule.component](data, rule)
              for rule in rules.itertuples()
              if rule.component in CONSTRAINT_VALIDATORS]
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
