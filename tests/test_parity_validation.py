"""Cross-engine parity for SHACL validation.

pyshacl is the reference. Engines must agree 1:1 on the canonical violations
schema, modulo the one documented deviation: the lexical-form datatype check
(`triplets:lexicalForm` Warnings are EXTRA rows the vectorized engines add;
they must never lose a violation pyshacl reports). Grows a column per engine
as pandas (full) / polars / duckdb land.
"""
import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

import pandas
import triplets

from pathlib import Path

from _parity import SVEDALA_DIR, SKIP_REASON

SVEDALA_EQ = str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml")

DATATYPE_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ] .
"""


@pytest.fixture(scope="module")
def shape_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("shapes") / "datatype.ttl"
    path.write_text(DATATYPE_SHAPE)
    return str(path)


@pytest.fixture(scope="module")
def mixed_data():
    """Canonical float, integer-form float, and a broken value per object."""
    rows = [
        ("a1", "Type", "ACLineSegment", "eq"), ("a1", "Conductor.length", "10.5", "eq"),
        ("a2", "Type", "ACLineSegment", "eq"), ("a2", "Conductor.length", "1", "eq"),
        ("a3", "Type", "ACLineSegment", "eq"), ("a3", "Conductor.length", "abc", "eq"),
        ("a4", "Type", "ACLineSegment", "eq"), ("a4", "Conductor.length", "-2.5e3", "eq"),
    ]
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def _violation_set(violations):
    """Comparable set of Violation-severity datatype findings."""
    rows = violations[violations["VIOLATION_TYPE"] == "sh:datatype"]
    return set(zip(rows["ID"], rows["KEY"], rows["VALUE"]))


def test_datatype_parity_pyshacl_vs_pandas(mixed_data, shape_file):
    """Same sh:datatype violations; lexicalForm Warnings are documented extras."""
    from triplets.export_schema import schemas

    reference = triplets.validation.validate(mixed_data, shape_file, engine="pyshacl",
                                             rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
                                             lexical=False)
    ours = triplets.validation.validate(mixed_data, shape_file, engine="pandas")

    assert _violation_set(reference) == _violation_set(ours) == {("a3", "Conductor.length", "abc")}

    extras = ours[ours["VIOLATION_TYPE"] == "triplets:lexicalForm"]
    assert set(extras["VALUE"]) == {"1"}
    assert (extras["SEVERITY"] == "Warning").all()


def test_datatype_parity_on_real_data(shape_file):
    """Svedala EQ: typed pyshacl run and the pandas engine agree on sh:datatype."""
    if not Path(SVEDALA_EQ).exists():
        pytest.skip(SKIP_REASON)
    from triplets.export_schema import schemas

    data = pandas.read_RDF([SVEDALA_EQ])
    reference = triplets.validation.validate(data, shape_file, engine="pyshacl",
                                             rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
                                             lexical=False)
    ours = triplets.validation.validate(data, shape_file, engine="pandas")
    assert _violation_set(reference) == _violation_set(ours)


# ── component parity matrix: pandas engine vs pyshacl, per constraint ─────────
# Real CIM keys (so rdf_map types the literals) and UUID ids (so references
# export as urn:uuid IRIs). Expected = violating focus IDs; both engines must
# report exactly these under the same VIOLATION_TYPE.
def _uuid(n):
    return f"{n:08d}-0000-0000-0000-000000000000"


A1, A2, VL, SUB = _uuid(1), _uuid(2), _uuid(71), _uuid(72)

_SEGMENT_BASE = [(A1, "Type", "ACLineSegment", "eq"), (A2, "Type", "ACLineSegment", "eq")]

COMPONENT_CASES = {
    "sh:minCount": (
        "sh:path cim:IdentifiedObject.name ; sh:minCount 1",
        _SEGMENT_BASE + [(A1, "IdentifiedObject.name", "L1", "eq")],
        {A2}),
    "sh:maxCount": (
        "sh:path cim:IdentifiedObject.name ; sh:maxCount 1",
        _SEGMENT_BASE + [(A1, "IdentifiedObject.name", "L1", "eq"),
                         (A1, "IdentifiedObject.name", "L1b", "eq"),
                         (A2, "IdentifiedObject.name", "L2", "eq")],
        {A1}),
    "sh:pattern": (
        'sh:path cim:IdentifiedObject.name ; sh:pattern "^[A-Z]"',
        _SEGMENT_BASE + [(A1, "IdentifiedObject.name", "Good", "eq"),
                         (A2, "IdentifiedObject.name", "bad", "eq")],
        {A2}),
    "sh:minInclusive": (
        "sh:path cim:Conductor.length ; sh:minInclusive 0.0",
        _SEGMENT_BASE + [(A1, "Conductor.length", "-5.0", "eq"),
                         (A2, "Conductor.length", "3.0", "eq")],
        {A1}),
    "sh:in": (
        'sh:path cim:IdentifiedObject.name ; sh:in ( "L1" "L2" )',
        _SEGMENT_BASE + [(A1, "IdentifiedObject.name", "L1", "eq"),
                         (A2, "IdentifiedObject.name", "other", "eq")],
        {A2}),
    "sh:class": (
        "sh:path cim:Equipment.EquipmentContainer ; sh:class cim:VoltageLevel",
        _SEGMENT_BASE + [(VL, "Type", "VoltageLevel", "eq"), (SUB, "Type", "Substation", "eq"),
                         (A1, "Equipment.EquipmentContainer", VL, "eq"),
                         (A2, "Equipment.EquipmentContainer", SUB, "eq")],
        {A2}),
    "sh:sparql": (  # $PATH placeholder, like the real ENTSO-E constraint queries
        """sh:path cim:IdentifiedObject.name ;
           sh:sparql [ sh:select 'SELECT $this ?value WHERE { $this $PATH ?value . FILTER (str(?value) = "forbidden") }' ]""",
        _SEGMENT_BASE + [(A1, "IdentifiedObject.name", "fine", "eq"),
                         (A2, "IdentifiedObject.name", "forbidden", "eq")],
        {A2}),
}

_CASE_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ {body} ] .
"""


