"""Map a pyshacl/SHACL ValidationReport graph to the canonical violations DataFrame,
and the inverse: export a violations DataFrame as a standard sh:ValidationReport.

Canonical violations schema (identical across all current and future SHACL
engines, so the later vectorized engines can produce it natively)::

    [ID, KEY, VALUE, VIOLATION_TYPE, MESSAGE, SEVERITY, SOURCE_SHAPE]
"""
import io
import logging
import re
import os
from datetime import datetime, timezone

import numpy
import pandas

from .locations import check_source_root, locate_missing, relative_sources
from ..iri import (DCTERMS_NS, PROV_NS, RDFS_NS, RDF_TYPE, SCHEMA_ORG_NS, SH_NS, TRIPLETS_NS, TYPE_KEY,
                   XSD_NS, CIM_NS, absolute_id, absolute_key, absolute_name, absolute_value, decode_iri, is_iri,
                   load_rdf_map, local_id, local_key, local_value, namespaces, split_iri, value_types)

logger = logging.getLogger(__name__)

VIOLATION_COLUMNS = ["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"]
# SOURCE_SHAPE of a schema finding: ``urn:triplets:schema[:<profile>…]#<Class>[.<KEY>]`` —
# ``:`` and ``#`` separate, so a profile name is percent-encoded (schema_ir._shape_prefix)
SCHEMA_SHAPES = "urn:triplets:schema"
SCHEMA_SHAPE_STARTS = (SCHEMA_SHAPES + ":", SCHEMA_SHAPES + "#")   # with a scope, without one


VALIDATION_FAILURE = "triplets:validationFailure"   # the run stopped: the pyshacl engine's ValidationFailure
# VIOLATION_TYPEs of rows about a shape the run could not evaluate, never about the data
SHAPE_NOTES = ("triplets:invalidSparql", VALIDATION_FAILURE)


def run_language(violations):
    """The constraint language of a violations frame (``"shacl"`` / ``"rdfs"``): the run
    metadata's (``attrs["validation"]["language"]``); for a frame without it (``pandas.concat``
    drops the attrs) ``"rdfs"`` when every SOURCE_SHAPE is a schema shape, else ``"shacl"``."""
    language = violations.attrs.get("validation", {}).get("language")
    if language:
        return language
    if "SOURCE_SHAPE" not in violations.columns:     # a hand-made frame: nothing says rdfs
        return "shacl"
    shapes = violations["SOURCE_SHAPE"].dropna().astype(str)
    return "rdfs" if len(shapes) and shapes.str.startswith(SCHEMA_SHAPE_STARTS).all() else "shacl"


def has_violation(violations):
    """Whether *violations* holds a data Violation — the ``abort_on_first`` stop.
    A ``triplets:invalidSparql`` or ``triplets:validationFailure`` row flags a
    defective shape, not the data."""
    return bool(len(violations)) and bool(((violations["SEVERITY"] == "Violation")
                                           & ~violations["VIOLATION_TYPE"].isin(SHAPE_NOTES)).any())


# path suffix → rdflib serialize format (traversed in order for the reverse,
# so .xml — the suffix the docs push for RDF/XML — is the default before .rdf)
_EXT = {".ttl": "turtle", ".xml": "xml", ".rdf": "xml",
        ".nt": "nt", ".n3": "n3", ".jsonld": "json-ld", ".json-ld": "json-ld"}

# sh:sourceConstraintComponent URI suffix → short violation type
_COMPONENT_MAP = {
    "MinCountConstraintComponent": "sh:minCount",
    "MaxCountConstraintComponent": "sh:maxCount",
    "DatatypeConstraintComponent": "sh:datatype",
    "MinLengthConstraintComponent": "sh:minLength",
    "MaxLengthConstraintComponent": "sh:maxLength",
    "PatternConstraintComponent": "sh:pattern",
    "MinInclusiveConstraintComponent": "sh:minInclusive",
    "MaxInclusiveConstraintComponent": "sh:maxInclusive",
    "MinExclusiveConstraintComponent": "sh:minExclusive",
    "MaxExclusiveConstraintComponent": "sh:maxExclusive",
    "LessThanOrEqualsConstraintComponent": "sh:lessThanOrEquals",
    "ClassConstraintComponent": "sh:class",
    "NodeConstraintComponent": "sh:node",
    "NodeKindConstraintComponent": "sh:nodeKind",
    "InConstraintComponent": "sh:in",
    "HasValueConstraintComponent": "sh:hasValue",
    "EqualsConstraintComponent": "sh:equals",
    "DisjointConstraintComponent": "sh:disjoint",
    "LessThanConstraintComponent": "sh:lessThan",
    "ClosedConstraintComponent": "sh:closed",
    "OrConstraintComponent": "sh:or",
    "AndConstraintComponent": "sh:and",
    "XoneConstraintComponent": "sh:xone",
    "NotConstraintComponent": "sh:not",
    "SPARQLConstraintComponent": "sh:sparql",
    "QualifiedMinCountConstraintComponent": "sh:qualifiedMinCount",
    "QualifiedMaxCountConstraintComponent": "sh:qualifiedMaxCount",
    "QualifiedValueShapeConstraintComponent": "sh:qualifiedValueShape",
    "UniqueLangConstraintComponent": "sh:uniqueLang",
    "LanguageInConstraintComponent": "sh:languageIn",
}


