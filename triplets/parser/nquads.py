"""N-Quads reader — the inverse of the N-Quads export.

read_nquads turns N-Quads / N-Triples text (path, bytes, or file-like) back
into a triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID], applying the inverse
of the export conventions (triplets.iri): ID / INSTANCE_ID via local_id,
KEY via local_key (rdf:type → 'Type'), VALUE via local_value (urn:uuid: and any
http(s) #fragment namespace shortened), datatype / language annotations
dropped (values keep their lexical form), graph → INSTANCE_ID (absent → None).

Line splitting runs in Arrow compute, term conversion in vectorized pandas
string ops on arrow-backed columns. terms_to_triplets is the shared
term-level conversion, also used by the SPARQL engines to decode
CONSTRUCT/DESCRIBE results: the qlever engine feeds it Arrow-decoded term
columns, the oxigraph engine feeds read_nquads its serialized result bytes.
"""
import re

from pathlib import Path

import pandas
import pyarrow
import pyarrow.compute as pc

from .._engine_detect import to_return_type
from ..iri import iri_pandas

_STRING = pandas.ArrowDtype(pyarrow.string())

# N-Triples string escapes: \uXXXX / \UXXXXXXXX and single-char (\n \t \" \\ ...)
_ESCAPE = re.compile(r'\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})|\\(.)')
_CONTROL_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}


def read_nquads(source, return_type="pandas"):
    """Parse N-Quads (or N-Triples) into a triplet DataFrame.

    Parameters
    ----------
    source : str/Path, bytes, or file-like
        Path to a .nq/.nt file, the serialized content as bytes/str, or an
        open file object (text or binary).
    return_type : str, default "pandas"
        "pandas", "polars", or "arrow".

    Returns
    -------
    Triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID] — the round-trip inverse
    of export_to_nquads (datatype annotations drop to lexical form, which is
    the triplets convention: everything is a string). Lines without a graph
    term (N-Triples) get INSTANCE_ID null.
    """
    lines = pc.utf8_trim_whitespace(pyarrow.array(_read_text(source).splitlines(), type=pyarrow.string()))
    lines = lines.filter(pc.and_(pc.not_equal(lines, ""), pc.invert(pc.starts_with(lines, "#"))))
    return to_return_type(terms_to_triplets(_split_terms(lines)), return_type)


def _split_terms(lines):
    """``subject predicate object [graph] .`` lines → term frame [ID, KEY, VALUE, INSTANCE_ID].

    Subject and predicate are whitespace-free, so the first two splits are
    safe; the object may contain spaces inside a quoted literal, so the graph
    is recognised from the right: a trailing whitespace-free ``<iri>`` /
    ``_:bnode`` term preceded by something. All Arrow compute — ~10x the
    speed of a lazy-quantifier regex extract over the same lines.
    """
    ends = pc.ends_with(lines, ".")
    if not pc.all(ends).as_py():
        raise ValueError(f"not N-Quads: {lines.filter(pc.invert(ends))[0].as_py()[:200]!r}")
    body = pc.utf8_rtrim_whitespace(pc.utf8_slice_codeunits(lines, 0, -1))
    parts = pc.split_pattern_regex(body, r"\s+", max_splits=2)
    complete = pc.equal(pc.list_value_length(parts), 3)
    if not pc.all(complete).as_py():
        raise ValueError(f"not N-Quads: {lines.filter(pc.invert(complete))[0].as_py()[:200]!r}")
    subject, predicate, rest = (pc.list_element(parts, index) for index in range(3))

    if pc.any(pc.match_substring(rest, "\t")).as_py():   # tab separators are legal but rare: regex path
        tail = pc.extract_regex(rest, r"(?P<tail>\S+)$").field("tail")
        head = pc.replace_substring_regex(rest, r"\s+\S+$", "")
    else:                                                 # rsplit on the last space (a leading " " keeps it two-part)
        split = pc.split_pattern(pc.binary_join_element_wise(" ", rest, ""), " ", max_splits=1, reverse=True)
        head, tail = pc.list_element(split, 0), pc.list_element(split, 1)
    head = pc.utf8_trim_whitespace(head)
    graph_shaped = pc.or_(pc.and_(pc.starts_with(tail, "<"), pc.ends_with(tail, ">")),
                          pc.starts_with(tail, "_:"))
    has_graph = pc.and_(pc.not_equal(head, ""), graph_shaped)
    return pandas.DataFrame({
        "ID": pandas.Series(subject, dtype=_STRING),
        "KEY": pandas.Series(predicate, dtype=_STRING),
        "VALUE": pandas.Series(pc.if_else(has_graph, head, rest), dtype=_STRING),
        "INSTANCE_ID": pandas.Series(pc.if_else(has_graph, tail, pyarrow.scalar(None, pyarrow.string())),
                                     dtype=_STRING),
    })


