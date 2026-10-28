"""Compile SHACL shapes once into an engine-agnostic IR (constraint table).

rdflib is the definitive shapes parser: shapes are parsed exactly once into
``CompiledShapes`` — the shapes graph (consumed by the pyshacl reference
engine) plus a flat constraint table (consumed by the pandas/polars/duckdb
executors, which never touch rdflib). Compilation is cached by content hash,
and each engine caches its own compiled artifact (LazyFrame plan builders,
SQL) in ``CompiledShapes.plans`` — re-validating new data against the same
shapes never recompiles anything.

IR: one row per shape x target x path x constraint component.
    shape_id, shapes, target_on, target_by, path, inverse, via_type,
    component, params, severity, message, name, description
``params`` holds the component's parameter: a scalar (sh:minCount), a list
(sh:in), or nested row-dict lists (sh:or / sh:and / sh:xone / sh:not, and the
``rows`` of sh:node / sh:property). Unknown
components are kept as rows and logged — engines skip what they don't
implement; pyshacl still covers the full spec. ``shapes`` is the row's
lineage — the shape ids whose ``sh:deactivated`` removes it; every shape
compiles, and ``CompiledShapes.active()`` filters the switched-off ones.
"""
import hashlib
import logging
import os

from dataclasses import dataclass, field

import pandas

from .._caches import register_cache
from ..iri import SH_NS, local_id, local_key, local_value, split_iri

logger = logging.getLogger(__name__)

IR_COLUMNS = ["shape_id", "shapes", "target_on", "target_by", "path", "inverse", "via_type",
              "component", "params", "severity", "message", "name", "description"]

_SHAPE_FORMATS = {".ttl": "turtle", ".rdf": "xml", ".xml": "xml", ".nt": "nt", ".jsonld": "json-ld"}


@dataclass
class CompiledShapes:
    """Shapes compiled once, shared by all validation engines."""

    graph: object                 # rdflib.Graph — what pyshacl consumes
    ir: pandas.DataFrame          # constraint table — what the vectorized engines consume
    hash: str                     # content hash of the shape sources (cache key; a filtered compile keeps it)
    sources: tuple = ()           # shape file basenames (report metadata; () for a Graph)
    language: str = "shacl"       # constraint language — "rdfs" for schema-compiled IR
    stats: dict = field(default_factory=dict)  # coverage facts (see _shape_stats)
    plans: dict = field(default_factory=dict)  # engine name → compiled artifact (lazy)
    # shape ids switched off: on the full compile those the graph declares
    # sh:deactivated; on a filtered compile the set switched off in it
    deactivated: frozenset = frozenset()
    base: object = field(default=None, repr=False)   # the full compile this one was filtered from
    _active: dict = field(default_factory=dict, repr=False, compare=False)   # switched-off set → filtered
    # what the IR cannot express, as (lineage, text, drop): a switched-off shape takes its entries
    # along; drop — what pyshacl's graph loses for a malformed shape (_malformed), else None
    _skipped: frozenset = field(default=frozenset(), repr=False, compare=False)

    def active(self, deactivated_shapes=None):
        """The run's rule set: *deactivated_shapes* (a shape IRI or an iterable of
        them) switched off — None = this compile's own set (on the full compile the
        shapes the graph declares), [] = every shape runs, any other set replaces the
        declared one. The same compile and ``hash``, its IR without their rows; cached
        per set on the full compile, the full compile itself when nothing is declared
        or chosen.
        [] over a declared set is a compile of its own: pyshacl then validates the
        declared shapes too."""
        base = self.base or self
        if isinstance(deactivated_shapes, str):
            deactivated_shapes = [deactivated_shapes]
        chosen = self.deactivated if deactivated_shapes is None else frozenset(map(str, deactivated_shapes))
        if not chosen and not base.deactivated:
            return base
        if base.graph is None:
            raise ValueError("deactivated_shapes needs SHACL shapes — a schema compile has none")
        if chosen not in base._active:
            base._active[chosen] = _switched_off(base, chosen)
        return base._active[chosen]


_COMPILE_CACHE: dict = register_cache({})  # content hash → CompiledShapes
_GRAPH_CACHE: dict = register_cache({})    # file content digest → parsed rdflib.Graph


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
    skipped = set()
    ir = parse_ir(graph, skipped)
    logger.debug("compiled %d constraint rows from %d shape triples", len(ir), len(graph))
    compiled = CompiledShapes(graph=graph, ir=ir, hash=key, sources=_source_names(shapes),
                              stats=_shape_stats(graph, ir, skipped), deactivated=_declared_deactivated(graph),
                              _skipped=frozenset(skipped))
    _COMPILE_CACHE[key] = compiled
    return compiled