# a blank-node label in N-Triples text (rdflib's serializer: at the start of a line or after a space)
_BLANK_NODE = re.compile(r"(?:^|\s)_:([A-Za-z0-9]+)", re.MULTILINE)


def shape_triples(text, graph=None):
    """A shape definition of the run metadata (``source_shapes``: N-Triples text) →
    its triples in *graph* (a new one by default), the blank-node labels kept:
    they are the ones SOURCE_SHAPE names."""
    import rdflib

    graph = rdflib.Graph() if graph is None else graph
    # bnode_context (rdflib's N-Triples parser): label → the BNode it is read as; a label
    # that is no node (inside a literal) is an entry nothing asks for
    labels = {label: rdflib.BNode(label) for label in _BLANK_NODE.findall(text)}
    return graph.parse(data=text, format="nt", bnode_context=labels)


def report_to_violations(report_graph, shapes_graph=None, rdf_map=None, undefined_namespace=CIM_NS):
    """ValidationReport rdflib graph → violations DataFrame (single columnar pass).

    Nested ``sh:detail`` results (the cause of a ``sh:node`` result) are not
    listed: the parent result is the finding. With the ``shapes_graph`` the
    report was made from, a blank-node ``sh:resultPath`` (inverse / sequence /
    alternative path) is the KEY it resolves to; without it (reports written by
    other tools) KEY is the blank-node label.

    An IRI ``sh:value`` becomes the VALUE the data holds. With the *rdf_map* (and
    *undefined_namespace*) the report was written with, exactly: the local name when
    the IRI is the one that name stands for (a class under ``rdf:type``, an
    enumeration value, a schema name), else :func:`~triplets.iri.local_id` — a
    reference or a class in another namespace stays whole, with a ``#`` too.
    Without *rdf_map* the form is guessed (:func:`~triplets.iri.local_value`): an
    ``http`` IRI with a ``#`` is taken as a name and loses its namespace."""
    import rdflib

    from .shacl_ir import _resolve_path

    sh = rdflib.Namespace(SH_NS)
    names = (namespaces(rdf_map), value_types(rdf_map), undefined_namespace) if rdf_map else None
    details = set(report_graph.objects(None, sh.detail))
    # collect into per-column lists, build the DataFrame once (no per-row concat)
    columns = {name: [] for name in VIOLATION_COLUMNS}

    for result in report_graph.subjects(rdflib.RDF.type, sh.ValidationResult):
        if result in details:
            continue
        path = report_graph.value(result, sh.resultPath)
        key = local_key(decode_iri(path))
        if shapes_graph is not None and path is not None and not isinstance(path, rdflib.URIRef):
            key = _resolve_path(shapes_graph, sh, path)[0] or key
        focus = report_graph.value(result, sh.focusNode)
        value = report_graph.value(result, sh.value)
        if path is None and value == focus:     # pyshacl echoes the focus node on path-less results; ID holds it
            value = None
        component = report_graph.value(result, sh.sourceConstraintComponent)
        severity = report_graph.value(result, sh.resultSeverity)
        shape = report_graph.value(result, sh.sourceShape)
        message = _constraint_message(report_graph.objects(result, sh.resultMessage))

        columns["ID"].append(local_id(decode_iri(focus)))
        columns["KEY"].append(key)
        columns["VALUE"].append(_term_value(value, TYPE_KEY if path == rdflib.RDF.type else key, names))
        columns["VIOLATION_TYPE"].append(_component(component))
        columns["MESSAGE"].append(message)
        columns["SEVERITY"].append(split_iri(severity)[1] if severity is not None else "Violation")
        columns["SOURCE_SHAPE"].append(str(shape) if shape is not None else None)

    return pandas.DataFrame(columns, columns=VIOLATION_COLUMNS)


