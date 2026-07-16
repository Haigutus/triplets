"""Exact source regions for violations — one grep-style pass over the CIM/XML.

RDF objects carry no text coordinates through the triplets frame, so regions
are recovered from the original files at *export* time: each source file is
read once and every ``rdf:ID`` / ``rdf:about`` definition is indexed with its
line number — one sequential pass per file, cost independent of how many IDs
are wanted. For the sample violations that end up in the report, the violated
property's own line is then searched inside that object's text window, so the
annotation lands on the offending property element, not just the object.

The parse/validate hot paths are untouched: nothing here runs (or imports)
unless ``sources=`` is passed to the SARIF export. When the same object is
defined in several files (``rdf:about`` continuation across profiles), the
first definition wins — a violated KEY living in a later profile file falls
back to the definition line.
"""
import re
import logging

logger = logging.getLogger(__name__)

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
    dict {ID: {"uri": str, "startLine": int, "keyLines": {KEY: int}}}
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
            located[object_id] = {
                "uri": uri, "startLine": line,
                "keyLines": _key_lines(text, match.start(), window_end, line, wanted[object_id]),
            }
    if remaining:
        logger.debug("%d violating object(s) not found in the sources", len(remaining))
    return located


def _key_lines(text, definition_start, window_end, definition_line, keys):
    """Line of each violated property element inside the object's window."""
    lines = {}
    for key in keys:
        if not key or key == "Type":         # the type is the definition element itself
            continue
        pattern = rb"<[\w.-]+:" + re.escape(str(key).encode()) + rb"[\s>/]"
        match = re.search(pattern, text[definition_start:window_end])
        if match is not None:
            lines[key] = definition_line + text.count(
                b"\n", definition_start, definition_start + match.start())
    return lines


def _read(source):
    """(uri, bytes) for a path or a file-like (zip members from find_all_xml)."""
    if hasattr(source, "read"):
        return str(getattr(source, "name", "<memory>")), source.read()
    with open(source, "rb") as file:
        return str(source), file.read()
