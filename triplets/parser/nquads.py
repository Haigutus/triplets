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

from .._engine_detect import to_return_type
from ..iri import TYPE_KEY, iri_pandas, load_rdf_map, value_types


# N-Triples string escapes: \uXXXX / \UXXXXXXXX and single-char (\n \t \" \\ ...)
_ESCAPE = re.compile(r'\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})|\\(.)')
_CONTROL_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}


def read_nquads(source, return_type="pandas", rdf_map=None, type_key=TYPE_KEY):
    """Parse N-Quads (or N-Triples) into a triplet DataFrame.

    Parameters
    ----------
    source : str/Path, bytes, or file-like
        Path to a .nq/.nt file, the serialized content as bytes/str, or an
        open file object (text or binary).
    return_type : str, default "pandas"
        "pandas", "polars", or "arrow".
    rdf_map : dict or str, optional
        Export schema. With it every VALUE IRI is shortened by its KEY's schema entry
        (Association → node rule, Enumeration → name rule) instead of the schema-free guess.
    type_key : str, default "Type"
        KEY for ``rdf:type`` triples. N-Quads has no typed-node shorthand, so every type is
        an ``rdf:type`` triple: ``"Type"`` for CIM data (one type per object), another key
        (e.g. ``"type"``) to read every type as an ordinary statement.

    Returns
    -------
    Triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID] — the round-trip inverse
    of export_to_nquads (datatype annotations drop to lexical form, which is
    the triplets convention: everything is a string). Lines without a graph
    term (N-Triples) get INSTANCE_ID null.
    """
    import pyarrow
    import pyarrow.compute as pc
    lines = pc.utf8_trim_whitespace(pyarrow.array(_read_text(source).splitlines(), type=pyarrow.string()))
    lines = lines.filter(pc.and_(pc.not_equal(lines, ""), pc.invert(pc.starts_with(lines, "#"))))
    kinds = value_types(load_rdf_map(rdf_map)) if rdf_map is not None else None
    return to_return_type(terms_to_triplets(_split_terms(lines), kinds, type_key), return_type)


def _split_terms(lines):
    """``subject predicate object [graph] .`` lines → term frame [ID, KEY, VALUE, INSTANCE_ID].

    Subject and predicate are whitespace-free, so the first two splits are
    safe; the object may contain spaces inside a quoted literal, so the graph
    is recognised from the right: a trailing whitespace-free ``<iri>`` /
    ``_:bnode`` term preceded by something. All Arrow compute — ~10x the
    speed of a lazy-quantifier regex extract over the same lines.
    """
    import pyarrow
    import pyarrow.compute as pc
    _STRING = pandas.ArrowDtype(pyarrow.string())
    if len(lines) == 0:                                   # comment-only / blank buffer
        return pandas.DataFrame({column: pandas.Series([], dtype=_STRING) for column in ("ID", "KEY", "VALUE", "INSTANCE_ID")})
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
    graph_shaped = pc.and_(pc.or_(pc.and_(pc.starts_with(tail, "<"), pc.ends_with(tail, ">")),
                                  pc.starts_with(tail, "_:")),
                           pc.invert(pc.match_substring(tail, '"')))
    # the split is only a graph when what precedes it is a complete object term: a
    # quoted literal must be closed (`"…"`, `"…"^^<dt>`, `"…"@lang`) — otherwise the
    # "graph" is the tail of a literal containing whitespace ("hello _:world")
    literal_closed = pc.or_(pc.or_(pc.ends_with(head, '"'), pc.ends_with(head, ">")),
                            pc.match_substring_regex(head, r'"@[A-Za-z0-9-]+$'))
    object_complete = pc.or_(pc.invert(pc.starts_with(head, '"')), literal_closed)
    has_graph = pc.and_(pc.and_(pc.not_equal(head, ""), graph_shaped), object_complete)
    return pandas.DataFrame({
        "ID": pandas.Series(subject, dtype=_STRING),
        "KEY": pandas.Series(predicate, dtype=_STRING),
        "VALUE": pandas.Series(pc.if_else(has_graph, head, rest), dtype=_STRING),
        "INSTANCE_ID": pandas.Series(pc.if_else(has_graph, tail, pyarrow.scalar(None, pyarrow.string())),
                                     dtype=_STRING),
    })


