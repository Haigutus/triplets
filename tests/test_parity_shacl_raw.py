"""Raw-pyshacl check of the shapes compiler. pyshacl validates the shapes Turtle as
rdflib parses it — no triplets.validation.compile, no switch-off, no report reader —
and its rows are compared with the polars engine (the matrix shows the engines agree,
all of them through the compiler; this leg is the one the compiler cannot touch). The
compile gets a copy of the same parse, never the graph pyshacl runs on (pyshacl,
advanced=True, may mutate it); the copy keeps the blank-node labels, so anonymous
SOURCE_SHAPEs compare exactly. The data leg is the N-Quads export (roundtrip-tested,
what the pyshacl engine loads too), parsed with rdflib's literal normalisation off:
the frames hold lexical forms.

Key: (ID, KEY, VALUE, component, SEVERITY, SOURCE_SHAPE); MESSAGE is the matrix's.
An ours-only row fails. A pyshacl-only row fails unless the run metadata declares it:
its sh:sourceShape is a skipped_shapes id (for a skipped component of the shape, only
that component's rows), or its component is a skipped_components entry.
Known divergences are strict xfails: the matrix XFAIL for polars, and UNLISTED.
Runtime deactivated_shapes are dropped: both sides run the graph's declared set.

Real data: each CGMES 3.0 Equipment-AP shape file on small relicapgrid EQ instances.
Not verified here: RegulatingControl-RegulatingEquipment and
ActivePowerLimit.normalValue-valueRange, which fire only on the Svedala-sized
instances, where raw pyshacl takes > 300 s per Complex shape file.
"""
import functools
import re
from pathlib import Path

import pytest

rdflib = pytest.importorskip("rdflib")
pyshacl = pytest.importorskip("pyshacl")
pytest.importorskip("polars")

import pandas
import triplets

from triplets.export import export_to_nquads
from triplets.export_schema import schemas
from triplets.iri import load_rdf_map
from triplets.validation.schema_scope import scoped_rdf_map

from _parity import LEXICAL_ALLOWANCE
from test_parity_shacl_matrix import B, CASES, CIM, PREFIX, SCHEMA, SKIPPED, TRIO, XFAIL, frame

SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
UUID = "urn:uuid:"

# the matrix's polars divergences; #185 is about MESSAGE, which this key leaves out
EXPECTED = {case: reason for (case, engine), reason in XFAIL.items()
            if engine == "polars" and case != "message_two_values"}
# a skip no run metadata names: sh:BlankNodeOrIRI is only debug-logged
UNLISTED = {"nodeKind_blank_or_iri": "#180 sh:nodeKind sh:BlankNodeOrIRI not evaluated and no skipped_shapes entry"}
EXPECTED |= UNLISTED
# skipped_shapes reasons that name one component of a shape otherwise evaluated
PART = re.compile(r"(?:node-level (sh:\w+)|(sh:node) target|(sh:(?:and|or|xone|not)) member)")

CGMES_3 = Path("test_data/CGMES-AP/CGMES")
RELICAPGRID = Path("test_data/relicapgrid/Instance")
# (shape files, EQ data files, rdf_map, CIM namespace, RDFS vocabulary) per CGMES version
REAL_DATA = [(sorted((CGMES_3 / "SHACL").glob("*_Equipment-AP*.ttl")),
              [RELICAPGRID / "Belgovia/Grid/cimxml/20220615T2230Z__Belgovia_EQ_1.xml",
               RELICAPGRID / "Britheim/Grid/cimxml/20220615T2230Z__Britheim_EQ_1.xml",
               RELICAPGRID / "DC-Nordheim-Galia/Grid/cimxml/20220615T2230Z__HVDC-Nordheim-Galia_EQ_1.xml"],
              schemas.ENTSOE_CGMES_3_0_0_552_ED1, CIM, CGMES_3 / "RDFS/61970-600-2_Equipment-AP-Voc-RDFS2020.rdf")]


@pytest.fixture(autouse=True)
def lexical_literals():
    """rdflib's default parse turns "30"^^xsd:float into "30.0", which a sh:sparql
    str() filter then sees. Process-global, so restored after; per test, because
    pyshacl.validate sets it back to True on return."""
    normalize, rdflib.NORMALIZE_LITERALS = rdflib.NORMALIZE_LITERALS, False
    yield
    rdflib.NORMALIZE_LITERALS = normalize


