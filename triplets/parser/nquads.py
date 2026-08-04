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

from .._engine_detect import to_return_type
from ..export.nquads_utils import CIM_NS, RDF_TYPE

_UUID_PREFIX = "urn:uuid:"

# subject predicate object [graph] . — subject/predicate are space-free terms,
# the object is a quoted literal (escapes honored, so embedded "<" or "\""
# cannot bleed into the graph term) with an optional ^^/@ suffix, or a
# space-free term; the graph is an IRI or a blank-node label.
_QUAD_PATTERN = (r'^\s*(\S+)\s+(\S+)\s+'
                 r'("(?:[^"\\]|\\.)*"(?:\^\^<[^>]*>|@[\w-]+)?|\S+)'
                 r'(?:\s+(<[^>]*>|_:[^\s"<]+))?\s*\.\s*$')

# N-Triples string escapes: \uXXXX / \UXXXXXXXX and single-char (\n \t \" \\ ...)
_ESCAPE = re.compile(r'\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})|\\(.)')
_CONTROL_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}


def read_nquads(source, return_type="pandas", context=False):
    """Parse N-Quads (or N-Triples) into a triplet DataFrame.

    Parameters
    ----------
    source : str/Path, bytes, or file-like
        Path to a .nq/.nt file, the serialized content as bytes/str, or an
        open file object (text or binary).
    return_type : str, default "pandas"
        "pandas", "polars", or "arrow".
    context : bool, default False
        Capture term metadata into a ``context`` struct column instead of
        discarding it (the parser_rdfxml convention): stripped IRI prefixes,
        term kind, ``@lang`` / ``^^datatype`` annotations. Values still keep
        their lexical form either way.

    Returns
    -------
    Triplet DataFrame [ID, KEY, VALUE, INSTANCE_ID] — the round-trip inverse
    of export_to_nquads (without ``context``, datatype annotations drop to
    lexical form, which is the triplets convention: everything is a string).
    Lines without a graph term (N-Triples) get INSTANCE_ID null.
    """
    lines = pandas.Series(_read_text(source).splitlines(), dtype="object")
    lines = lines[lines.str.strip().ne("") & ~lines.str.lstrip().str.startswith("#")]

    terms = lines.str.extract(_QUAD_PATTERN)
    terms.columns = ["ID", "KEY", "VALUE", "INSTANCE_ID"]
    bad = terms["ID"].isna()
    if bad.any():
        raise ValueError(f"not N-Quads: {lines[bad].iloc[0][:200]!r}")

    captured = _capture_context(terms) if context else None
    frame = terms_to_triplets(terms).reset_index(drop=True)
    if captured is not None:
        frame["context"] = captured
    return to_return_type(frame, return_type)


def _capture_context(terms):
    """Raw quad terms → the parser_rdfxml context struct (ArrowDtype column).

    Mirrors what terms_to_triplets strips: prefixes land in *_PREFIX fields
    (``prefix + column`` reconstructs the term), the object's shape becomes
    rdf_value_kind, and ``@lang`` / ``^^<datatype>`` suffixes are extracted.
    Position-only fields (rdf_id_source, source_line …) stay null."""
    import pyarrow
    from .rdfxml_lxml_arrow import CONTEXT_FIELDS, context_struct_type

    def prefixes(column):
        """The chain _iri strips, recorded: (prefix, is_blank)."""
        remaining = column.str.replace(r"^<(.*)>$", r"\1", regex=True)
        prefix = pandas.Series("", index=column.index, dtype="object")
        for rule in ("_:", _UUID_PREFIX, CIM_NS):
            hit = remaining.str.startswith(rule).fillna(False)
            prefix = prefix.mask(hit, prefix + rule)
            remaining = remaining.mask(hit, remaining.str.slice(len(rule)))
        return prefix.mask(prefix == "", None).where(column.notna(), None)

    value = terms["VALUE"]
    is_literal = value.str.startswith('"')
    is_blank = value.str.startswith("_:")
    kind = pandas.Series("iri", index=terms.index, dtype="object").mask(
        is_literal, "literal").mask(is_blank, "blank")
    annotated = value.where(is_literal)
    fields = {
        "ID_PREFIX": prefixes(terms["ID"]),
        "KEY_PREFIX": prefixes(terms["KEY"]).mask(
            terms["KEY"] == f"<{RDF_TYPE}>", None),
        "VALUE_PREFIX": prefixes(value).where(~is_literal, None),
        "rdf_value_kind": kind,
        "rdf_language": annotated.str.extract(r'@([A-Za-z0-9-]+)$', expand=False),
        "rdf_datatype": annotated.str.extract(r'\^\^<([^>]*)>$', expand=False),
        "rdf_node_id": terms["ID"].str.removeprefix("_:").where(
            terms["ID"].str.startswith("_:"), None),
    }
    arrays = [pyarrow.array(fields.get(name, [None] * len(terms)),
                            type=pyarrow.int32() if name == "source_line" else pyarrow.string(),
                            from_pandas=True)
              for name in CONTEXT_FIELDS]
    struct = pyarrow.StructArray.from_arrays(arrays, fields=list(context_struct_type()))
    return pandas.arrays.ArrowExtensionArray(struct)


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
