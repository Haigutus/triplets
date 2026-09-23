"""duckdb flavor of :mod:`triplets.iri` — SQL expression text in, SQL expression text out, same names.

Only the names the duckdb SHACL engine consumes exist so far; add the rest as
consumers appear, holding them to ``tests/test_iri.py`` like the other flavors.
"""


def local_term(column):
    """After the last ``#``, else after the last ``/`` — the scalar ``local_term`` rule as SQL."""
    return f"list_extract(string_split(list_extract(string_split({column}, '#'), -1), '/'), -1)"