@pytest.mark.parametrize("component", sorted(COMPONENT_CASES))
def test_component_parity(component, tmp_path):
    from triplets.export_schema import schemas
    body, rows, expected = COMPONENT_CASES[component]

    shape = tmp_path / "case.ttl"
    shape.write_text(_CASE_SHAPE.format(body=body))
    data = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])

    reference = triplets.validation.validate(data, str(shape), engine="pyshacl",
                                             rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
                                             lexical=False)
    ours = triplets.validation.validate(data, str(shape), engine="pandas")

    assert set(reference.loc[reference["VIOLATION_TYPE"] == component, "ID"]) == expected, "pyshacl disagrees"
    assert set(ours.loc[ours["VIOLATION_TYPE"] == component, "ID"]) == expected, "pandas engine disagrees"


def test_node_parity(tmp_path):
    """sh:node: referenced node must conform to the referenced (target-less) shape."""
    from triplets.export_schema import schemas
    shape = tmp_path / "node.ttl"
    shape.write_text("""
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:node cim:VoltageLevelShape ] .

cim:VoltageLevelShape a sh:NodeShape ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
""")
    named_vl, bare_vl = _uuid(81), _uuid(82)
    data = pandas.DataFrame(
        _SEGMENT_BASE
        + [(named_vl, "Type", "VoltageLevel", "eq"), (named_vl, "IdentifiedObject.name", "VL", "eq"),
           (bare_vl, "Type", "VoltageLevel", "eq"),
           (A1, "Equipment.EquipmentContainer", named_vl, "eq"),
           (A2, "Equipment.EquipmentContainer", bare_vl, "eq")],
        columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])

    reference = triplets.validation.validate(data, str(shape), engine="pyshacl",
                                             rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
                                             lexical=False)
    ours = triplets.validation.validate(data, str(shape), engine="pandas")
    assert set(reference.loc[reference["VIOLATION_TYPE"] == "sh:node", "ID"]) == {A2}, "pyshacl disagrees"
    assert set(ours.loc[ours["VIOLATION_TYPE"] == "sh:node", "ID"]) == {A2}, "pandas engine disagrees"


def test_input_flavor_parity(mixed_data, shape_file):
    """pandas engine gives identical findings for pandas / polars / duckdb input."""
    reference = triplets.validation.validate(mixed_data, shape_file, engine="pandas")

    polars = pytest.importorskip("polars")
    from_polars = triplets.validation.validate(polars.from_pandas(mixed_data), shape_file,
                                               engine="pandas")
    assert _violation_set(from_polars) == _violation_set(reference)

    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.register("src", mixed_data)
    con.execute("CREATE TABLE triplets AS SELECT * FROM src")
    from_duckdb = con.shacl.validate(shape_file, engine="pandas")
    assert _violation_set(from_duckdb) == _violation_set(reference)
