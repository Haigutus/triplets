"""Compile SHACL shapes once into an engine-agnostic IR (constraint table).

rdflib is the definitive shapes parser: shapes are parsed exactly once into
``CompiledShapes`` — the shapes graph (consumed by the pyshacl reference
engine) plus a flat constraint table (consumed by the pandas/polars/duckdb
executors, which never touch rdflib). Compilation is cached by content hash,
and each engine caches its own compiled artifact (LazyFrame plan builders,
SQL) in ``CompiledShapes.plans`` — re-validating new data against the same
shapes never recompiles anything.

IR: one row per shape x path x constraint component.
    shape_id, target_class, path, inverse, component, params,
    severity, message, name, description
``params`` holds the component's parameter: a scalar (sh:minCount), a list
(sh:in), or nested row-dict lists (sh:or / sh:and / sh:not). Unknown
components are kept as rows and logged — engines skip what they don't
implement; pyshacl still covers the full spec.
"""
import hashlib
import logging
import os

from dataclasses import dataclass, field

import pandas

from .._caches import register_cache

logger = logging.getLogger(__name__)

IR_COLUMNS = ["shape_id", "target_class", "target_kind", "path", "inverse", "via_type",
              "component", "params", "severity", "message", "name", "description"]

_SHAPE_FORMATS = {".ttl": "turtle", ".rdf": "xml", ".xml": "xml", ".nt": "nt", ".jsonld": "json-ld"}


@dataclass
class CompiledShapes:
    """Shapes compiled once, shared by all validation engines."""

    graph: object                 # rdflib.Graph — what pyshacl consumes
    ir: pandas.DataFrame          # constraint table — what the vectorized engines consume
    hash: str                     # content hash of the shape sources (cache key)
    sources: tuple = ()           # shape file basenames (report metadata; () for a Graph)
    stats: dict = field(default_factory=dict)  # coverage facts (see _shape_stats)
    plans: dict = field(default_factory=dict)  # engine name → compiled artifact (lazy)


_COMPILE_CACHE: dict = register_cache({})  # content hash → CompiledShapes


def compile_shapes(shapes) -> CompiledShapes:
    """Parse SHACL *shapes* (str/path | list of paths | rdflib.Graph) once.

    Returns the cached ``CompiledShapes`` when the same shape content was
    compiled before (content hash — path identity does not matter).
    """
    key = _content_hash(shapes)
    if key in _COMPILE_CACHE:
        logger.debug("shapes compile cache hit: %s", key[:12])
        return _COMPILE_CACHE[key]

    graph = _load_shapes(shapes)
    ir = parse_ir(graph)
    logger.debug("compiled %d constraint rows from %d shape triples", len(ir), len(graph))
    compiled = CompiledShapes(graph=graph, ir=ir, hash=key, sources=_source_names(shapes),
                              stats=_shape_stats(graph, ir))
    _COMPILE_CACHE[key] = compiled
    return compiled


def _shape_stats(graph, ir):
    """Coverage facts for the report metadata: shape/constraint counts plus
    what the vectorized engines skip (mirrors the parse_ir warnings as data —
    the pyshacl reference engine covers everything, so validate() reports
    these only for vectorized runs)."""
    import rdflib

    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    skipped = set()
    for term in (SH.targetNode, SH.targetObjectsOf, SH.target, SH.xone):
        skipped |= {f"{subject}: sh:{_local(term)} not walked"
                    for subject, _ in graph.subject_objects(term)}
    for subject in set(graph.subjects(SH.path)):
        path_node = graph.value(subject, SH.path)
        if path_node is not None and _resolve_path(graph, SH, path_node)[0] is None:
            skipped.add(f"{subject}: unsupported sh:path form")
    unknown = ir.loc[~ir["component"].isin(KNOWN_COMPONENTS), "component"].unique()
    return {
        "node_shapes": sum(1 for _ in graph.subjects(rdflib.RDF.type, SH.NodeShape)),
        "constraints": len(ir),
        "skipped_shapes": sorted(skipped),
        "unknown_components": sorted(unknown),
    }