def _shape_stats(graph, ir, skipped, off=frozenset()):
    """Coverage facts for the report metadata: shape/constraint counts plus
    what the vectorized engines skip (parse_ir's *skipped* entries as data —
    the pyshacl reference engine covers everything but the malformed shapes, so
    validate_shacl() reports for it only ``malformed_shapes``: the entries a
    malformed shape causes). *off*: the shapes switched off."""
    import rdflib

    SH = rdflib.Namespace(SH_NS)
    return {
        "node_shapes": sum(1 for shape in graph.subjects(rdflib.RDF.type, SH.NodeShape) if str(shape) not in off),
        "constraints": len(_distinct(ir)),
        "skipped_shapes": sorted({text for _, text, _ in skipped}),
        "malformed_shapes": sorted({text for _, text, drop in skipped if drop is not None}),
        "unknown_components": _unknown_components(ir),
    }


def _unknown_components(ir):
    return sorted(_components_in(ir.to_dict("records")) - KNOWN_COMPONENTS)


def _components_in(rows):
    """Every component in *rows*, the nested ones included."""
    found = set()
    for row in rows:
        found.add(row["component"])
        for member in _nested(row["component"], row["params"]):
            found |= _components_in(member)
    return found


def _declared_deactivated(graph):
    """Shape ids the graph marks sh:deactivated, as pyshacl reads the flag:
    bool(literal.value) — true, a non-empty plain literal (so a plain "false")
    or a non-zero number deactivates; "false"^^xsd:boolean and an ill-typed
    literal (no value) do not. A non-literal or a second value makes the shape
    malformed: the compile skips it (_malformed)."""
    import rdflib

    SH = rdflib.Namespace(SH_NS)
    off = set()
    for shape in set(graph.subjects(SH.deactivated)):
        flags = set(graph.objects(shape, SH.deactivated))
        if len(flags) == 1 and isinstance(flag := flags.pop(), rdflib.Literal) and bool(flag.value):
            off.add(str(shape))
    return frozenset(off)


def _is_iri(shape_id):
    return "://" in shape_id or shape_id.startswith("urn:")


def _term(shape_id):
    """A shape id (``shape_id`` / ``shapes`` string form) → its rdflib term."""
    import rdflib
    return rdflib.URIRef(shape_id) if _is_iri(shape_id) else rdflib.BNode(shape_id)


def _mark_switched_off(graph, compiled):
    """*graph* — the compiled shapes graph or a part of it — with sh:deactivated
    true on exactly *compiled*'s switched-off shapes: re-enabled shapes lose
    their declaration. In place, and only on shapes that are subjects in
    *graph*; a no-op when the set is the declared one."""
    import rdflib

    deactivated = rdflib.URIRef(SH_NS + "deactivated")
    for shape in compiled.deactivated ^ (compiled.base or compiled).deactivated:
        node = _term(shape)
        if (node, None, None) not in graph:
            continue
        graph.remove((node, deactivated, None))   # also a declared false: two values would not load
        if shape in compiled.deactivated:
            graph.add((node, deactivated, rdflib.Literal(True)))
    return graph


def _switched_off(base, off):
    """*base* with the shapes in *off* switched off: rows whose lineage meets *off*
    dropped, nested members and sh:node targets pruned the same way. Engines cache
    their split per object, so the filtered compile gets fresh ``plans``; its
    ``hash`` stays the shape files' — ``deactivated`` names what is off."""
    unknown = sorted(shape for shape in off if (_term(shape), None, None) not in base.graph)
    if unknown:
        logger.warning("deactivated_shapes names no shape in the shapes graph: %s", ", ".join(unknown))
    ir = base.ir.loc[base.ir["shapes"].map(off.isdisjoint)].reset_index(drop=True)
    ir["params"] = [_prune(component, params, off) for component, params in zip(ir["component"], ir["params"])]
    skipped = frozenset(entry for entry in base._skipped if off.isdisjoint(entry[0]))
    return CompiledShapes(graph=base.graph, ir=ir, hash=base.hash,
                          sources=base.sources, language=base.language,
                          stats=_shape_stats(base.graph, ir, skipped, off), deactivated=off, base=base,
                          _skipped=skipped)


