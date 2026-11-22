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
onto a violations frame, and it is exposed as
``violations.shacl.locate(sources=...)``. It stamps the source names as
given, a zip member as ``<zip file name>/<member name>``. Both the SARIF and
the sh:ValidationReport exports, given ``sources=``, run it through
``locate_missing``: only the columns the frame lacks are stamped, and the
sh:ValidationReport export, which writes no referenced-object location, asks
for the SOURCE_* columns only. ``relative_sources`` is the one pass both
report exports run for ``source_root=``: every file name they write goes
through ``relative_name``.

The parse/validate hot paths are untouched: nothing here runs unless sources
are handed to an export. A finding whose instance file is known
(INSTANCE_LABEL) is located in that file. Otherwise, when the same object is
defined in several files (``rdf:about`` continuation across profiles), the first
definition is the object's position, and a violated KEY is located at its
element in the file that holds it (an SSH property of an EQ object); a KEY in no
file falls back to the definition position. Positions are whole lines (1-based), deliberately: RDF text
serializations put one statement per line, so the line IS the reference —
columns add per-format anchor rules (byte vs UTF-16 units, element shapes)
and broke SARIF viewers, for no extra information. Line-only is also what
keeps the pass extensible beyond XML: IDs and KEYs appear as literal
substrings in every serialization (N-Quads, Turtle, JSON-LD).
"""
import functools
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


def member_name(container, name):
    """The name of a file read from a zip: ``<zip file name>/<member name>`` —
    a relative name that is not the path of a file on disk. *container* is the
    zip's file name without directories (``.container`` of a zip member, the
    ``belongsToContainer`` row of a parsed file); without one, *name* as it is."""
    return f"{container}/{name}" if container else name


def locate(wanted, sources, files=None):
    """Find the wanted objects in the source XML.

    Parameters
    ----------
    wanted : dict {ID: set of KEYs}
        Objects to locate; the KEY sets name the violated properties whose
        own lines are worth pinpointing (may be empty).
    sources : str/Path to .xml/.rdf/.zip, file-like, or a list of those —
        the same shapes ``parse()``/``read_rdf`` accept.
    files : dict {ID: {source name: set of KEYs}}, optional
        The files whose own position of the ID (and of those KEYs) is wanted
        too — the file of the instance a finding was found in; a name that is
        no source's is ignored.

    Returns
    -------
    dict {ID: {"uri": str, "startLine": int, "snippet": str,
               "keyLines": {KEY: (uri, line, snippet)},
               "files": {uri: {"startLine": int, "snippet": str, "keyLines": {KEY: (uri, line, snippet)}}}}}
        "uri" is the source name as given (the path, a file-like's
        ``.name``); a zip member is ``<zip file name>/<member name>``, the
        zip's name without directories. The position is that of the first
        rdf:ID / rdf:about element with the ID in read order — the plain
        files as given, then the members of the zips: an rdf:about element
        in a file read earlier wins over the rdf:ID element in a later one.
        A KEY's position is that of its element in the first object window
        of the ID that holds it, in whichever file (an SSH property of an
        object an EQ file defines). "files" holds the definition and KEY
        positions within each of the ID's *files* that defines it, when they are
        not the top-level ones: a file it lacks (always the file of the first
        definition) has its positions at the top level. IDs not present in the sources
        are absent from the result; a KEY in none of the ID's windows is absent
        from "keyLines". The sources are read in that order until every wanted
        ID, KEY and file is found; one missing makes the pass read them all.
    """
    from ..parser.utils import find_all_xml

    sources = [(_name(source), source) for source in find_all_xml(sources)]
    names = {name for name, _ in sources}
    ids = {str(object_id).encode(): object_id for object_id in wanted}
    missing = {object_id: _searched(found) for object_id, found in wanted.items()}
    # per (ID, file): the KEYs to search there, and whether a finding there needs the definition
    own_files = {object_id: {name: _own(found) for name, found in in_files.items() if name in names}
                 for object_id, in_files in (files or {}).items()}
    unread = {name for in_files in own_files.values() for name in in_files}       # the instance files to read
    located = {}
    for uri, source in sources:
        if len(located) == len(ids) and not any(missing.values()) and not unread:
            break
        unread.discard(uri)
        text = _read(uri, source)
        matches = list(_DEFINITION.finditer(text))
        line, previous = 1, 0
        for position, match in enumerate(matches):
            line += text.count(b"\n", previous, match.start())
            previous = match.start()
            object_id = ids.get(match.group(1))
            if object_id is None:
                continue
            entry = located.get(object_id)
            if entry is None:
                entry = located[object_id] = {"uri": uri, "startLine": line, "snippet": _line_text(text, match.start()),
                                              "keyLines": {}, "files": {}}
            # in the defining file the top-level positions are the instance file's own
            own, definition = (_NO_FILE if uri == entry["uri"] or object_id not in own_files
                               else own_files[object_id].get(uri, _NO_FILE))
            if own is None and not missing[object_id]:
                continue
            window_end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
            searched = missing[object_id]
            if own is not None:
                searched = searched | own if searched else own
            found = {key: (uri, *found_at)
                     for key, found_at in _key_lines(text, match.start(), window_end, line, searched).items()}
            if missing[object_id]:
                entry["keyLines"] |= {key: found[key] for key in missing[object_id] & found.keys()}
                missing[object_id] -= found.keys()
            if own is None:
                continue
            in_file = entry["files"].get(uri)
            if in_file is None:
                if not definition and all(entry["keyLines"].get(key, ("",))[0] == uri for key in own):
                    continue                                   # every KEY's top-level position is in this file
                entry["files"][uri] = {"startLine": line, "snippet": _line_text(text, match.start()),
                                       "keyLines": found}
            else:
                in_file["keyLines"] = found | in_file["keyLines"]          # the first window holding a KEY wins
    if len(located) < len(ids):
        logger.debug("%d violating object(s) not found in the sources", len(ids) - len(located))
    return located


_NO_FILE = (None, False)        # no instance file position wanted: (KEYs, definition needed)


def _searched(keys):
    """The KEYs worth a search: not None, not the type (the definition itself)."""
    return {key for key in keys if key and key != TYPE_KEY}


def _own(keys):
    """(the KEYs to search in an instance file, whether a finding there is located at
    the definition — a KEY not searched)."""
    searched = _searched(keys)
    return searched, bool(set(keys) - searched)


# findings of a property the object lacks: their KEY is searched in no file
ABSENT_KEY_TYPES = frozenset({"sh:minCount", "sh:qualifiedMinCount", "xsd:minOccurs"})


def locate_violations(violations, sources, targets=True):
    """Stamp LOCATION_COLUMNS onto a violations frame — one locate() pass.

    Per row (ID, KEY): a row whose INSTANCE_LABEL names one of the sources
    (``enrich`` stamps the instance's file name, a zip member as
    ``<zip file name>/<member name>``) is located in that file first — at the
    violated property element when the object's text window there holds it,
    else at the object's definition there, unless the KEY is found in another
    file (a property of an object another file defines). Every other row is
    located at the violated property element's own position when it was found
    inside one of the object's text windows (in the file that holds it, which
    need not be the defining one), else at the object definition's; rows whose
    object is not in the sources get nulls. A finding of an absent
    property (``ABSENT_KEY_TYPES``: ``sh:minCount``, ``xsd:minOccurs``) does not
    search its KEY, so a KEY in no file does not make the pass read every source;
    it is located at the definition. The referenced object
    of a reference finding found in the data (TARGET_ID — context.enrich) is
    located in the same pass, at its definition, into the TARGET_* columns; a
    frame without TARGET_ID gets nulls there. A definition is the first
    rdf:ID / rdf:about element with the id, in read order (see locate).
    SOURCE_URI / TARGET_URI are the source names as given, a zip member
    ``<zip file name>/<member name>`` (see locate). ``targets=False`` does
    not search the referenced objects: the TARGET_* columns get nulls.
    """
    import pandas

    def column(name, used=True):
        if used and name in violations.columns:
            return violations[name]
        return pandas.Series(None, index=violations.index, dtype=object)

    def text(values):
        """The cells as str, None for a missing one."""
        return [value if isinstance(value, str) else None if pandas.isna(value) else str(value) for value in values]

    ids, keys, labels = text(violations["ID"]), text(violations["KEY"]), text(column("INSTANCE_LABEL"))
    targets = text(column("TARGET_ID", targets))
    wanted, files = {}, {}
    for object_id, key, violation_type, label in zip(ids, keys, column("VIOLATION_TYPE"), labels):
        if object_id is not None:
            searched = None if violation_type in ABSENT_KEY_TYPES else key
            wanted.setdefault(object_id, set()).add(searched)
            if label is not None:
                files.setdefault(object_id, {}).setdefault(label, set()).add(searched)
    for target in targets:
        if target is not None:
            wanted.setdefault(target, set())
    located = locate(wanted, sources, files)

    def position(object_id, key=None, label=None):
        entry = located.get(object_id)
        if entry is None:
            return None, None, None
        own = entry["files"].get(label)
        if own is not None and key in own["keyLines"]:
            return own["keyLines"][key]
        if key in entry["keyLines"]:
            return entry["keyLines"][key]
        if own is not None:
            return label, own["startLine"], own["snippet"]
        return entry["uri"], entry["startLine"], entry["snippet"]

    frame = violations.copy()
    positions = [position(object_id, key, label) + position(target)
                 for object_id, key, label, target in zip(ids, keys, labels, targets)]
    # object columns: a line stays an int next to the None of a row not located
    frame[LOCATION_COLUMNS] = pandas.DataFrame(positions, columns=LOCATION_COLUMNS, index=frame.index, dtype=object)
    return frame


def locate_missing(violations, sources, targets=True):
    """locate_violations for a report export: stamp only what the frame lacks.

    The SOURCE_* columns are located unless the frame carries all three;
    all three on the frame are kept as they are, hand-stamped ones too. The
    TARGET_* columns are located when the frame has referenced objects
    (TARGET_ID) and none of them located (no TARGET_URI value) — it was
    located before context.enrich filled TARGET_ID, or never, or its
    referenced objects are in none of the sources read before; else the
    TARGET_* columns on the frame are kept. ``targets=False`` — an export
    that writes no referenced-object location — leaves that half out: the
    sources are read for the SOURCE_* columns only, and the referenced objects
    are not searched in them. With neither half to
    locate, the sources are not read and the frame is returned as it is.

    Both tests are on the frame, not per row: a frame located in part (rows
    of a located and of a not located frame concatenated) counts as located,
    and its rows without a position stay without one.
    """
    present = set(violations.columns)
    source = not set(LOCATION_COLUMNS[:3]) <= present
    target = (targets and "TARGET_ID" in present and violations["TARGET_ID"].notna().any()
              and not ("TARGET_URI" in present and violations["TARGET_URI"].notna().any()))
    if not (source or target):
        return violations
    frame = locate_violations(violations, sources, targets=targets)
    kept = [column for column, lacking in zip(LOCATION_COLUMNS, [source] * 3 + [target] * 3)
            if not lacking and column in present]
    frame[kept] = violations[kept]
    return frame


def _key_lines(text, definition_start, window_end, definition_line, keys):
    """(line, snippet) of each violated property element inside the object's window."""
    lines = {}
    for key in keys:
        match = _key_element(key).search(text, definition_start, window_end)
        if match is not None:
            lines[key] = (definition_line + text.count(b"\n", definition_start, match.start()),
                          _line_text(text, match.start()))
    return lines


