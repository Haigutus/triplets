"""Exact source locations for violations — one grep-style pass over the CIM/XML.

RDF objects carry no text coordinates through the triplets frame, so locations
are recovered from the original files at *export* time: each source file is
read once and every ``rdf:ID`` / ``rdf:about`` definition is indexed with its
line number — one sequential pass per file, cost independent of how many IDs
are wanted. For each violation, the violated property's own position is then
searched inside that object's text window, so the annotation lands on the
offending property element, not just the object.

``locate_violations`` is the public pass: it stamps ``LOCATION_COLUMNS``
(SOURCE_URI, SOURCE_LINE, SOURCE_COLUMN) onto a violations frame — both the
SARIF and the sh:ValidationReport exports call it when given ``sources=``,
and it is exposed as ``violations.shacl.locate(sources=...)``.

The parse/validate hot paths are untouched: nothing here runs unless sources
are handed to an export. When the same object is defined in several files
(``rdf:about`` continuation across profiles), the first definition wins — a
violated KEY living in a later profile file falls back to the definition
position. Lines and columns are 1-based; columns count bytes (a multi-byte
UTF-8 character earlier on the line shifts them).
"""
import re
import logging

logger = logging.getLogger(__name__)

LOCATION_COLUMNS = ["SOURCE_URI", "SOURCE_LINE", "SOURCE_COLUMN"]

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
               "keyLines": {KEY: (line, column)}}}
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
            located[object_id] = {
                "uri": uri, "startLine": line, "startColumn": _column(text, element_start),
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
            return None, None, None
        key = None if pandas.isna(key) else str(key)
        line, column = entry["keyLines"].get(key, (entry["startLine"], entry["startColumn"]))
        return entry["uri"], line, column

    frame = violations.copy()
    positions = [position(object_id, key) for object_id, key in zip(frame["ID"], frame["KEY"])]
    if positions:
        stamped = pandas.DataFrame(positions, columns=LOCATION_COLUMNS,
                                   index=frame.index).astype(object)
        frame[LOCATION_COLUMNS] = stamped.where(stamped.notna(), None)
    else:
        frame[LOCATION_COLUMNS] = None
    return frame


def _column(text, position):
    """1-based byte column of *position* on its line."""
    return position - text.rfind(b"\n", 0, position)


def _key_lines(text, definition_start, window_end, definition_line, keys):
    """(line, column) of each violated property element inside the object's window."""
    lines = {}
    for key in keys:
        if not key or key == "Type":         # the type is the definition element itself
            continue
        pattern = rb"<[\w.-]+:" + re.escape(str(key).encode()) + rb"[\s>/]"
        match = re.search(pattern, text[definition_start:window_end])
        if match is not None:
            start = definition_start + match.start()
            lines[key] = (definition_line + text.count(b"\n", definition_start, start),
                          _column(text, start))
    return lines


def _read(source):
    """(uri, bytes) for a path or a file-like (zip members from find_all_xml)."""
    if hasattr(source, "read"):
        return str(getattr(source, "name", "<memory>")), source.read()
    with open(source, "rb") as file:
        return str(source), file.read()