def _without(rows, off):
    """Nested row dicts without the switched-off ones, their own nesting pruned."""
    return [{**row, "params": _prune(row["component"], row["params"], off)}
            for row in rows if off.isdisjoint(row["shapes"])]


def _prune(component, params, off):
    """A switched-off member or sh:node target empties: an empty sh:or / sh:and /
    sh:xone member and sh:node target conform, an empty sh:not shape flags every value —
    pyshacl's sh:deactivated semantics."""
    if component not in _NESTED:
        return params
    lists, rebuild = _NESTED[component]
    return rebuild(params, [_without(rows, off) for rows in lists(params)])


# component → (its nested row lists, params rebuilt from new lists)
_NESTED = {
    "sh:or": (lambda params: params, lambda params, lists: lists),
    "sh:and": (lambda params: params, lambda params, lists: lists),
    "sh:xone": (lambda params: params, lambda params, lists: lists),
    "sh:not": (lambda params: [params], lambda params, lists: lists[0]),
    "sh:node": (lambda params: [params["rows"]], lambda params, lists: {**params, "rows": lists[0]}),
    "sh:property": (lambda params: [params["rows"]], lambda params, lists: {**params, "rows": lists[0]}),
}


def _nested(component, params):
    """The nested row lists of a logical, sh:node or sh:property row; [] for any other component."""
    return _NESTED[component][0](params) if component in _NESTED else []


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
    """str/path | list of paths | rdflib.Graph → one rdflib.Graph of shapes.

    Each file is parsed once per content (cached) — different shape unions
    sharing member files (the NC per-area unions) reuse the parsed graphs.
    The union is always a FRESH graph: cached graphs are never handed out,
    because pyshacl (advanced=True) may mutate the shapes graph it receives.
    Cached bnode labels shared across unions are a feature — sh:sourceShape
    labels stay stable for the same shape file everywhere."""
    import rdflib

    if isinstance(shapes, rdflib.Graph):
        return shapes

    graph = rdflib.Graph()
    for path in _paths(shapes):
        cached = _parsed_graph(path)
        for triple in cached:
            graph.add(triple)
        for prefix, namespace in cached.namespaces():
            graph.bind(prefix, namespace)
    return graph


def _parsed_graph(path):
    """The file's parsed graph, cached by content digest (path-independent)."""
    import rdflib

    key = _file_digest(path)
    if key not in _GRAPH_CACHE:
        suffix = str(path)[str(path).rfind("."):].lower()
        graph = rdflib.Graph()
        graph.parse(str(path), format=_SHAPE_FORMATS.get(suffix, "turtle"))
        _GRAPH_CACHE[key] = graph
    return _GRAPH_CACHE[key]


def _file_digest(path):
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def _content_hash(shapes):
    """Union key: sha256 over the SORTED SET of per-file digests — path,
    order and duplicates don't matter, so reordered/overlapping unions of the
    same files hit the same compile cache entry."""
    import rdflib

    digest = hashlib.sha256()
    if isinstance(shapes, rdflib.Graph):
        digest.update(b"\n".join(sorted(shapes.serialize(format="nt").encode().splitlines())))
        return digest.hexdigest()

    for file_digest in sorted({_file_digest(path) for path in _paths(shapes)}):
        digest.update(file_digest.encode())
    return digest.hexdigest()


# Nested/query components every vectorized engine delegates to the pandas
# implementations — part of the shared IR contract, defined here so no engine
# needs to import another engine for a constant.
FALLBACK_COMPONENTS = {"sh:or", "sh:and", "sh:xone", "sh:not", "sh:node", "sh:property", "sh:sparql"}


def split_rules(ir, implemented, fallback_components, engine):
    """IR rows → (vectorized, fallback, skipped components) against an
    engine's registries.

    Engines cache the result in ``CompiledShapes.plans[engine]`` so the split
    runs once per compiled shapes, not once per validate call; validate_shacl()
    reads the skipped components into the report metadata.
    """
    rules = list(_distinct(ir).itertuples())
    vectorized = [rule for rule in rules if rule.component in implemented]
    fallback = [rule for rule in rules if rule.component in fallback_components]
    skipped = {rule.component for rule in rules} - set(implemented) - set(fallback_components)
    if skipped:
        logger.debug("%s engine skips components: %s (pyshacl covers them)",
                     engine, ", ".join(sorted(skipped)))
    return vectorized, fallback, sorted(skipped)