def _paths(shapes):
    """Shape source(s) → list of paths (single str/bytes/PathLike or sequence)."""
    return ([shapes] if isinstance(shapes, (str, bytes)) or hasattr(shapes, "__fspath__")
            else list(shapes))


def _source_names(shapes):
    """Shape file basenames for report metadata (a cache hit keeps the names
    of the first compile — content identity, not path identity)."""
    import rdflib

    if isinstance(shapes, rdflib.Graph):
        return ()
    return tuple(os.path.basename(str(path)) for path in _paths(shapes))


def _load_shapes(shapes):
    """str/path | list of paths | rdflib.Graph → one rdflib.Graph of shapes."""
    import rdflib

    if isinstance(shapes, rdflib.Graph):
        return shapes

    graph = rdflib.Graph()
    for path in _paths(shapes):
        suffix = str(path)[str(path).rfind("."):].lower()
        graph.parse(str(path), format=_SHAPE_FORMATS.get(suffix, "turtle"))
    return graph


def _content_hash(shapes):
    import rdflib

    digest = hashlib.sha256()
    if isinstance(shapes, rdflib.Graph):
        digest.update(b"\n".join(sorted(shapes.serialize(format="nt").encode().splitlines())))
        return digest.hexdigest()

    for path in _paths(shapes):
        with open(path, "rb") as file:
            digest.update(file.read())
    return digest.hexdigest()


# Nested/query components every vectorized engine delegates to the pandas
# implementations — part of the shared IR contract, defined here so no engine
# needs to import another engine for a constant.
FALLBACK_COMPONENTS = {"sh:or", "sh:and", "sh:not", "sh:node", "sh:sparql"}


def split_rules(ir, implemented, fallback_components, engine):
    """IR rows → (vectorized, fallback, skipped components) against an
    engine's registries.

    Engines cache the result in ``CompiledShapes.plans[engine]`` so the split
    runs once per compiled shapes, not once per validate call; validate()
    reads the skipped components into the report metadata.
    """
    rules = list(ir.itertuples())
    vectorized = [rule for rule in rules if rule.component in implemented]
    fallback = [rule for rule in rules if rule.component in fallback_components]
    skipped = {rule.component for rule in rules} - set(implemented) - set(fallback_components)
    if skipped:
        logger.debug("%s engine skips components: %s (pyshacl covers them)",
                     engine, ", ".join(sorted(skipped)))
    return vectorized, fallback, sorted(skipped)


# ── shapes graph → constraint table ──────────────────────────────────────────

def parse_ir(graph) -> pandas.DataFrame:
    """Walk NodeShapes → property shapes → one IR row per constraint component.

    A NodeShape may declare several sh:targetClass (the ENTSO-E profiles do);
    every constraint row is emitted once per target. ``sh:targetSubjectsOf``
    targets compile the same way with target_kind="subjectsOf" — the engines
    resolve the focus as the subjects carrying that KEY instead of a class's
    instances.
    """
    import rdflib

    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    rows = []
    for shape in graph.subjects(rdflib.RDF.type, SH.NodeShape):
        for target in graph.objects(shape, SH.targetClass):
            rows.extend(_node_rows(graph, SH, shape, _local(target)))
        for target in graph.objects(shape, SH.targetSubjectsOf):
            rows.extend(_node_rows(graph, SH, shape, _local(target), target_kind="subjectsOf"))

    # Only sh:targetClass / sh:targetSubjectsOf are walked into the IR —
    # shapes reached exclusively through other target declarations (or
    # sh:xone) are INVISIBLE to the vectorized engines and would silently
    # under-validate. Warn loudly; the pyshacl reference engine covers them
    # (engine="pyshacl").
    invisible = {term: count for term in
                 (SH.targetNode, SH.targetObjectsOf, SH.target, SH.xone)
                 if (count := sum(1 for _ in graph.subject_objects(term)))}
    if invisible:
        logger.warning(
            "shapes use SHACL features the vectorized engines do not implement — "
            "affected shapes are skipped by the polars/pandas/duckdb engines; "
            "use engine=\"pyshacl\" for full coverage: %s",
            ", ".join(f"sh:{_local(term)} (x{count})" for term, count in invisible.items()))

    ir = pandas.DataFrame(rows, columns=IR_COLUMNS)
    # DataFrame construction turns None into NaN — which is TRUTHY, so the
    # engines' `rule.message or default` would emit NaN instead of the default
    # (object dtype: the str dtype would coerce the None straight back to NaN)
    ir["message"] = ir["message"].astype(object).where(ir["message"].notna(), None)
    unknown = ir.loc[~ir["component"].isin(KNOWN_COMPONENTS), "component"].unique()
    if len(unknown):
        logger.info("IR contains components no vectorized engine implements yet: %s", ", ".join(unknown))
    return ir