def _constraint_message(messages):
    """The constraint's own message among a result's prefixed set — the
    [shacl_message]/[engine_message] entry with the prefix stripped (inverse of _messages);
    for reports written by other tools, every message sorted and joined by a newline."""
    texts = sorted(str(message) for message in messages)
    for text in texts:
        for prefix in ("[shacl_message] ", "[rdfs_message] ", "[engine_message] "):
            if text.startswith(prefix):
                return text[len(prefix):]
    return "\n".join(texts) if texts else None


def _term_value(term, key=None, names=None):
    """sh:value → triplet VALUE: literals verbatim, IRIs decoded. Without *names*
    ``local_value`` guesses (a class on a type path). With the schema's
    *names* (namespaces, value types, undefined namespace) the inverse of the
    writer: the local name when the IRI is the one the name stands for under
    *key* (a class, an enumeration value) or a declared schema name (the class a
    ``( association rdf:type )`` path reports), else ``local_id``."""
    if term is None:
        return None
    if type(term).__name__ == "Literal":
        return str(term)
    iri = decode_iri(term)
    if names is None:
        return local_value(iri, "class" if key == TYPE_KEY else None)
    schema_namespaces, schema_value_types, undefined_namespace = names
    name = split_iri(iri)[1]
    named = key == TYPE_KEY or schema_value_types.get(key) == "enum" or name in schema_namespaces
    return name if named and absolute_name(name, schema_namespaces, undefined_namespace) == str(term) else local_id(iri)


def _component(term):
    if term is None:
        return "sh:unknown"
    value = str(term)
    suffix = split_iri(value)[1]
    if value.startswith(TRIPLETS_NS):
        return f"triplets:{suffix}"
    if value.startswith(RDFS_NS):
        return f"rdfs:{suffix}"
    if value.startswith(XSD_NS):
        return f"xsd:{suffix}"
    if value.startswith(SCHEMA_ORG_NS):    # schema.org has no '#' — suffix is the path tail
        return f"schema:{value[len(SCHEMA_ORG_NS):]}"
    return _COMPONENT_MAP.get(suffix, f"sh:{suffix}")


# short violation type → sh:sourceConstraintComponent URI (inverse of _COMPONENT_MAP)
_COMPONENT_URI = {short: f"{SH_NS}{suffix}" for suffix, short in _COMPONENT_MAP.items()}