def _distinct(ir):
    """One rule per constraint for the engines: rows that differ only in lineage
    (one property shape under two node shapes with a common target) run once —
    the polars batch path counts every rule row (issue #99)."""
    signature = ir.drop(columns=["shapes"]).assign(params=ir["params"].astype(str))
    return ir.loc[~signature.duplicated()]


# ── shapes graph → constraint table ──────────────────────────────────────────

def parse_ir(graph, skipped=None) -> pandas.DataFrame:
    """Walk the targeted NodeShapes → one IR row per constraint component.

    A NodeShape may declare several targets (the ENTSO-E profiles do); every
    constraint row is emitted once per target, ``target_by`` saying how the
    engines pick the rows: ``Type`` (sh:targetClass), ``KEY`` (sh:targetSubjectsOf:
    the IDs having that KEY), ``VALUE`` (sh:targetObjectsOf: the VALUEs under
    that KEY), ``ID`` (sh:targetNode), ``SPARQL`` (sh:target with sh:select: the
    IDs the query projects as ``?this``, resolved per run; ``target_on`` is the
    executable query text). *skipped* collects what the IR cannot express as
    (lineage, text, drop) entries, for the run metadata (see CompiledShapes._skipped).
    """
    import rdflib

    SH = rdflib.Namespace(SH_NS)
    skipped = set() if skipped is None else skipped
    targets = ((SH.targetClass, "Type", lambda node: local_value(node, "class")),
               (SH.targetSubjectsOf, "KEY", local_key),
               (SH.targetObjectsOf, "VALUE", local_key),
               # the ID as the data spells it (urn:uuid: stripped); any other IRI
               # verbatim — a targetNode need not occur in the data at all
               (SH.targetNode, "ID", local_id))
    rows = []
    for shape in graph.subjects(rdflib.RDF.type, SH.NodeShape):
        for term, target_by, name in targets:
            for target in graph.objects(shape, term):
                rows.extend(_shape_rows(graph, SH, shape, name(target), target_by=target_by, skipped=skipped))
        # sh:select makes a SPARQL target whatever the declared type (pyshacl's rule)
        for target in graph.objects(shape, SH.target):
            select = graph.value(target, SH.select)
            if select is None:   # a SPARQLTargetType instance, a JS or custom target — pyshacl only
                logger.warning("%s: sh:target without sh:select — shape skipped by the vectorized engines; "
                               "use engine=\"pyshacl\"", shape)
                skipped.add(((str(shape),), f"{shape}: sh:target not walked (no sh:select)", None))
                continue
            rows.extend(_shape_rows(graph, SH, shape, _prefixes(graph, SH, target) + str(select),
                                    target_by="SPARQL", skipped=skipped))
    # pyshacl (advanced=True) applies SPARQLRule inference; the IR does not
    skipped |= {((str(subject),), f"{subject}: sh:rule not applied", None) for subject, _ in graph.subject_objects(SH.rule)}
    # a malformed shape the walk never reaches still makes pyshacl refuse the whole graph
    walked = {drop for _, _, drop in skipped}
    for shape in {subject for subject, predicate in graph.subject_predicates() if predicate in SH} - walked:
        if reason := _malformed(graph, SH, shape):
            skipped.add(((str(shape),), f"{shape}: {reason}", shape))

    ir = pandas.DataFrame(rows, columns=IR_COLUMNS)
    # identical re-declarations behave like ONE shape (issue #99): the ENTSO-E
    # Simple files re-declare shared property-shape URIs per profile file —
    # duplicated rules double-counted in the polars batch path. Rows that differ
    # only in lineage (one property shape under two node shapes with the same
    # target) both stay, so switching one node shape off keeps the other's;
    # split_rules hands the engines one of them.
    signature = ir.assign(params=ir["params"].astype(str))
    ir = ir.loc[~signature.duplicated()].reset_index(drop=True)
    # DataFrame construction turns None into NaN — which is TRUTHY, so the
    # engines' `rule.message or default` would emit NaN instead of the default
    # and `rule.path is None` (a focus-level operator) would never hold
    # (object dtype: the str dtype would coerce the None straight back to NaN)
    for column in ("message", "path"):
        ir[column] = ir[column].astype(object).where(ir[column].notna(), None)
    unknown = _unknown_components(ir)
    if unknown:
        logger.info("IR contains components no vectorized engine implements yet: %s", ", ".join(unknown))
    return ir