def _node_rows(graph, SH, shape, target_class, target_kind="class"):
    """NodeShape-level constraints (closed, sparql) + its property shapes' rows."""
    meta = _shape_meta(graph, SH, shape, target_class, path=None, inverse=False,
                       target_kind=target_kind)
    rows = []

    closed = graph.value(shape, SH.closed)
    if closed is not None and closed.toPython() is True:
        # params = the complete allowed list, resolved at compile time:
        # sh:ignoredProperties + every (non-inverse) sh:property path of THIS shape
        ignored = graph.value(shape, SH.ignoredProperties)
        allowed = _rdf_list(graph, ignored, _local) if ignored is not None else []
        for property_shape in graph.objects(shape, SH.property):
            path, inverse, _ = _resolve_path(graph, SH, graph.value(property_shape, SH.path))
            if path is not None and not inverse:
                allowed.append(path)
        rows.append({**meta, "component": "sh:closed", "params": allowed})

    rows.extend(_sparql_rows(graph, SH, shape, meta))
    for property_shape in graph.objects(shape, SH.property):
        rows.extend(_shape_rows(graph, SH, property_shape, target_class, parent=meta,
                                target_kind=target_kind))
    return rows


def _shape_rows(graph, SH, shape_uri, target_class, visited=frozenset(), parent=None,
                target_kind="class"):
    """One property shape → IR rows (one per constraint component present).

    *visited* tracks shapes already expanded through sh:node / sh:or / sh:and /
    sh:not, so shape graphs that reference each other cannot recurse forever.
    *parent* is the owning NodeShape's meta — a property shape without its own
    sh:name / sh:description inherits them (authors commonly title the node shape).
    """
    if str(shape_uri) in visited:
        logger.warning("shape reference cycle at %s — constraint dropped", shape_uri)
        return []
    visited = visited | {str(shape_uri)}
    path_node = graph.value(shape_uri, SH.path)
    path, inverse, via_type = _resolve_path(graph, SH, path_node)
    if path_node is not None and path is None:
        logger.warning(
            "sh:path at %s is a property path the vectorized engines cannot express "
            "as one KEY — property shape skipped; use engine=\"pyshacl\" for coverage",
            shape_uri)
        return []
    meta = _shape_meta(graph, SH, shape_uri, target_class, path, inverse,
                       target_kind=target_kind, via_type=via_type)
    if parent is not None:
        meta["name"] = meta["name"] or parent["name"]
        meta["description"] = meta["description"] or parent["description"]
    rows = []

    for term, component, transform in _components(SH):
        value = graph.value(shape_uri, term)
        if value is not None:
            rows.append({**meta, "component": component, "params": transform(graph, value)})

    rows.extend(_sparql_rows(graph, SH, shape_uri, meta))

    # logical operators: params are nested row-dict lists, recursion via RDF lists
    for term, component in ((SH["or"], "sh:or"), (SH["and"], "sh:and")):
        head = graph.value(shape_uri, term)
        if head is not None:
            nested = [_shape_rows(graph, SH, item, target_class, visited, target_kind=target_kind)
                      for item in _rdf_list(graph, head, lambda node: node)]
            rows.append({**meta, "component": component, "params": nested})

    negated = graph.value(shape_uri, SH["not"])
    if negated is not None:
        rows.append({**meta, "component": "sh:not",
                     "params": _shape_rows(graph, SH, negated, target_class, visited,
                                           target_kind=target_kind)})

    node_shape = graph.value(shape_uri, SH.node)
    if node_shape is not None:
        nested = _node_expansion(graph, SH, node_shape, target_class, visited, target_kind)
        if nested is None:
            logger.warning("sh:node cycle at %s — constraint dropped (%s)", node_shape, shape_uri)
        else:
            rows.append({**meta, "component": "sh:node",
                         "params": {"shape": _local(node_shape), "rows": nested}})

    return rows