def violations_to_report_graph(violations, report_source=None, report_references=None,
                               rdf_map=None, undefined_namespace=CIM_NS, source_root=None):
    r"""Violations DataFrame → sh:ValidationReport rdflib graph (inverse of
    report_to_violations; KEYs expand through the export schema namespaces —
    ``rdf_map`` — ``undefined_namespace`` for names it lacks, unless already URIs).

    ``sh:value`` has the term kind the schema entry type of the KEY gives it
    (:func:`~triplets.iri.absolute_value`): the IRI of the referenced object for
    an Association, the enumeration value IRI for an Enumeration, the class IRI
    for ``Type`` (the name's schema namespace; ``undefined_namespace`` — CIM100 by
    default — for a class the schema lacks and for every class without ``rdf_map``,
    whatever the data's CIM version), a plain literal with the VALUE text
    for an Attribute and for a KEY the schema does not define — the same terms
    ``export_to_nquads`` writes for the row: a reference VALUE is
    :func:`~triplets.iri.absolute_id` of its text (``urn:uuid:…``, an absolute
    IRI whole), whatever the text reads like. The VALUE of a finding whose shape
    has a ``( association rdf:type )`` path (the shape's definition in the run
    metadata) is the object's class, written as the class IRI. A KEY may be its
    IRI: the entry of its local name decides.

    A result carries several plain-text ``sh:resultMessage``\ s when the frame
    has the context/location columns: the engine message, the shape and
    schema descriptions (context.enrich) and the source position
    (locations.locate_violations) — SHACL has no location vocabulary, so a
    message is the interoperable carrier.

    Report-level metadata (always): ``prov:generatedAtTime``,
    ``dcterms:creator`` (tool + version). Optional: ``dcterms:source`` /
    ``dcterms:references`` from ``report_source`` / ``report_references``
    (str or sequence — validated file name(s) / shape file name(s)).

    Defaults come from ``violations.attrs["validation"]`` — the metadata
    the validation functions stamp on the frame (timestamp of the validation run, tool
    version, data/shape file names). Explicit arguments override it. With
    ``source_root``, a file name that is an absolute path under it is written
    relative to it — SOURCE_URI, ``dcterms:source``, ``dcterms:references``
    (locations.relative_sources).
    """
    import rdflib
    import triplets
    from rdflib.collection import Collection
    from .shacl_ir import _term as _shape_term

    meta = violations.attrs.get("validation", {})
    if report_source is None:
        report_source = meta.get("source")
    if report_references is None:
        report_references = meta.get("references")
    generated_at = (meta.get("generated_at")
                    or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    creator = meta.get("creator") or f"triplets {triplets.__version__}"
    if meta.get("engine"):
        creator = f"{creator} (engine: {meta['engine']})"

    schema_namespaces = namespaces(rdf_map)
    schema_value_types = value_types(rdf_map)
    expand = lambda value: _expand(value, schema_namespaces, undefined_namespace)  # noqa: E731

    via_type = {}   # SOURCE_SHAPE → its sh:path is a sequence ending in rdf:type (the embedded definitions)

    def reports_type(shape):
        if shape not in via_type:
            path = None if pandas.isna(shape) else graph.value(_shape_term(str(shape)), sh.path)
            members = list(Collection(graph, path)) if path is not None and (path, rdflib.RDF.first, None) in graph else []
            via_type[shape] = members[-1:] == [rdflib.RDF.type]
        return via_type[shape]

    def value_term(key, value, shape):
        key = local_key(key)                          # a KEY given as an IRI has the entry of its name
        if key != TYPE_KEY and schema_value_types.get(key) not in ("reference", "enum"):
            return rdflib.Literal(value)
        if reports_type(shape):                       # ( association rdf:type ): the VALUE is the object's class
            key = TYPE_KEY
        return rdflib.URIRef(absolute_value(key, str(value), schema_namespaces, schema_value_types,
                                            undefined_namespace=undefined_namespace)[1])
    sh = rdflib.Namespace(SH_NS)
    prov = rdflib.Namespace(PROV_NS)
    dcterms = rdflib.Namespace(DCTERMS_NS)
    graph = rdflib.Graph()
    graph.bind("sh", sh)
    graph.bind("prov", prov)
    graph.bind("dcterms", dcterms)

    # embed the violated shapes' definitions (CBD, stamped by validate_shacl())
    # so sh:sourceShape is never an empty blank node — the constraint
    # parameters (sh:in list, sh:minCount, ...) are IN the report
    for text in meta.get("source_shapes", {}).values():
        shape_triples(text, graph)

    language = run_language(violations)

    report = rdflib.BNode()
    graph.add((report, rdflib.RDF.type, sh.ValidationReport))
    graph.add((report, sh.conforms, rdflib.Literal(violations.empty)))
    graph.add((report, prov.generatedAtTime,
               rdflib.Literal(generated_at, datatype=rdflib.URIRef(f"{XSD_NS}dateTime"))))
    graph.add((report, dcterms.creator, rdflib.Literal(creator)))
    violations, names = relative_sources(violations, {"source": _as_list(report_source),
                                                      "references": _as_list(report_references)}, source_root)
    for value in names["source"]:
        graph.add((report, dcterms.source, rdflib.Literal(value)))
    for value in names["references"]:
        graph.add((report, dcterms.references, rdflib.Literal(value)))

    for row in violations.itertuples(index=False):
        result = rdflib.BNode()
        graph.add((report, sh.result, result))
        graph.add((result, rdflib.RDF.type, sh.ValidationResult))
        graph.add((result, sh.resultSeverity, sh[row.SEVERITY if pandas.notna(row.SEVERITY) else "Violation"]))
        if pandas.notna(row.ID):
            graph.add((result, sh.focusNode, rdflib.URIRef(absolute_id(str(row.ID)))))
        if pandas.notna(row.KEY):
            graph.add((result, sh.resultPath, rdflib.URIRef(expand(row.KEY))))
        if pandas.notna(row.VALUE):
            graph.add((result, sh.value, value_term(row.KEY, row.VALUE, row.SOURCE_SHAPE)))
        if pandas.notna(row.VIOLATION_TYPE):
            graph.add((result, sh.sourceConstraintComponent, rdflib.URIRef(expand(row.VIOLATION_TYPE))))
        for message in _messages(row, language):
            graph.add((result, sh.resultMessage, rdflib.Literal(message)))
        if pandas.notna(row.SOURCE_SHAPE):
            shape = str(row.SOURCE_SHAPE)   # anonymous property shapes stay blank nodes
            graph.add((result, sh.sourceShape,
                       rdflib.URIRef(shape) if is_iri(shape) else rdflib.BNode(shape)))

    return graph


def _as_list(value):
    """None / single name / sequence of names → tuple of str (paths/bytes coerced)."""
    if value is None:
        return ()
    single = isinstance(value, (str, bytes)) or hasattr(value, "__fspath__")
    values = (value,) if single else tuple(value)
    return tuple(v.decode() if isinstance(v, bytes) else os.fspath(v) for v in values)


def _messages(row, language="shacl"):
    """The result's message set — one entry per fact, each prefixed with
    what it is: the constraint message verbatim ([shacl_message] when authored,
    [engine_message] when the engine worded it — exactly one of the two), what the
    constraint requires ([shacl_expected]), the referenced object's state
    ([context_message]), the validated object ([context_object] — context.enrich), the shape
    and schema descriptions ([shacl_description]/[schema_property]), the source position
    and line text ([context_location]/[context_snippet] — locations.locate_violations)."""
    def cell(name):
        value = getattr(row, name, None)
        return None if value is None or pandas.isna(value) else value

    messages = []
    if cell("MESSAGE") is not None:
        messages.append(f"{message_prefix(row.VIOLATION_TYPE, cell('MESSAGE_SOURCE'), language)} "
                        f"{row.MESSAGE}")
    if profile_text(getattr(row, "PROFILE", None)) is not None:
        messages.append(f"[{language}_profile] {profile_text(row.PROFILE)}")
    if cell("VALUE") is not None:
        messages.append(f"[context_value] {row.VALUE}")
    if cell("EXPECTED") is not None:
        messages.append(f"[{language}_expected] {row.EXPECTED}")
    if cell("TARGET") is not None:
        messages.append(f"[context_message] {row.TARGET}")
    if cell("OBJECT_TYPE") is not None or cell("OBJECT_NAME") is not None:
        label = " ".join(str(part) for part in (cell("OBJECT_TYPE"), cell("OBJECT_NAME"))
                         if part is not None)
        messages.append(f"[context_object] {label}")
    if cell("SHAPE_DESCRIPTION") is not None:
        messages.append(f"[{language}_description] {row.SHAPE_DESCRIPTION}")
    if cell("SCHEMA_DESCRIPTION") is not None:
        multiplicity = f" [{row.SCHEMA_MULTIPLICITY}]" if cell("SCHEMA_MULTIPLICITY") is not None else ""
        messages.append(f"[schema_property] {row.SCHEMA_DESCRIPTION}{multiplicity}")
    if cell("CLASS_DESCRIPTION") is not None:
        messages.append(f"[schema_class] {row.CLASS_DESCRIPTION}")
    if cell("SOURCE_URI") is not None:
        messages.append(f"[context_location] {row.SOURCE_URI} line {int(row.SOURCE_LINE)}")
    if cell("SOURCE_SNIPPET") is not None:
        messages.append(f"[context_snippet] {row.SOURCE_SNIPPET}")
    return messages


def message_prefix(violation_type, source=None, language="shacl"):
    """[<language>_message] = the text is the shape's/schema's own message;
    [engine_message] = the engine worded it. the validation functions stamp MESSAGE_SOURCE
    on every row; frames without it fall back to the violation-type namespace
    (triplets:* → engine). The constraint language ("shacl"; "rdfs" for
    schema-compiled runs) names the whole tag family in both report formats."""
    if source is not None and not pandas.isna(source):
        return f"[{language}_message]" if source == "shacl" else "[engine_message]"
    return ("[engine_message]" if str(violation_type).startswith("triplets:")
            else f"[{language}_message]")


def _expand(value, namespaces=None, undefined_namespace=CIM_NS):
    """Short KEY / violation type → URI (inverse of local_key / _component).

    Vocabulary prefixes (sh:, rdfs:, xsd:, schema:, triplets:) are report
    terms; anything else is a data KEY and expands through the schema
    namespaces (``undefined_namespace`` for a KEY they lack)."""
    value = str(value)
    if is_iri(value):
        return value
    if value in _COMPONENT_URI:
        return _COMPONENT_URI[value]
    if value.startswith("rdfs:"):        # schema-validation types are real vocabulary terms
        return f"{RDFS_NS}{value[5:]}"
    if value.startswith("xsd:"):
        return f"{XSD_NS}{value[4:]}"
    if value.startswith("schema:"):
        return f"{SCHEMA_ORG_NS}{value[7:]}"
    if value.startswith("sh:"):
        return f"{SH_NS}{value[3:]}"
    if value.startswith("triplets:"):
        return f"{TRIPLETS_NS}{value[len('triplets:'):]}"
    return absolute_key(value, namespaces, undefined_namespace)


def _resolve_format(path, format):
    """Explicit rdflib format wins; else path suffix via ``_EXT``; else turtle."""
    if format is not None:
        return format
    if path is not None:
        return _EXT.get(os.path.splitext(os.fspath(path))[1].lower(), "turtle")
    return "turtle"


def _default_path(fmt):
    """First suffix in ``_EXT`` that maps to ``fmt``, else ``.ttl``."""
    for suffix, name in _EXT.items():
        if name == fmt:
            return f"report{suffix}"
    return "report.ttl"


def export_to_shacl_report(violations, sources=None, path=None, export_to_memory=False,
                           format=None, report_source=None, report_references=None,
                           rdf_map=None, undefined_namespace=CIM_NS, source_root=None):
    r"""Violations frame → standard sh:ValidationReport (any rdflib format).

    Parameters
    ----------
    violations : DataFrame in VIOLATION_COLUMNS (any engine's output).
        Enrichment/location columns, when present, become additional
        ``sh:resultMessage``\ s (Description/Schema/Source).
    sources : optional
        The original CIM/XML files — runs the locate pass so each result
        carries a "Source: file line N" message. The sources are read only
        when the frame lacks one of the three SOURCE_* columns; all three on
        the frame are kept (see locations.locate_missing).
    path : str or Path, optional
        Output file. Default ``report.<ext>`` from the resolved format.
        Suffix selects format when ``format`` is None (``.xml``/``.rdf`` →
        RDF/XML, ``.ttl`` → turtle, …).
    export_to_memory : bool, default False
        Return a BytesIO (with .name) instead of writing to disk.
    format : str or None, default None
        rdflib serialize format. When None, derived from ``path`` suffix
        (unknown/missing → turtle). Explicit value always wins.
    report_source : str or sequence, optional
        ``dcterms:source`` on the ValidationReport (validated file name(s)).
        Metadata only — ``sources`` is the one that runs the locate pass.
        Default: the data file names the validation functions stamped in
        ``violations.attrs["validation"]``.
    report_references : str or sequence, optional
        ``dcterms:references`` on the ValidationReport (shape file name(s)).
        Plain labels — not the shapes object ``to_sarif(shapes=)`` takes.
        Default: the shape file names from ``violations.attrs["validation"]``.
    rdf_map : dict or str, optional
        Export schema — ``sh:resultPath`` KEYs expand to their profile namespace,
        and ``sh:value`` of an Association / Enumeration KEY is an IRI (the
        referenced object, the enumeration value); without it every value but
        a ``Type`` is a plain literal (see violations_to_report_graph).
    undefined_namespace : str, default CIM100
        Namespace for KEYs the schema does not declare (every KEY without one).
    source_root : str or Path, optional
        A file name that is an absolute path under this directory is
        written relative to it (forward slashes); every other name is
        written as given. One rule for every name in the report: SOURCE_URI
        (the "[context_location]" message; located by *sources* here or
        already on the frame), ``dcterms:source`` and ``dcterms:references``.
        ``references`` of a validation run are file names without
        directories, so they stay as they are. Absolute names outside the
        directory are named in one logged warning (see
        locations.relative_sources).
    """
    check_source_root(source_root)
    if sources is not None:
        violations = locate_missing(violations, sources, targets=False)
    fmt = _resolve_format(path, format)
    path = _default_path(fmt) if path is None else os.fspath(path)
    payload = (violations_to_report_graph(violations, report_source=report_source,
                                          report_references=report_references,
                                          rdf_map=load_rdf_map(rdf_map),
                                          undefined_namespace=undefined_namespace,
                                          source_root=source_root)
               .serialize(format=fmt).encode("utf-8"))
    if export_to_memory:
        buffer = io.BytesIO(payload)
        buffer.name = os.path.basename(path)
        return buffer

    with open(path, "wb") as file:
        file.write(payload)
    logger.info("Saved %s", path)
    return path


# ── tabular exports (csv / excel) — same metadata as the RDF/SARIF reports ───

# run metadata the SARIF run properties and the csv / excel sidecars leave out:
# the violated shapes' triples (the SHACL report embeds them), the per-rule-set
# counts concat_violations keeps (the totals are node_shapes / constraints) and
# the stable names of anonymous shapes the SARIF ruleIds are made of
UNEXPORTED = ("source_shapes", "rule_sets", "anonymous_shapes")


def _meta_rows(meta):
    """The attrs["validation"] dict as (KEY, VALUE) rows — lists fan out; an
    empty list keeps one blank row so "none" is stated, not implied. Without
    the UNEXPORTED keys."""
    return [(key, item) for key, value in meta.items() if key not in UNEXPORTED
            for item in ((value or ("",)) if isinstance(value, (list, tuple)) else (value,))]


def _buffer(name, payload):
    """BytesIO with .name — the in-memory file convention every export shares."""
    buffer = io.BytesIO(payload)
    buffer.name = name
    return buffer


def profile_names(value):
    """The profiles of a PROFILE cell — a list, tuple or numpy array of
    section keys, or text: the keys separated by commas, spaces around a key
    left out (a cell read back from a csv / Excel export, where profile_text
    wrote it); nulls and empty strings dropped."""
    values = (value if isinstance(value, (list, tuple, numpy.ndarray))
              else [name.strip() for name in value.split(",")] if isinstance(value, str) else (value,))
    return [str(name) for name in values if not pandas.isna(name) and str(name)]


def profile_text(value):
    """A PROFILE cell as text: the scoped section keys joined with ", " (display only)."""
    return ", ".join(profile_names(value)) or None


def _with_profile_text(violations):
    return (violations.assign(PROFILE=violations["PROFILE"].map(profile_text))
            if "PROFILE" in violations.columns else violations)


def _meta_frame(meta):
    return pandas.DataFrame(_meta_rows(meta), columns=["KEY", "VALUE"])


def violations_to_csv(violations, path="violations.csv", export_to_memory=False):
    """Violations frame → CSV, plus a ``<name>_meta.<ext>`` sidecar carrying
    the validation metadata (``violations.attrs["validation"]``) as KEY,VALUE
    rows (no sidecar when the frame carries none). ``export_to_memory=True``
    returns the BytesIO objects (with .name) instead of writing to disk —
    same convention as the other exports."""
    path = os.fspath(path)
    stem, suffix = os.path.splitext(os.path.basename(path))
    files = [_buffer(os.path.basename(path), _with_profile_text(violations).to_csv(index=False).encode("utf-8"))]
    meta = violations.attrs.get("validation")
    if meta:
        files.append(_buffer(f"{stem}_meta{suffix}",
                             _meta_frame(meta).to_csv(index=False).encode("utf-8")))
    if export_to_memory:
        return files
    directory = os.path.dirname(path)
    for file in files:
        target = os.path.join(directory, file.name)
        with open(target, "wb") as handle:
            handle.write(file.getvalue())
        logger.info("Saved %s", target)
    return path


def violations_to_excel(violations, path="violations.xlsx", export_to_memory=False):
    """Violations frame → Excel; the validation metadata
    (``violations.attrs["validation"]``) goes to a second "metadata" sheet.
    ``export_to_memory=True`` returns a BytesIO (with .name)."""
    path = os.fspath(path)
    meta = violations.attrs.get("validation")
    buffer = io.BytesIO()
    with pandas.ExcelWriter(buffer, engine="openpyxl") as writer:
        _with_profile_text(violations).to_excel(writer, sheet_name="violations", index=False)
        if meta:
            _meta_frame(meta).to_excel(writer, sheet_name="metadata", index=False)
    buffer.name = os.path.basename(path)
    buffer.seek(0)
    if export_to_memory:
        return buffer
    with open(path, "wb") as handle:
        handle.write(buffer.getvalue())
    logger.info("Saved %s", path)
    return path