def terms_to_triplets(frame, kinds=None, type_key=TYPE_KEY):
    """N-Triples-form term columns → triplet values, converted in place.

    frame carries columns [ID, KEY, VALUE] and optionally INSTANCE_ID (the
    graph term; a missing column → None, a constructed graph has no source
    instance). Term shapes: ``<iri>``, ``_:bnode``, ``"literal"`` (optionally
    with a ``^^<datatype>`` / ``@lang`` suffix — dropped, the value keeps its
    lexical form; string escapes decoded), or bare turtle-shorthand
    numbers/booleans. IRI escapes the exporter wrote (``%20``) are decoded once,
    then the triplets.iri column rules apply: ``local_id`` for ID / INSTANCE_ID,
    ``local_key`` for KEY (``rdf:type`` → *type_key*), ``local_value`` for VALUE —
    kind ``class`` on type rows, else by *kinds* (``iri.value_types`` of a schema).
    Without a schema a guessed VALUE that is a subject of the same frame shortens
    like its ID, so the reference still joins it.
    """
    subjects = iri_pandas.decode_iri(_term(frame["ID"]))
    frame["ID"] = iri_pandas.local_id(subjects)
    frame["KEY"] = iri_pandas.by_distinct(frame["KEY"], lambda keys: iri_pandas.local_key(
        iri_pandas.decode_iri(_term(keys)), type_key))
    value = frame["VALUE"]
    quoted = value.str.startswith('"', na=False).astype(bool)
    # drop a ^^<datatype> / @lang suffix, then slice the quotes off — cheaper
    # than one back-reference regex over the whole literal
    unquoted = _unescape(value.str.replace(r'"(\^\^<[^>]*>|@[\w-]+)?$', '"', regex=True).str.slice(1, -1))
    objects = iri_pandas.decode_iri(_term(value).where(~quoted))
    kind = frame["KEY"].map({key: kind for key, kind in (kinds or {}).items() if kind != "literal"})
    kind = kind.astype(object).where(frame["KEY"] != type_key, "class")
    local = iri_pandas.local_value(objects, kind)
    if kinds is None:       # the guess cannot tell an enum from a reference to an http#… object
        local = local.mask(_subject_references(objects, subjects, kind), iri_pandas.local_id(objects))
    frame["VALUE"] = unquoted.where(quoted, local)
    graphs = iri_pandas.by_distinct(frame["INSTANCE_ID"], lambda graphs: iri_pandas.local_id(
        iri_pandas.decode_iri(_term(graphs)))) if "INSTANCE_ID" in frame.columns else None
    # no graph term anywhere (N-Triples, CONSTRUCT results) → plain None column,
    # the same shape every SPARQL engine returns for a constructed graph
    frame["INSTANCE_ID"] = graphs if graphs is not None and graphs.notna().any() else None
    return frame


def _subject_references(objects, subjects, kind):
    """Guessed rows the guess took as a name (http + "#") yet are a subject of this frame —
    looked up in Arrow only on those rows (pandas ``isin`` on arrow strings is ~50x slower)."""
    import pyarrow
    import pyarrow.compute as pc
    candidates = (kind.isna() & objects.str.startswith("http", na=False)
                  & objects.str.contains("#", regex=False, na=False)).astype(bool)
    hit = pandas.Series(False, index=objects.index)
    if candidates.any():
        found = pc.is_in(pyarrow.array(objects[candidates], type=pyarrow.string()),
                         value_set=pc.unique(pyarrow.array(subjects, type=pyarrow.string())))
        hit[candidates] = found.to_numpy(zero_copy_only=False)
    return hit


def _term(column):
    """``<iri>`` / ``_:bnode`` → bare text as read; decoding and shortening are per column (triplets.iri).

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
    if isinstance(source, str) and (source == "" or "\n" in source or source.lstrip().startswith(("<", "_:", "#"))):
        return source  # serialized content, not a path
    if hasattr(source, "read"):
        content = source.read()
        return content.decode("utf-8") if isinstance(content, bytes) else content
    return Path(source).read_text(encoding="utf-8")