@functools.cache
def _key_element(key):
    """The pattern of a KEY's property element, ``<prefix:KEY`` — compiled once per KEY."""
    return re.compile(rb"<[\w.-]+:" + re.escape(str(key).encode()) + rb"[\s>/]")


def _line_text(text, position):
    """The stripped text of *position*'s line — the debugging snippet."""
    line_start = text.rfind(b"\n", 0, position) + 1
    line_end = text.find(b"\n", position)
    line_end = len(text) if line_end == -1 else line_end
    return text[line_start:line_end].decode("utf-8", "replace").strip()


def relative_name(name, root):
    """*name* relative to *root* with forward slashes when *name* is an
    absolute path of a file in *root* or a directory below it; else None.
    *root* is an absolute path with its symlinks resolved (os.path.realpath),
    and the symlinks of *name* are resolved the same way, so a path through a
    symlink and the real path compare equal. A relative name — a zip member,
    a bare file name — is never read against the working directory."""
    if not os.path.isabs(name):
        return None
    path = pathlib.PurePath(os.path.realpath(name))
    return path.relative_to(root).as_posix() if path.parent.is_relative_to(root) else None


def check_source_root(source_root):
    """ValueError for a *source_root* that is not a directory (None passes) — what an
    export checks first, before it reads any data or source."""
    if source_root is not None and not os.path.isdir(source_root):
        raise ValueError(f"source_root is not a directory: {source_root}")