# ── reference leg: rdflib + pyshacl only ──────────────────────────────────────
def local(term):
    """An IRI as the frame holds it (urn:uuid: id or local name), a literal's lexical form."""
    if term is None:
        return None
    if isinstance(term, rdflib.Literal):
        return str(term)
    text = str(term)
    return text.removeprefix(UUID) if text.startswith(UUID) else text.rsplit("#", 1)[-1]


def stem(component):
    """sh:MinCountConstraintComponent / sh:minCount → mincount."""
    return str(component).rsplit("#", 1)[-1].split(":")[-1].removesuffix("ConstraintComponent").lower()


def clone(graph):
    """Same triples, prefixes and blank-node labels, a new graph object."""
    new = rdflib.Graph()
    for prefix, namespace in graph.namespaces():
        new.bind(prefix, namespace)
    new += graph
    return new


def nquads(data, rdf_map, scope, cim):
    scoped, _ = scoped_rdf_map(rdf_map, scope)
    buffer = export_to_nquads(data, rdf_map=scoped, export_to_memory=True, undefined_namespace=cim)
    buffer.seek(0)
    dataset = rdflib.Dataset(default_union=True)
    dataset.parse(source=buffer, format="nquads")
    return dataset


def subclass_edges(rdf_map):
    """rdfs:subClassOf from the rdf_map's inheritance chains (pyshacl walks them transitively)."""
    graph = rdflib.Graph()
    for section in rdf_map.values():
        for entry in section.values():
            chain = entry.get("inheritance", []) if isinstance(entry, dict) else []
            for child, parent in zip(chain, chain[1:]):
                graph.add((rdflib.URIRef(child), rdflib.RDFS.subClassOf, rdflib.URIRef(parent)))
    return graph


def pyshacl_rows(data, shapes, ontology):
    _, report, _ = pyshacl.validate(data, shacl_graph=shapes, ont_graph=ontology, advanced=True, inference="none")
    details = set(report.objects(None, SH.detail))   # nested causes: the engines report the outer result
    rows = set()
    for result in set(report.subjects(rdflib.RDF.type, SH.ValidationResult)) - details:
        focus, path, value, component, severity, shape = (report.value(result, SH[term]) for term in (
            "focusNode", "resultPath", "value", "sourceConstraintComponent", "resultSeverity", "sourceShape"))
        if isinstance(path, rdflib.BNode):   # inverse → its property, sequence → its first step: the engines' KEY
            path = shapes.value(path, SH.inversePath) or shapes.value(path, rdflib.RDF.first) or path
        if path is None and value == focus:   # a node-level result echoes the focus node
            value = None
        rows.add((local(focus), local(path), local(value), stem(component), local(severity), str(shape)))
    return rows


# ── our leg ───────────────────────────────────────────────────────────────────
def engine_rows(data, compiled, **kwargs):
    violations = triplets.validation.validate_shacl(data, compiled, engine="polars", lexical=False, **kwargs)
    found = violations[violations["VIOLATION_TYPE"] != LEXICAL_ALLOWANCE].astype(object)
    rows = {(local(row.ID), "type" if row.KEY == "Type" else row.KEY, row.VALUE, stem(row.VIOLATION_TYPE), row.SEVERITY,
             row.SOURCE_SHAPE) for row in found.where(found.notna(), None).itertuples(index=False)}
    return rows, violations.attrs["validation"]


def compare(reference, ours, meta):
    """(ours-only rows, pyshacl-only rows the run metadata does not declare). A
    skipped_shapes text is "{shape id}: {reason}": the whole shape, or the one
    component PART names; a skipped_components entry covers its component anywhere."""
    skipped = set()
    for text in meta["skipped_shapes"]:
        shape, reason = text.split(": ", 1)
        part = PART.match(reason)
        skipped.add((shape, stem(next(filter(None, part.groups()))) if part else None))
    components = {stem(name) for name in meta["skipped_components"]}
    declared = {row for row in reference - ours
                if {(row[5], None), (row[5], row[3])} & skipped or row[3] in components}
    return ours - reference, reference - ours - declared


