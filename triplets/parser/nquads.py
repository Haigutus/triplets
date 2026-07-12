"""N-Quads reader — the inverse of the N-Quads export.

read_nquads turns N-Quads / N-Triples text (path, bytes, or file-like) back
into a triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID], applying the inverse
of the export conventions (triplets.export.nquads_utils): urn:uuid: stripped,
CIM namespace shortened, rdf:type → 'Type', datatype / language annotations
dropped (values keep their lexical form), graph → INSTANCE_ID (absent → None).

Everything is vectorized pandas string ops. terms_to_triplets is the shared
term-level conversion, also used by the SPARQL engines to decode
CONSTRUCT/DESCRIBE results: the qlever engine feeds it Arrow-decoded term
columns, the oxigraph engine feeds read_nquads its serialized result bytes.
"""
import re

from pathlib import Path

import pandas

from ..export.nquads_utils import CIM_NS, RDF_TYPE

_UUID_PREFIX = "urn:uuid:"

# subject predicate object [graph] . — subject/predicate are space-free terms,
# the object may contain spaces inside a quoted literal, the graph is an IRI.
_QUAD_PATTERN = r'^\s*(\S+)\s+(\S+)\s+(.+?)(?:\s+(<[^>]*>))?\s*\.\s*$'

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
    lines = pandas.Series(_read_text(source).splitlines(), dtype="object")
    lines = lines[lines.str.strip().ne("") & ~lines.str.lstrip().str.startswith("#")]

    terms = lines.str.extract(_QUAD_PATTERN)
    terms.columns = ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    bad = terms["ID"].isna()
    if bad.any():
        raise ValueError(f"not N-Quads: {lines[bad].iloc[0][:200]!r}")

    return _to_return_type(terms_to_triplets(terms).reset_index(drop=True), return_type)


def terms_to_triplets(frame):
    """N-Triples-form term columns → triplet values, converted in place.

    frame carries columns [ID, KEY, VALUE] and optionally INSTANCE_ID (the
    graph term; a missing column → None, a constructed graph has no source
    instance). Term shapes: ``<iri>``, ``_:bnode``, ``"literal"`` (optionally
    with a ``^^<datatype>`` / ``@lang`` suffix — dropped, the value keeps its
    lexical form; string escapes decoded), or bare turtle-shorthand
    numbers/booleans. IRIs lose urn:uuid: and the CIM namespace,
    rdf:type → 'Type'.
    """
    rdf_type = frame["KEY"] == f"<{RDF_TYPE}>"
    frame["ID"] = _iri(frame["ID"])
    frame["KEY"] = _iri(frame["KEY"]).mask(rdf_type, "Type")
    unquoted = _unescape(
        frame["VALUE"].str.replace(r'(?s)^"(.*)"(\^\^<[^>]*>|@[\w-]+)?$', r"\1", regex=True))
    frame["VALUE"] = unquoted.where(frame["VALUE"].str.startswith('"'), _iri(frame["VALUE"]))
    graphs = _iri(frame["INSTANCE_ID"]) if "INSTANCE_ID" in frame.columns else None
    frame["INSTANCE_ID"] = graphs.where(graphs.notna(), None) if graphs is not None else None
    return frame


def _iri(column):
    return (column.str.replace(r"^<(.*)>$", r"\1", regex=True)
            .str.removeprefix("_:").str.removeprefix(_UUID_PREFIX).str.removeprefix(CIM_NS))


def _unescape(column):
    """Decode N-Triples string escapes — only rows that carry a backslash."""
    escaped = column.str.contains("\\", regex=False).fillna(False)
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


def _to_return_type(frame, return_type):
    if return_type == "polars":
        import polars
        return polars.from_pandas(frame)
    if return_type == "arrow":
        import pyarrow
        return pyarrow.Table.from_pandas(frame, preserve_index=False)
    return frame
