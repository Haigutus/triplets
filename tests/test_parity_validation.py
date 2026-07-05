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