def synthetic(shape, rows, kwargs):
    """(pyshacl rows, polars rows, run metadata) of one shape Turtle on matrix-style rows."""
    rdf_map = load_rdf_map(kwargs["rdf_map"])
    dataset = nquads(frame(rows), rdf_map, "*", CIM)
    data = (sum((dataset.get_context(rdflib.URIRef(UUID + instance)) for instance in kwargs["data_scope"]),
                rdflib.Graph()) if "data_scope" in kwargs else dataset)
    shapes = rdflib.Graph().parse(data=PREFIX + shape, format="turtle")
    compiled = triplets.validation.compile(clone(shapes))
    ontology = None if kwargs.get("inheritance") is False else subclass_edges(rdf_map)
    return pyshacl_rows(data, shapes, ontology), *engine_rows(frame(rows), compiled, **kwargs)


# ── synthetic: every matrix case ──────────────────────────────────────────────
def matrix_cases():
    return [pytest.param(case, id=case,
                         marks=[pytest.mark.xfail(strict=True, reason=EXPECTED[case])] if case in EXPECTED else [])
            for case in {**CASES, **SKIPPED} if case != "target_without_select"]


@pytest.mark.parametrize("case", matrix_cases())
def test_matrix_raw(case):
    shape, rows, *rest = {**CASES, **SKIPPED}[case]
    kwargs = {"rdf_map": SCHEMA, "rdf_map_scope": "*", **(rest[0] if rest and isinstance(rest[0], dict) else {})}
    kwargs.pop("deactivated_shapes", None)
    assert compare(*synthetic(shape, rows, kwargs)) == (set(), set())


def test_skip_excuses_only_its_component():
    """A skipped node-level sh:in leaves the shape's sh:closed rows to compare: with
    them missing on our side, they are pyshacl-only rows nothing declares."""
    shape = (f"{B} sh:in ( <urn:uuid:b1> ) ; sh:closed true ;"
             " sh:ignoredProperties ( rdf:type cim:IdentifiedObject.name ) .")
    reference, ours, meta = synthetic(shape, TRIO, {"rdf_map": SCHEMA, "rdf_map_scope": "*"})
    closed = {row for row in reference if row[3] == "closed"}
    assert closed and compare(reference, ours, meta) == (set(), set())
    assert compare(reference, ours - closed, meta) == (set(), closed)


def test_raw_pyshacl_rejects_target_without_select():
    shape, rows, _ = SKIPPED["target_without_select"]
    shapes = rdflib.Graph().parse(data=PREFIX + shape, format="turtle")
    with pytest.raises(pyshacl.errors.ShapeLoadError):
        pyshacl_rows(nquads(frame(rows), load_rdf_map(SCHEMA), "*", CIM), shapes, None)


def test_expected_entries_name_cases():
    assert set(EXPECTED) <= set(CASES) | set(SKIPPED)


# ── real data: one shape file x one EQ instance ───────────────────────────────
@functools.cache
def shape_file(path):
    return rdflib.Graph().parse(path, format="turtle")


@functools.cache
def eq_data(path, rdf_map, cim):
    data = pandas.read_RDF([str(path)])
    return data, nquads(data, load_rdf_map(rdf_map), ["EQ"], cim)


@functools.cache
def vocabulary(path, cim):
    graph = rdflib.Graph().parse(path, publicID=cim.rstrip("#"))   # rdf:about="#X" without xml:base
    edges = rdflib.Graph()
    for edge in graph.triples((None, rdflib.RDFS.subClassOf, None)):
        edges.add(edge)
    return edges


def real_data():
    return [pytest.param(shapes, data, rdf_map, cim, rdfs, id=f"{shapes.stem}-{data.stem}")
            for shape_files, data_files, rdf_map, cim, rdfs in REAL_DATA
            for shapes in shape_files for data in data_files]


@pytest.mark.parametrize("shapes_path, data_path, rdf_map, cim, rdfs", real_data())
def test_real_data_raw(shapes_path, data_path, rdf_map, cim, rdfs):
    if not data_path.exists():
        pytest.skip("relicapgrid test data not available (needs git submodule)")
    data, dataset = eq_data(data_path, rdf_map, cim)
    compiled = triplets.validation.compile(clone(shape_file(shapes_path)))
    shapes = clone(shape_file(shapes_path))
    reference = pyshacl_rows(dataset, shapes, vocabulary(rdfs, cim))
    ours = engine_rows(data, compiled, rdf_map=rdf_map, rdf_map_scope=["EQ"])
    assert compare(reference, *ours) == (set(), set())