def _shape_rows(graph, SH, shape_uri, target_on, visited=frozenset(), parent=None,
                target_by="Type", value_path=None, skipped=None, shapes=(), judged=False):
    """One shape → IR rows (one per constraint component present): the targeted
    NodeShape, each of its property shapes, a shape referenced through sh:node,
    a member of sh:or / sh:and / sh:xone / sh:not — one walker for all of them.

    *visited* tracks shapes already expanded through sh:node and the logical
    operators, so shape graphs that reference each other cannot recurse forever.
    *parent* is the owning shape's meta — a property shape without its own
    sh:name / sh:description inherits them (authors commonly title the node shape).
    *value_path*: the owning property shape's (path, inverse, via_type) for a
    shape nested in its logical operators — a nested shape without its own
    sh:path constrains that property's value nodes.
    *skipped* collects what the IR cannot express, as (lineage, text, drop) entries;
    a malformed shape (_malformed) compiles to no rows and one such entry.
    *shapes* is the lineage of the shapes enclosing this one.
    *judged*: the shape is judged for conformance — a sh:node target, a logical
    member, or a shape below one. A property shape's own sh:property then makes
    one sh:node row per value node (SHACL §4.7.1); reached from the targeted
    NodeShape through sh:property only, it is a sh:property row whose property
    shapes report their own findings on the value nodes (§4.7.2).
    """
    if str(shape_uri) in visited:
        logger.warning("shape reference cycle at %s — constraint dropped", shape_uri)
        return []
    visited = visited | {str(shape_uri)}
    reason = _malformed(graph, SH, shape_uri)
    if reason:
        logger.warning("%s: %s — shape skipped", shape_uri, reason)
        skipped.add(((*shapes, str(shape_uri)), f"{shape_uri}: {reason}", shape_uri))
        return []
    path_node = graph.value(shape_uri, SH.path)
    path, inverse, via_type = _resolve_path(graph, SH, path_node)
    if path_node is None and value_path is not None:
        path, inverse, via_type = value_path
    if path_node is not None and path is None:
        logger.warning(
            "sh:path at %s is a property path the vectorized engines cannot express "
            "as one KEY — property shape skipped; use engine=\"pyshacl\" for coverage",
            shape_uri)
        skipped.add(((*shapes, str(shape_uri)), f"{shape_uri}: unsupported sh:path form", None))
        return []
    meta = _shape_meta(graph, SH, shape_uri, target_on, path, inverse,
                       target_by=target_by, via_type=via_type, shapes=shapes)
    if parent is not None:
        meta["name"] = meta["name"] or parent["name"]
        meta["description"] = meta["description"] or parent["description"]
    walk = {"target_on": target_on, "visited": visited, "target_by": target_by, "skipped": skipped,
            "shapes": meta["shapes"], "judged": judged}
    rows = []

    # each value of a parameter is a constraint of its own (SHACL §4.1)
    for term, component, transform in _components(SH):
        for value in sorted(graph.objects(shape_uri, term), key=str):
            if path is None:
                # on a node shape it judges the focus node itself, which no engine reads
                skipped.add((meta["shapes"], f"{shape_uri}: node-level {component} not evaluated", None))
            else:
                rows.append({**meta, "component": component, "params": transform(graph, value)})
    # components no engine implements stay visible as rows: the run metadata
    # names them, and a logical operator over such a member is not evaluated
    for name in _UNKNOWN_TERMS:
        if graph.value(shape_uri, SH[name]) is not None:
            rows.append({**meta, "component": f"sh:{name}", "params": None})

    rows.extend(_sparql_rows(graph, SH, shape_uri, meta))
    rows.extend(_logical_rows(graph, SH, shape_uri, meta,
                              value_path=(path, inverse, via_type) if path else None, **walk))

    for node_shape in sorted(graph.objects(shape_uri, SH.node), key=str):
        if str(node_shape) in visited:
            logger.warning("sh:node cycle at %s — constraint dropped (%s)", node_shape, shape_uri)
            continue
        # the referenced shape expanded at compile time; the engines run its
        # rows against the value nodes (the focus node on a node shape)
        target = _shape_rows(graph, SH, node_shape, **{**walk, "judged": True})
        under = _under(skipped, (*meta["shapes"], str(node_shape)))
        if not target and under:
            # a target with nothing left but skipped parts would conform on every
            # node: the row is not evaluated, as a logical operator over a skipped
            # part (decided at compile time); pyshacl drops it too when nothing is
            # left for it either (only malformed parts)
            reason = f"{shape_uri}: sh:node target {node_shape} not evaluated"
            drop = (shape_uri, SH.node, node_shape) if all(entry[2] is not None for entry in under) else None
            _warn_skipped(reason, drop)
            skipped.add((meta["shapes"], reason, drop))
            continue
        rows.append({**meta, "component": "sh:node",
                     "params": {"shape": split_iri(node_shape)[1], "rows": target}})

    # the shape's own property shapes (and sh:closed over them): a NodeShape —
    # targeted, referenced or a logical member — judges its focus with them; a
    # shape with a path runs them on its value nodes (see *judged*)
    own = _closed_rows(graph, SH, shape_uri, meta)
    for property_shape in graph.objects(shape_uri, SH.property):
        own.extend(_shape_rows(graph, SH, property_shape, parent=meta, **walk))
    if path is None:
        rows.extend(own)
    elif own:
        rows.append({**meta, "component": "sh:node" if judged else "sh:property",
                     "params": {"shape": split_iri(shape_uri)[1], "rows": own}})
    return rows