def _node_expansion(graph, SH, node_shape, target_class, visited, target_kind="class"):
    """sh:node — expand the referenced shape into nested IR rows at compile time.

    params = {"shape": local name (for messages), "rows": nested row dicts}.
    The engines run the nested rows against the referenced value nodes.
    Returns None on a reference cycle.
    """
    key = str(node_shape)
    if key in visited:
        return None
    visited = visited | {key}

    rows = []
    meta = _shape_meta(graph, SH, node_shape, target_class, path=None, inverse=False,
                       target_kind=target_kind)
    rows.extend(_sparql_rows(graph, SH, node_shape, meta))
    for property_shape in graph.objects(node_shape, SH.property):
        rows.extend(_shape_rows(graph, SH, property_shape, target_class, visited,
                                target_kind=target_kind))
    return rows


def _sparql_rows(graph, SH, shape_uri, meta):
    """sh:sparql constraints. params carries everything an engine needs to run
    the query without rdflib:

        {"select":   SELECT text ($this / $PATH placeholders kept),
         "prefixes": resolved "PREFIX ..." header lines (sh:prefixes → sh:declare),
         "path":     full IRI of the owning shape's sh:path, or None}

    The sparql node's own sh:message overrides the shape's.
    """
    rows = []
    path = graph.value(shape_uri, SH.path)
    for sparql in graph.objects(shape_uri, SH.sparql):
        select = graph.value(sparql, SH.select)
        if select is None:
            continue
        prefixes = "".join(
            f"PREFIX {graph.value(declaration, SH.prefix)}: <{graph.value(declaration, SH.namespace)}>\n"
            for ontology in graph.objects(sparql, SH.prefixes)
            for declaration in graph.objects(ontology, SH.declare))
        message = graph.value(sparql, SH.message)
        row = {**meta, "component": "sh:sparql",
               "params": {"select": str(select), "prefixes": prefixes,
                          # $PATH substitution needs the full IRI; only direct paths qualify
                          "path": str(path) if type(path).__name__ == "URIRef" else None}}
        if message is not None:
            row["message"] = str(message)
        rows.append(row)
    return rows


def _shape_meta(graph, SH, shape_uri, target_class, path, inverse, target_kind="class",
                via_type=False):
    severity = graph.value(shape_uri, SH.severity)
    message = graph.value(shape_uri, SH.message)
    name = graph.value(shape_uri, SH.name)
    description = graph.value(shape_uri, SH.description)
    return {
        "shape_id": str(shape_uri),
        # target_class holds the target term's local name: a class name for
        # target_kind="class", the property KEY for target_kind="subjectsOf"
        "target_class": target_class,
        "target_kind": target_kind,
        "path": path,
        "inverse": inverse,
        # ( assoc rdf:type ) sequence path: the constraint's value nodes are
        # the types of the referenced objects, not the association values
        "via_type": via_type,
        "severity": _local(severity) if severity is not None else "Violation",
        "message": str(message) if message is not None else None,
        "name": str(name) if name is not None else None,
        "description": str(description) if description is not None else None,
    }