def relative_sources(violations, names, source_root):
    """The file names a report export writes, relative to *source_root*.

    One rule for every name (relative_name): an absolute path under the root
    is written relative to it; any other name is written as given. Symlinks
    are resolved in the root and in the names before they compare. Applied to
    the SOURCE_URI, TARGET_URI and INSTANCE_LABEL columns of *violations*
    (where present) and to each list of *names* ({key: [file name, ...]} — the
    run metadata's source / references). Absolute names outside the root are
    named in one warning. Returns (frame, names); the inputs are not changed, and
    ``source_root=None`` returns them as they are. A *source_root* that is not
    a directory raises ValueError.
    """
    check_source_root(source_root)
    if source_root is None:
        return violations, names
    root = os.path.realpath(source_root)
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


def _name(source):
    """The name a source is located under: a path as given, a file-like's ``.name``
    (``<memory>`` without one), a zip member ``<zip file name>/<member name>`` (its
    ``.container``, see member_name)."""
    if hasattr(source, "read"):
        return member_name(getattr(source, "container", None), str(getattr(source, "name", "<memory>")))
    return str(source)


def _read(uri, source):
    """The bytes of a path or a file-like (zip members from find_all_xml) named *uri*.
    A seekable file-like is read from its start, so one open handle serves every pass
    it is given to; a file-like that gives no bytes (a stream that cannot be rewound,
    read before) is named in a warning."""
    if hasattr(source, "read"):
        if getattr(source, "seekable", lambda: False)():
            source.seek(0)
        text = source.read()
        if not text:
            logger.warning("Nothing to read from source %s: no object is located in it", uri)
        return text
    with open(source, "rb") as file:
        return file.read()