def _closed_rows(graph, SH, shape, meta):
    """sh:closed — params = the complete allowed list, resolved at compile time:
    every sh:ignoredProperties list (pyshacl unions them) + every (non-inverse)
    sh:path value of THIS shape's sh:property shapes: a property shape skipped
    for two sh:path values keeps both declared."""
    closed = graph.value(shape, SH.closed)
    if closed is None or closed.toPython() is not True:
        return []
    allowed = [key for ignored in graph.objects(shape, SH.ignoredProperties) for key in _rdf_list(graph, ignored, local_key)]
    for property_shape in graph.objects(shape, SH.property):
        for path_node in graph.objects(property_shape, SH.path):
            path, inverse, _ = _resolve_path(graph, SH, path_node)
            if path is not None and not inverse:
                allowed.append(path)
    return [{**meta, "component": "sh:closed", "params": allowed}]


def _logical_rows(graph, SH, shape, meta, value_path=None, **walk):
    """sh:or / sh:and / sh:xone / sh:not: params are nested row-dict lists, recursion via
    RDF lists. A member the IR cannot express (no shape triples at all, a
    component no engine implements anywhere in it, or a part of it already
    skipped) makes the operator undecidable: the row is dropped and named in
    *skipped* — never judged on a partial member, which would fire on every
    focus node. Decided at compile time, whatever is switched off later. A
    shape with an empty member list never gets here (_malformed)."""
    rows = []
    for term, component in ((SH["or"], "sh:or"), (SH["and"], "sh:and"), (SH.xone, "sh:xone"),
                            (SH["not"], "sh:not")):
        for head in sorted(graph.objects(shape, term), key=str):   # one operator row per value
            items = [head] if component == "sh:not" else _rdf_list(graph, head, lambda node: node)
            members = [(item, _member_rows(graph, SH, item, meta, value_path, **walk)) for item in items]
            reasons = [f"{shape}: {component} member {item} {reason}"
                       for item, member in members
                       if (reason := _inexpressible(graph, item, member, (*meta["shapes"], str(item)), walk["skipped"]))]
            if reasons:
                # a malformed part in any member: pyshacl's graph loses the operator too
                malformed = any(entry[2] is not None for item, _ in members
                                for entry in _under(walk["skipped"], (*meta["shapes"], str(item))))
                drop = (shape, term, head) if malformed else None
                for reason in reasons:
                    _warn_skipped(reason, drop)
                walk["skipped"].update((meta["shapes"], reason, drop) for reason in reasons)
                continue
            nested = [member for _, member in members]
            rows.append({**meta, "component": component, "params": nested[0] if component == "sh:not" else nested})
    return rows


def _member_rows(graph, SH, item, meta, value_path, **walk):
    """A logical member's rows. Under a path, a member with its own sh:path is a
    property shape over the value nodes: its rows run as a sh:node row of the
    parent path, like a NodeShape member with sh:property does (one result per
    value node, reported by the operator)."""
    walk = {**walk, "judged": True}
    if value_path is None or graph.value(item, SH.path) is None:
        return _shape_rows(graph, SH, item, value_path=value_path, **walk)
    rows = _shape_rows(graph, SH, item, **walk)
    return [{**meta, "component": "sh:node", "params": {"shape": split_iri(item)[1], "rows": rows}}] if rows else []


