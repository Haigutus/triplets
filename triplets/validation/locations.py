"""Exact source locations for violations — one grep-style pass over the CIM/XML.

RDF objects carry no text coordinates through the triplets frame, so locations
are recovered from the original files at *export* time: each source file is
read once and every ``rdf:ID`` / ``rdf:about`` definition is indexed with its
line number — one sequential pass per file, cost independent of how many IDs
are wanted. For each violation, the violated property's own position is then
searched inside that object's text window, so the annotation lands on the
offending property element, not just the object.

``locate_violations`` is the public pass: it stamps ``LOCATION_COLUMNS``
(SOURCE_URI, SOURCE_LINE, SOURCE_SNIPPET — the located line's text — and the
same three TARGET_* columns for the referenced object of a reference finding)
onto a violations frame — both the SARIF and the sh:ValidationReport exports call
it when given ``sources=``, and it is exposed as
``violations.shacl.locate(sources=...)``. It stamps the source names as
given. ``relative_sources`` is the one pass both report exports run for
``source_root=``: every file name they write goes through ``relative_name``.

The parse/validate hot paths are untouched: nothing here runs unless sources
are handed to an export. When the same object is defined in several files
(``rdf:about`` continuation across profiles), the first definition wins — a
violated KEY living in a later profile file falls back to the definition
position. Positions are whole lines (1-based), deliberately: RDF text
serializations put one statement per line, so the line IS the reference —
columns add per-format anchor rules (byte vs UTF-16 units, element shapes)
and broke SARIF viewers, for no extra information. Line-only is also what
keeps the pass extensible beyond XML: IDs and KEYs appear as literal
substrings in every serialization (N-Quads, Turtle, JSON-LD).
"""
import os
import re
import logging
import pathlib

from ..iri import ID_PREFIXES, TYPE_KEY

logger = logging.getLogger(__name__)

LOCATION_COLUMNS = ["SOURCE_URI", "SOURCE_LINE", "SOURCE_SNIPPET",   # the finding: ID (+ KEY)
                    "TARGET_URI", "TARGET_LINE", "TARGET_SNIPPET"]   # the referenced object: TARGET_ID

# an object definition: <cim:Breaker rdf:ID="_uuid"> / rdf:about="#_uuid" /
# rdf:about="urn:uuid:uuid" — group(1) is the bare ID (one prefix stripped,
# the triplets.iri.local_id rule)
_DEFINITION = re.compile(rb'rdf:(?:ID|about)="(?:' + "|".join(map(re.escape, ID_PREFIXES)).encode()
                         + rb')?([^"]+)"')


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
    dict {ID: {"uri": str, "startLine": int, "snippet": str,
               "keyLines": {KEY: (line, snippet)}}}
        "uri" is the source name as given (the path, the zip member name, a
        file-like's ``.name``). IDs not present in the sources are absent
        from the result.
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
                "uri": uri, "startLine": line, "snippet": _line_text(text, match.start()),
                "keyLines": _key_lines(text, match.start(), window_end, line, wanted[object_id]),
            }
    if remaining:
        logger.debug("%d violating object(s) not found in the sources", len(remaining))
    return located


