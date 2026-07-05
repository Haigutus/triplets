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

from dataclasses import dataclass, field

import pandas

logger = logging.getLogger(__name__)

IR_COLUMNS = ["shape_id", "target_class", "path", "inverse", "component", "params",
              "severity", "message", "name", "description"]

_SHAPE_FORMATS = {".ttl": "turtle", ".rdf": "xml", ".xml": "xml", ".nt": "nt", ".jsonld": "json-ld"}


@dataclass
class CompiledShapes:
    """Shapes compiled once, shared by all validation engines."""

    graph: object                 # rdflib.Graph — what pyshacl consumes
    ir: pandas.DataFrame          # constraint table — what the vectorized engines consume
    hash: str                     # content hash of the shape sources (cache key)
    plans: dict = field(default_factory=dict)  # engine name → compiled artifact (lazy)


_COMPILE_CACHE: dict = {}  # content hash → CompiledShapes


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
    compiled = CompiledShapes(graph=graph, ir=ir, hash=key)
    _COMPILE_CACHE[key] = compiled
    return compiled


def _load_shapes(shapes):
    """str/path | list of paths | rdflib.Graph → one rdflib.Graph of shapes."""
    import rdflib

    if isinstance(shapes, rdflib.Graph):
        return shapes

    paths = [shapes] if isinstance(shapes, (str, bytes)) or hasattr(shapes, "__fspath__") else list(shapes)
    graph = rdflib.Graph()
    for path in paths:
        suffix = str(path)[str(path).rfind("."):].lower()
        graph.parse(str(path), format=_SHAPE_FORMATS.get(suffix, "turtle"))
    return graph


def _content_hash(shapes):
    import rdflib

    digest = hashlib.sha256()
    if isinstance(shapes, rdflib.Graph):
        digest.update(b"\n".join(sorted(shapes.serialize(format="nt").encode().splitlines())))
        return digest.hexdigest()

    paths = [shapes] if isinstance(shapes, (str, bytes)) or hasattr(shapes, "__fspath__") else list(shapes)
    for path in paths:
        with open(path, "rb") as file:
            digest.update(file.read())
    return digest.hexdigest()


# ── shapes graph → constraint table ──────────────────────────────────────────

def parse_ir(graph) -> pandas.DataFrame:
    """Walk NodeShapes → property shapes → one IR row per constraint component.

    A NodeShape may declare several sh:targetClass (the ENTSO-E profiles do);
    every constraint row is emitted once per target class.
    """
    import rdflib

    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    rows = []
    for shape in graph.subjects(rdflib.RDF.type, SH.NodeShape):
        for target in graph.objects(shape, SH.targetClass):
            rows.extend(_node_rows(graph, SH, shape, _local(target)))

    ir = pandas.DataFrame(rows, columns=IR_COLUMNS)
    unknown = ir.loc[~ir["component"].isin(KNOWN_COMPONENTS), "component"].unique()
    if len(unknown):
        logger.info("IR contains components no vectorized engine implements yet: %s", ", ".join(unknown))
    return ir


def _node_rows(graph, SH, shape, target_class):
    """NodeShape-level constraints (closed, sparql) + its property shapes' rows."""
    meta = _shape_meta(graph, SH, shape, target_class, path=None, inverse=False)
    rows = []

    closed = graph.value(shape, SH.closed)
    if closed is not None and closed.toPython() is True:
        ignored = graph.value(shape, SH.ignoredProperties)
        rows.append({**meta, "component": "sh:closed",
                     "params": _rdf_list(graph, ignored, _local) if ignored is not None else []})

    rows.extend(_sparql_rows(graph, SH, shape, meta))
    for property_shape in graph.objects(shape, SH.property):
        rows.extend(_shape_rows(graph, SH, property_shape, target_class))
    return rows


def _shape_rows(graph, SH, shape_uri, target_class):
    """One property shape → IR rows (one per constraint component present)."""
    path, inverse = _resolve_path(graph, SH, graph.value(shape_uri, SH.path))
    meta = _shape_meta(graph, SH, shape_uri, target_class, path, inverse)
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
            nested = [_shape_rows(graph, SH, item, target_class)
                      for item in _rdf_list(graph, head, lambda node: node)]
            rows.append({**meta, "component": component, "params": nested})

    negated = graph.value(shape_uri, SH["not"])
    if negated is not None:
        rows.append({**meta, "component": "sh:not",
                     "params": _shape_rows(graph, SH, negated, target_class)})

    return rows


def _sparql_rows(graph, SH, shape_uri, meta):
    """sh:sparql constraints; params is the SELECT text ($this/$PATH placeholders kept).

    The sparql node's own sh:message overrides the shape's. sh:prefixes stays
    unresolved for now — pyshacl executes these natively; the vectorized
    engines will resolve prefixes when they delegate to triplets.sparql.
    """
    rows = []
    for sparql in graph.objects(shape_uri, SH.sparql):
        select = graph.value(sparql, SH.select)
        if select is None:
            continue
        message = graph.value(sparql, SH.message)
        row = {**meta, "component": "sh:sparql", "params": str(select)}
        if message is not None:
            row["message"] = str(message)
        rows.append(row)
    return rows


def _shape_meta(graph, SH, shape_uri, target_class, path, inverse):
    severity = graph.value(shape_uri, SH.severity)
    message = graph.value(shape_uri, SH.message)
    name = graph.value(shape_uri, SH.name)
    description = graph.value(shape_uri, SH.description)
    return {
        "shape_id": str(shape_uri),
        "target_class": target_class,
        "path": path,
        "inverse": inverse,
        "severity": _local(severity) if severity is not None else "Violation",
        "message": str(message) if message is not None else None,
        "name": str(name) if name is not None else None,
        "description": str(description) if description is not None else None,
    }


def _resolve_path(graph, SH, path_node):
    """Resolve sh:path → (KEY name, inverse); handles sh:inversePath / sh:alternativePath."""
    import rdflib

    if path_node is None:
        return None, False
    if isinstance(path_node, rdflib.URIRef):
        return _local(path_node), False
    inverse = graph.value(path_node, SH.inversePath)
    if inverse is not None:
        return _local(inverse), True
    alternative = graph.value(path_node, SH.alternativePath)
    if alternative is not None:
        for item in _rdf_list(graph, alternative, lambda node: node):
            nested_inverse = graph.value(item, SH.inversePath)
            if nested_inverse is not None:
                return _local(nested_inverse), True
    return _local(path_node), False


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
        (SH.node, "sh:node", lambda g, v: str(v)),
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