def _inexpressible(graph, item, rows, lineage, skipped):
    """Why a logical member cannot be judged: it has no shape triples (a bare
    IRI in the list), a component no engine implements sits anywhere in it, or
    a part of it was already skipped (an entry under its *lineage*)."""
    if not any(True for _ in graph.predicate_objects(item)):
        return "has no shape — not evaluated"
    unknown = sorted(_components_in(rows) - KNOWN_COMPONENTS)
    if unknown:
        return f"uses {', '.join(unknown)} — not evaluated"
    if _under(skipped, lineage):
        return "holds a part that was not evaluated"
    return None


def _under(skipped, lineage):
    """*skipped*'s entries at or below the shape *lineage*."""
    return [entry for entry in skipped if entry[0][:len(lineage)] == lineage]


def _warn_skipped(reason, drop):
    logger.warning("%s%s", reason, "" if drop else '; use engine="pyshacl" for coverage')


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
        message = graph.value(sparql, SH.message)
        row = {**meta, "component": "sh:sparql",
               "params": {"select": str(select), "prefixes": _prefixes(graph, SH, sparql),
                          # $PATH substitution needs the full IRI; only direct paths qualify
                          "path": str(path) if type(path).__name__ == "URIRef" else None}}
        if message is not None:
            row["message"] = str(message)
        rows.append(row)
    return rows


def _prefixes(graph, SH, node):
    """The ``PREFIX`` header of a SPARQL-based *node* (a sh:sparql constraint or a
    SPARQL target): the sh:declare entries of its sh:prefixes, sorted by prefix —
    the declarations are blank nodes, and the header must not change between
    compiles of the same files."""
    return "".join(sorted({
        f"PREFIX {graph.value(declaration, SH.prefix)}: <{graph.value(declaration, SH.namespace)}>\n"
        for ontology in graph.objects(node, SH.prefixes)
        for declaration in graph.objects(ontology, SH.declare)}))