def locate_violations(violations, sources):
    """Stamp LOCATION_COLUMNS onto a violations frame — one locate() pass.

    Per row (ID, KEY): the violated property element's own position when it
    was found inside the object's text window, else the object definition's;
    rows whose object is not in the sources get nulls. The referenced object
    of a reference finding found in the data (TARGET_ID — context.enrich) is
    located in the same pass, at its definition, into the TARGET_* columns; a
    frame without TARGET_ID gets nulls there. SOURCE_URI / TARGET_URI are the
    source names as given.
    """
    import pandas

    targets = (violations["TARGET_ID"] if "TARGET_ID" in violations.columns
               else pandas.Series(None, index=violations.index, dtype=object))
    wanted = {}
    for object_id, key in zip(violations["ID"], violations["KEY"]):
        if not pandas.isna(object_id):
            wanted.setdefault(str(object_id), set()).add(None if pandas.isna(key) else str(key))
    for target in targets.dropna():
        wanted.setdefault(str(target), set())
    located = locate(wanted, sources)

    def position(object_id, key=None):
        entry = located.get(str(object_id)) if not pandas.isna(object_id) else None
        if entry is None:
            return None, None, None
        key = None if pandas.isna(key) else str(key)
        line, snippet = entry["keyLines"].get(key, (entry["startLine"], entry["snippet"]))
        return entry["uri"], line, snippet

    frame = violations.copy()
    positions = [position(object_id, key) + position(target)
                 for object_id, key, target in zip(frame["ID"], frame["KEY"], targets)]
    if positions:
        stamped = pandas.DataFrame(positions, columns=LOCATION_COLUMNS,
                                   index=frame.index).astype(object)
        frame[LOCATION_COLUMNS] = stamped.where(stamped.notna(), None)
    else:
        frame[LOCATION_COLUMNS] = None
    return frame


def _key_lines(text, definition_start, window_end, definition_line, keys):
    """(line, snippet) of each violated property element inside the object's window."""
    lines = {}
    for key in keys:
        if not key or key == TYPE_KEY:         # the type is the definition element itself
            continue
        pattern = rb"<[\w.-]+:" + re.escape(str(key).encode()) + rb"[\s>/]"
        match = re.search(pattern, text[definition_start:window_end])
        if match is not None:
            start = definition_start + match.start()
            lines[key] = (definition_line + text.count(b"\n", definition_start, start),
                          _line_text(text, start))
    return lines


def _line_text(text, position):
    """The stripped text of *position*'s line — the debugging snippet."""
    line_start = text.rfind(b"\n", 0, position) + 1
    line_end = text.find(b"\n", position)
    line_end = len(text) if line_end == -1 else line_end
    return text[line_start:line_end].decode("utf-8", "replace").strip()


def relative_name(name, root):
    """*name* relative to *root* with forward slashes when *name* is an
    absolute path of a file in *root* or a directory below it; else None.
    *root* is an absolute path. A relative name — a zip member, a bare file
    name — is never read against the working directory. Lexical: symlinks
    are not resolved."""
    if not os.path.isabs(name):
        return None
    path = pathlib.PurePath(os.path.normpath(name))
    return path.relative_to(root).as_posix() if path.parent.is_relative_to(root) else None


def relative_sources(violations, names, source_root):
    """The file names a report export writes, relative to *source_root*.

    One rule for every name (relative_name): an absolute path under the root
    is written relative to it; any other name is written as given. Applied to
    the SOURCE_URI, TARGET_URI and INSTANCE_LABEL columns of *violations*
    (where present) and to each list of *names* ({key: [file name, ...]} — the
    run metadata's source / references). Absolute names outside the root are
    named in one warning. Returns (frame, names); the inputs are not changed, and
    ``source_root=None`` returns them as they are.
    """
    if source_root is None:
        return violations, names
    root = os.path.abspath(source_root)
    known = {}                              # name → relative name or None: each name is checked once

    def relative(name):
        if name not in known:
            known[name] = relative_name(str(name), root)
        return known[name] or name

    columns = [column for column in ("SOURCE_URI", "TARGET_URI", "INSTANCE_LABEL")
               if column in violations.columns]
    frame = violations.assign(**{column: violations[column].map(relative, na_action="ignore")
                                 for column in columns})
    names = {key: [relative(name) for name in values] for key, values in names.items()}
    outside = [str(name) for name, inside in known.items() if inside is None and os.path.isabs(str(name))]
    if outside:
        logger.warning("%d file name(s) outside source_root %s keep their names: %s",
                       len(outside), root, ", ".join(outside))
    return frame, names


def _read(source):
    """(uri, bytes) for a path or a file-like (zip members from find_all_xml)."""
    if hasattr(source, "read"):
        return str(getattr(source, "name", "<memory>")), source.read()
    with open(source, "rb") as file:
        return str(source), file.read()