def terms_to_triplets(frame):
    """N-Triples-form term columns → triplet values, converted in place.

    frame carries columns [ID, KEY, VALUE] and optionally INSTANCE_ID (the
    graph term; a missing column → None, a constructed graph has no source
    instance). Term shapes: ``<iri>``, ``_:bnode``, ``"literal"`` (optionally
    with a ``^^<datatype>`` / ``@lang`` suffix — dropped, the value keeps its
    lexical form; string escapes decoded), or bare turtle-shorthand
    numbers/booleans. IDs lose urn:uuid:, VALUE IRIs also any http(s)
    #fragment namespace, rdf:type → 'Type' (triplets.iri rules).
    """
    frame["ID"] = iri_pandas.local_id(_term(frame["ID"]))
    frame["KEY"] = iri_pandas.local_key(_term(frame["KEY"]))
    value = frame["VALUE"]
    quoted = value.str.startswith('"', na=False).astype(bool)
    # drop a ^^<datatype> / @lang suffix, then slice the quotes off — cheaper
    # than one back-reference regex over the whole literal
    unquoted = _unescape(value.str.replace(r'"(\^\^<[^>]*>|@[\w-]+)?$', '"', regex=True).str.slice(1, -1))
    frame["VALUE"] = unquoted.where(quoted, iri_pandas.local_value(_term(value)))
    graphs = iri_pandas.local_id(_term(frame["INSTANCE_ID"])) if "INSTANCE_ID" in frame.columns else None
    # no graph term anywhere (N-Triples, CONSTRUCT results) → plain None column,
    # the same shape every SPARQL engine returns for a constructed graph
    frame["INSTANCE_ID"] = graphs if graphs is not None and graphs.notna().any() else None
    return frame


def _term(column):
    """``<iri>`` / ``_:bnode`` → bare text; shortening is per column (triplets.iri).

    Slice + mask, not ``^<(.*)>$`` → ``\1``: the back-reference regex is ~7x
    slower on arrow-backed strings."""
    wrapped = (column.str.startswith("<", na=False) & column.str.endswith(">", na=False)).astype(bool)
    return column.str.slice(1, -1).where(wrapped, column).str.removeprefix("_:")


def _unescape(column):
    """Decode N-Triples string escapes — only rows that carry a backslash."""
    escaped = column.str.contains("\\", regex=False, na=False)
    if not escaped.any():
        return column
    column = column.copy()
    column[escaped] = column[escaped].map(
        lambda value: _ESCAPE.sub(_escape_char, value))
    return column


def _escape_char(match):
    unicode_hex = match.group(1) or match.group(2)
    if unicode_hex:
        return chr(int(unicode_hex, 16))
    return _CONTROL_ESCAPES.get(match.group(3), match.group(3))


def _read_text(source):
    if isinstance(source, bytes):
        return source.decode("utf-8")
    if isinstance(source, str) and ("\n" in source or source.lstrip().startswith(("<", "_:", "#"))):
        return source  # serialized content, not a path
    if hasattr(source, "read"):
        content = source.read()
        return content.decode("utf-8") if isinstance(content, bytes) else content
    return Path(source).read_text(encoding="utf-8")