def _shape_meta(graph, SH, shape_uri, target_on, path, inverse, target_by="Type",
                via_type=False, shapes=()):
    severity = graph.value(shape_uri, SH.severity)
    message = graph.value(shape_uri, SH.message)
    name = graph.value(shape_uri, SH.name)
    description = graph.value(shape_uri, SH.description)
    return {
        "shape_id": str(shape_uri),
        # lineage: the enclosing shapes, outermost first, then this one
        "shapes": (*shapes, str(shape_uri)),
        # target_on names what the shape applies to, read by target_by: a Type
        # name, a KEY (targetSubjectsOf / targetObjectsOf), an ID (targetNode)
        # or a SPARQL target's query text
        "target_on": target_on,
        "target_by": target_by,
        "path": path,
        "inverse": inverse,
        # ( assoc rdf:type ) sequence path: the constraint's value nodes are
        # the types of the referenced objects, not the association values
        "via_type": via_type,
        "severity": split_iri(severity)[1] if severity is not None else "Violation",
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
        return local_key(path_node), False, False
    inverse = graph.value(path_node, SH.inversePath)
    if inverse is not None:
        return local_key(inverse), True, False
    alternative = graph.value(path_node, SH.alternativePath)
    if alternative is not None:
        for item in _rdf_list(graph, alternative, lambda node: node):
            nested_inverse = graph.value(item, SH.inversePath)
            if nested_inverse is not None:
                return local_key(nested_inverse), True, False
    if graph.value(path_node, rdflib.RDF.first) is not None:   # sequence path
        steps = _rdf_list(graph, path_node, lambda node: node)
        if (len(steps) == 2 and isinstance(steps[0], rdflib.URIRef)
                and steps[1] == rdflib.RDF.type):
            return local_key(steps[0]), False, True
    return None, False, False


# SHACL term → (component short name, params transform). Short names match the
# VIOLATION_TYPE values in shacl_report._COMPONENT_MAP so IR rows, engine
# output and pyshacl output all speak the same vocabulary.
def _components(SH):
    return [
        (SH.minCount, "sh:minCount", lambda g, v: int(v)),
        (SH.maxCount, "sh:maxCount", lambda g, v: int(v)),
        (SH.datatype, "sh:datatype", lambda g, v: f"xsd:{split_iri(v)[1]}"),
        (SH["class"], "sh:class", lambda g, v: local_value(v, "class")),
        (SH.minInclusive, "sh:minInclusive", lambda g, v: float(v)),
        (SH.maxInclusive, "sh:maxInclusive", lambda g, v: float(v)),
        (SH.minExclusive, "sh:minExclusive", lambda g, v: float(v)),
        (SH.maxExclusive, "sh:maxExclusive", lambda g, v: float(v)),
        (SH.pattern, "sh:pattern", lambda g, v: str(v)),
        (SH.minLength, "sh:minLength", lambda g, v: int(v)),
        (SH.maxLength, "sh:maxLength", lambda g, v: int(v)),
        (SH.nodeKind, "sh:nodeKind", lambda g, v: split_iri(v)[1]),
        (SH.hasValue, "sh:hasValue", lambda g, v: _value(v)),
        (SH["in"], "sh:in", lambda g, v: _rdf_list(g, v, _value)),
        (SH.equals, "sh:equals", lambda g, v: local_key(v)),
        (SH.disjoint, "sh:disjoint", lambda g, v: local_key(v)),
        (SH.lessThan, "sh:lessThan", lambda g, v: local_key(v)),
        (SH.lessThanOrEquals, "sh:lessThanOrEquals", lambda g, v: local_key(v)),
    ]


def _malformed(graph, SH, shape):
    """Why *shape* is malformed, or None — these load checks, each one pyshacl
    refuses the whole shapes graph for (ConstraintLoadError / ShapeLoadError):
    a parameter taken once (_SINGLE) given twice, an empty sh:or / sh:and /
    sh:xone list, a non-literal sh:deactivated, sh:ignoredProperties without
    sh:closed, a declared sh:PropertyShape without sh:path. The compile skips
    such a shape."""
    import rdflib

    for name, kind in _SINGLE.items():
        if len(set(graph.objects(shape, SH[name]))) > 1:
            return f"two sh:{name} {kind}"
    for name in ("or", "and", "xone"):
        if any(not _rdf_list(graph, head, lambda node: node) for head in graph.objects(shape, SH[name])):
            return f"empty sh:{name} list"
    flag = graph.value(shape, SH.deactivated)
    if flag is not None and not isinstance(flag, rdflib.Literal):
        return f"the sh:deactivated value {flag} is not a literal"
    if graph.value(shape, SH.ignoredProperties) is not None and graph.value(shape, SH.closed) is None:
        return "sh:ignoredProperties without sh:closed"
    if (shape, rdflib.RDF.type, SH.PropertyShape) in graph and graph.value(shape, SH.path) is None:
        return "sh:PropertyShape without sh:path"
    return None


# parameters a shape declares at most once (pyshacl refuses a second), with what one
# value is called in the skip reason — every other parameter is one constraint per value
_SINGLE = {**dict.fromkeys(("path", "minCount", "maxCount", "datatype", "nodeKind", "minLength", "maxLength", "closed",
                            "deactivated", "uniqueLang", "qualifiedMinCount", "qualifiedMaxCount"), "values"),
           "in": "lists", "languageIn": "lists"}


# SHACL terms no engine implements — kept as rows (params None) so the run
# metadata names them; pyshacl covers them
_UNKNOWN_TERMS = ("qualifiedValueShape", "qualifiedMinCount", "qualifiedMaxCount", "uniqueLang", "languageIn")


KNOWN_COMPONENTS = {
    "sh:minCount", "sh:maxCount", "sh:datatype", "sh:class", "sh:minInclusive",
    "sh:maxInclusive", "sh:minExclusive", "sh:maxExclusive", "sh:pattern",
    "sh:minLength", "sh:maxLength", "sh:nodeKind", "sh:hasValue", "sh:in",
    "sh:equals", "sh:disjoint", "sh:lessThan", "sh:lessThanOrEquals", "sh:node",
    "sh:closed", "sh:sparql", "sh:or", "sh:and", "sh:xone", "sh:not",
    "sh:property",      # a property shape's own property shapes, reported on its value nodes
    "triplets:range",   # schema references: the target's ANY type in the allowed
                        # set (RDF types are cumulative — issue #100); a target
                        # without a Type row is left to triplets:missing
    "triplets:missing",  # schema references: the target has no Type row in the data
}


def _rdf_list(graph, head, transform):
    from rdflib.collection import Collection
    return [transform(item) for item in Collection(graph, head)]


def _value(term):
    """sh:in / sh:hasValue member → comparable VALUE string: literals verbatim, IRIs by the
    VALUE rule (``local_value`` — an enum ``#`` IRI → its name, a ``/`` IRI stays whole)."""
    if type(term).__name__ == "Literal":
        return str(term)
    return local_value(str(term))