def _resolve_path(graph, SH, path_node):
    """Resolve sh:path → (KEY name, inverse, via_type); handles sh:inversePath,
    sh:alternativePath with a nested inverse, and the two-step sequence
    ``( assoc rdf:type )`` (the profile "valueType" pattern — the constraint
    applies to the type of the referenced object, via_type=True). Any other
    blank-node path (longer sequences, zeroOrMorePath, ...) spans more than
    one KEY and cannot be expressed as an IR row → (None, False, False);
    callers skip the shape."""
    import rdflib

    if path_node is None:
        return None, False, False
    if isinstance(path_node, rdflib.URIRef):
        return _local(path_node), False, False
    inverse = graph.value(path_node, SH.inversePath)
    if inverse is not None:
        return _local(inverse), True, False
    alternative = graph.value(path_node, SH.alternativePath)
    if alternative is not None:
        for item in _rdf_list(graph, alternative, lambda node: node):
            nested_inverse = graph.value(item, SH.inversePath)
            if nested_inverse is not None:
                return _local(nested_inverse), True, False
    if graph.value(path_node, rdflib.RDF.first) is not None:   # sequence path
        steps = _rdf_list(graph, path_node, lambda node: node)
        if (len(steps) == 2 and isinstance(steps[0], rdflib.URIRef)
                and steps[1] == rdflib.RDF.type):
            return _local(steps[0]), False, True
    return None, False, False


# SHACL term → (component short name, params transform). Short names match the
# VIOLATION_TYPE values in shacl_report._COMPONENT_MAP so IR rows, engine
# output and pyshacl output all speak the same vocabulary.
def _components(SH):
    return [
        (SH.minCount, "sh:minCount", lambda g, v: int(v)),
        (SH.maxCount, "sh:maxCount", lambda g, v: int(v)),
        (SH.datatype, "sh:datatype", lambda g, v: f"xsd:{_local(v)}"),
        (SH["class"], "sh:class", lambda g, v: _local(v)),
        (SH.minInclusive, "sh:minInclusive", lambda g, v: float(v)),
        (SH.maxInclusive, "sh:maxInclusive", lambda g, v: float(v)),
        (SH.minExclusive, "sh:minExclusive", lambda g, v: float(v)),
        (SH.maxExclusive, "sh:maxExclusive", lambda g, v: float(v)),
        (SH.pattern, "sh:pattern", lambda g, v: str(v)),
        (SH.minLength, "sh:minLength", lambda g, v: int(v)),
        (SH.maxLength, "sh:maxLength", lambda g, v: int(v)),
        (SH.nodeKind, "sh:nodeKind", lambda g, v: _local(v)),
        (SH.hasValue, "sh:hasValue", lambda g, v: _value(v)),
        (SH["in"], "sh:in", lambda g, v: _rdf_list(g, v, _value)),
        (SH.equals, "sh:equals", lambda g, v: _local(v)),
        (SH.disjoint, "sh:disjoint", lambda g, v: _local(v)),
        (SH.lessThan, "sh:lessThan", lambda g, v: _local(v)),
        (SH.lessThanOrEquals, "sh:lessThanOrEquals", lambda g, v: _local(v)),
    ]


KNOWN_COMPONENTS = {
    "sh:minCount", "sh:maxCount", "sh:datatype", "sh:class", "sh:minInclusive",
    "sh:maxInclusive", "sh:minExclusive", "sh:maxExclusive", "sh:pattern",
    "sh:minLength", "sh:maxLength", "sh:nodeKind", "sh:hasValue", "sh:in",
    "sh:equals", "sh:disjoint", "sh:lessThan", "sh:lessThanOrEquals", "sh:node",
    "sh:closed", "sh:sparql", "sh:or", "sh:and", "sh:not",
}


def _rdf_list(graph, head, transform):
    from rdflib.collection import Collection
    return [transform(item) for item in Collection(graph, head)]


def _local(term):
    """IRI → local name (matches the short KEY/VALUE names in triplet data)."""
    return str(term).split("#")[-1].split("/")[-1]


def _value(term):
    """sh:in / sh:hasValue member → comparable VALUE string (IRIs → local name)."""
    if type(term).__name__ == "Literal":
        return str(term)
    return _local(term)
