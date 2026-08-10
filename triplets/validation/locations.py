"""Exact source locations for violations — one grep-style pass over the CIM/XML.

RDF objects carry no text coordinates through the triplets frame, so locations
are recovered from the original files at *export* time: each source file is
read once and every ``rdf:ID`` / ``rdf:about`` definition is indexed with its
line number — one sequential pass per file, cost independent of how many IDs
are wanted. For each violation, the violated property's own position is then
searched inside that object's text window, so the annotation lands on the
offending property element, not just the object.

``locate_violations`` is the public pass: it stamps ``LOCATION_COLUMNS``
(SOURCE_URI, SOURCE_LINE, SOURCE_COLUMN, SOURCE_COLUMN_END) onto a violations
frame — both the SARIF and the sh:ValidationReport exports call it when given
``sources=``, and it is exposed as ``violations.shacl.locate(sources=...)``.

The parse/validate hot paths are untouched: nothing here runs unless sources
are handed to an export. When the same object is defined in several files
(``rdf:about`` continuation across profiles), the first definition wins — a
violated KEY living in a later profile file falls back to the definition
position. Lines and columns are 1-based; columns count UTF-16 code units
(SARIF's unit — what GitHub anchors annotations on). A position is a bounded
single-line region: the element's ``<`` through the end of its line —
SARIF viewers cannot display a region without an end.
"""
import re
import logging

logger = logging.getLogger(__name__)

LOCATION_COLUMNS = ["SOURCE_URI", "SOURCE_LINE", "SOURCE_COLUMN", "SOURCE_COLUMN_END"]

# an object definition: <cim:Breaker rdf:ID="_uuid"> / rdf:about="#_uuid" /
# rdf:about="urn:uuid:uuid" — group(1) is the bare ID, triplets conventions
_DEFINITION = re.compile(rb'rdf:(?:ID|about)="(?:urn:uuid:)?[#_]*([^"]+)"')


def locate(wanted, sources):
    """Find the wanted objects in the source XML.

    Parameters
    ----------
    wanted : dict {ID: set of KEYs}
        Objects to locate; the KEY sets name the violated properties whose
        own lines are worth pinpointing (may be empty).
    sources : str/Path to .xml/.rdf/.zip, file-like, or a list of those —
        the same shapes ``parse()``/``read_rdf`` accept.

    Returns
    -------
    dict {ID: {"uri": str, "startLine": int, "startColumn": int,
               "endColumn": int, "keyLines": {KEY: (line, column, end_column)}}}
        IDs not present in the sources are absent from the result.
    """
    from ..parser.utils import find_all_xml

    remaining = {str(object_id).encode(): object_id for object_id in wanted}
    located = {}
    for source in find_all_xml(sources):
        if not remaining:
            break
        uri, text = _read(source)
        matches = list(_DEFINITION.finditer(text))
        line, previous = 1, 0
        for position, match in enumerate(matches):
            line += text.count(b"\n", previous, match.start())
            previous = match.start()
            object_id = remaining.pop(match.group(1), None)
            if object_id is None:
                continue
            window_end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
            element_start = max(text.rfind(b"<", 0, match.start()), 0)
            start_column, end_column = _span(text, element_start)
            located[object_id] = {
                "uri": uri, "startLine": line, "startColumn": start_column,
                "endColumn": end_column,
                "keyLines": _key_lines(text, match.start(), window_end, line, wanted[object_id]),
            }
    if remaining:
        logger.debug("%d violating object(s) not found in the sources", len(remaining))
    return located


def locate_violations(violations, sources):
    """Stamp LOCATION_COLUMNS onto a violations frame — one locate() pass.

    Per row (ID, KEY): the violated property element's own position when it
    was found inside the object's text window, else the object definition's;
    rows whose object is not in the sources get nulls.
    """
    import pandas

    wanted = {}
    for object_id, key in zip(violations["ID"], violations["KEY"]):
        if not pandas.isna(object_id):
            wanted.setdefault(str(object_id), set()).add(None if pandas.isna(key) else str(key))
    located = locate(wanted, sources)

    def position(object_id, key):
        entry = located.get(str(object_id)) if not pandas.isna(object_id) else None
        if entry is None:
            return None, None, None, None
        key = None if pandas.isna(key) else str(key)
        line, column, end = entry["keyLines"].get(
            key, (entry["startLine"], entry["startColumn"], entry["endColumn"]))
        return entry["uri"], line, column, end

    frame = violations.copy()
    positions = [position(object_id, key) for object_id, key in zip(frame["ID"], frame["KEY"])]
    if positions:
        stamped = pandas.DataFrame(positions, columns=LOCATION_COLUMNS,
                                   index=frame.index).astype(object)
        frame[LOCATION_COLUMNS] = stamped.where(stamped.notna(), None)
    else:
        frame[LOCATION_COLUMNS] = None
    return frame


def _span(text, start):
    """1-based UTF-16 columns of *start* and of its line's end.

    The region is *start* through the end of the line (endColumn points one
    past the last character, per SARIF); UTF-16 code units are SARIF's column
    unit — byte columns overshoot on non-ASCII lines and break annotation.
    """
    line_start = text.rfind(b"\n", 0, start) + 1
    line_end = text.find(b"\n", start)
    line_end = len(text) if line_end == -1 else line_end
    line_end -= text.endswith(b"\r", line_start, line_end)

    def utf16_column(position):
        prefix = text[line_start:position].decode("utf-8", "replace")
        return len(prefix.encode("utf-16-le")) // 2 + 1

    return utf16_column(start), utf16_column(line_end)


def _key_lines(text, definition_start, window_end, definition_line, keys):
    """(line, column, end_column) of each violated property element inside the object's window."""
    lines = {}
    for key in keys:
        if not key or key == "Type":         # the type is the definition element itself
            continue
        pattern = rb"<[\w.-]+:" + re.escape(str(key).encode()) + rb"[\s>/]"
        match = re.search(pattern, text[definition_start:window_end])
        if match is not None:
            start = definition_start + match.start()
            lines[key] = (definition_line + text.count(b"\n", definition_start, start),
                          *_span(text, start))
    return lines


def _read(source):
    """(uri, bytes) for a path or a file-like (zip members from find_all_xml)."""
    if hasattr(source, "read"):
        return str(getattr(source, "name", "<memory>")), source.read()
    with open(source, "rb") as file:
        return str(source), file.read()
