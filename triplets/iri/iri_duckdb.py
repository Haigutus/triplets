"""duckdb flavor of :mod:`triplets.iri` — SQL expression text in, SQL expression text out, same names.

Only the names the duckdb SHACL engine uses; each is held to ``tests/test_iri.py``
like the other flavors.
"""


from . import GUESS_NAME_RE, ID_PREFIX_RE, SPLIT_RE


def _local(column):
    return f"regexp_replace({column}, '{SPLIT_RE.pattern}', '')"


def local_value(column, kind=None):
    """The scalar ``local_value`` rule as SQL; *kind*: None (guess) or one of ``VALUE_KINDS``."""
    if kind in ("class", "enum"):
        return _local(column)
    node = f"regexp_replace({column}, '{ID_PREFIX_RE.pattern}', '')"
    return node if kind == "reference" else f"regexp_replace({node}, '{GUESS_NAME_RE.pattern}', '')"
