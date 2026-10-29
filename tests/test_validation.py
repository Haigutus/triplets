"""Tests for SHACL validation (triplets.validation): pyshacl reference engine,
compile-once IR, and the lexical-form datatype deviation."""
import os

import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

import pandas
import triplets

from pathlib import Path

from _parity import SVEDALA_DIR, SKIP_REASON

SVEDALA_EQ = str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml")

# Inline shape (written to tmp) — deterministic, no committed shape files / external repo.
INLINE_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
                  sh:message "ACLineSegment must have a name" ] ;
    sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ;
                  sh:message "Conductor.length must be xsd:float" ] .
"""

# Real CGMES SHACL shapes from the test_data/CGMES-AP submodule (skip if not checked out).
CGMES_SHACL_DIR = Path(os.environ.get(
    "TRIPLETS_CGMES_SHACL",
    "test_data/CGMES-AP/CGMES/SHACL"))
CGMES_EQ_SHACL = CGMES_SHACL_DIR / "61970-301_Equipment-AP-Con-Complex-SHACL.ttl"


@pytest.fixture(scope="module")
def svedala_eq():
    if not Path(SVEDALA_EQ).exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF([SVEDALA_EQ])


@pytest.fixture(scope="module")
def shape_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("shapes") / "inline.ttl"
    path.write_text(INLINE_SHAPE)
    return str(path)


def test_typed_data_conforms(svedala_eq, shape_file):
    """With rdf_map, Conductor.length is xsd:float → datatype constraint passes."""
    from triplets.export_schema import schemas
    violations = svedala_eq.shacl.validate_shacl(shape_file, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope="*",
                                           engine="reference", lexical=False)
    assert isinstance(violations, pandas.DataFrame)
    assert len(violations) == 0


def test_untyped_data_trips_datatype(svedala_eq, shape_file):
    """Without rdf_map, Conductor.length is a plain string → xsd:float violations."""
    violations = svedala_eq.shacl.validate_shacl(shape_file, engine="reference", lexical=False)
    assert len(violations) > 0
    assert (violations["VIOLATION_TYPE"] == "sh:datatype").all()


def test_violations_columns(svedala_eq, shape_file):
    from triplets.validation.shacl_report import VIOLATION_COLUMNS
    violations = svedala_eq.shacl.validate_shacl(shape_file)
    # canonical schema + the columns validate_shacl() stamps
    assert list(violations.columns) == VIOLATION_COLUMNS + ["EXPECTED", "MESSAGE_SOURCE"]
    # focusNode stripped to bare UUID (no urn:uuid:)
    assert not violations["ID"].str.startswith("urn:uuid:").any()


def test_scope_excludes_out_of_scope_instances(svedala_eq, shape_file):
    """Scoping to an instance without ACLineSegments yields no violations."""
    instance = str(svedala_eq["INSTANCE_ID"].astype(str).iloc[0])
    in_scope = svedala_eq.shacl.validate_shacl(shape_file, engine="reference", data_scope=[instance])
    assert len(in_scope) > 0  # the EQ instance has the ACLineSegments
    out_scope = svedala_eq.shacl.validate_shacl(shape_file, engine="reference", data_scope=["00000000-0000-0000-0000-000000000000"])
    assert len(out_scope) == 0


@pytest.mark.parametrize("flavor", ["pandas", "duckdb"])
def test_pyshacl_scope_equals_prefiltered_and_loads_only_the_scope(shape_file, monkeypatch, flavor):
    """data_scope=["a"] reports what the pre-filtered frame reports (a's
    nameless x3), and only instance a's rows reach the rdflib load — b's
    nameless x2 and its string length on x1 stay out of the data graph."""
    from triplets.validation import shacl_pyshacl
    rows = [("x1", "Type", "ACLineSegment", "a"), ("x1", "IdentifiedObject.name", "L1", "a"),
            ("x3", "Type", "ACLineSegment", "a"),
            ("x2", "Type", "ACLineSegment", "b"), ("x1", "Conductor.length", "long", "b")]
    frame = pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    data = frame
    if flavor == "duckdb":   # the scope is filtered SQL-side
        data = pytest.importorskip("duckdb").connect()
        data.register("_rows", frame)
        data.execute("CREATE TABLE triplets AS SELECT * FROM _rows")
    loaded = []
    load_dataset = shacl_pyshacl.load_dataset
    monkeypatch.setattr(shacl_pyshacl, "load_dataset",
                        lambda rows, **kwargs: loaded.append(set(rows["INSTANCE_ID"])) or load_dataset(rows, **kwargs))

    key = ["ID", "KEY", "VALUE", "VIOLATION_TYPE", "SOURCE_SHAPE", "SEVERITY"]
    scoped = triplets.validation.validate_shacl(data, shape_file, engine="pyshacl", data_scope=["a"])
    prefiltered = triplets.validation.validate_shacl(frame[frame["INSTANCE_ID"] == "a"], shape_file, engine="pyshacl")
    assert loaded == [{"a"}, {"a"}]
    assert list(scoped["ID"]) == ["x3"]
    assert scoped[key].sort_values(key).reset_index(drop=True).equals(
        prefiltered[key].sort_values(key).reset_index(drop=True))
    assert len(triplets.validation.validate_shacl(frame, shape_file, engine="pyshacl")) > 0   # b's rows do trip


# ── lexical-form datatype deviation (documented divergence from pyshacl) ──────
@pytest.fixture(scope="module")
def lexical_data():
    """Hand-built triplets: one canonical float, one integer-form float, one broken value."""
    rows = [
        ("a1", "Type", "ACLineSegment", "eq"), ("a1", "IdentifiedObject.name", "L1", "eq"),
        ("a1", "Conductor.length", "10.5", "eq"),
        ("a2", "Type", "ACLineSegment", "eq"), ("a2", "IdentifiedObject.name", "L2", "eq"),
        ("a2", "Conductor.length", "1", "eq"),
        ("a3", "Type", "ACLineSegment", "eq"), ("a3", "IdentifiedObject.name", "L3", "eq"),
        ("a3", "Conductor.length", "abc", "eq"),
    ]
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def test_lexical_two_levels(lexical_data, shape_file):
    """"abc" → sh:datatype Violation; "1" under xsd:float → triplets:lexicalForm Warning."""
    violations = triplets.validation.validate_shacl(lexical_data, shape_file, engine="pandas")
    by_value = violations.set_index("VALUE")
    assert by_value.loc["abc", "VIOLATION_TYPE"] == "sh:datatype"
    assert by_value.loc["abc", "SEVERITY"] == "Violation"
    assert by_value.loc["1", "VIOLATION_TYPE"] == "triplets:lexicalForm"
    assert by_value.loc["1", "SEVERITY"] == "Warning"
    assert "10.5" not in by_value.index


def test_lexical_supplements_pyshacl(lexical_data, shape_file):
    """Default validate_shacl(): pyshacl report + lexical findings, no duplicate rows."""
    from triplets.export_schema import schemas
    violations = triplets.validation.validate_shacl(lexical_data, shape_file,
                                              rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope="*")
    # pyshacl accepts both "1" and "abc"... "abc"^^xsd:float is ill-typed → sh:datatype;
    # "1" passes pyshacl but the lexical check reports it as a Warning.
    assert (violations.loc[violations["VALUE"] == "1", "VIOLATION_TYPE"] == "triplets:lexicalForm").all()
    assert len(violations.loc[violations["VALUE"] == "1"]) == 1
    assert (violations.loc[violations["VALUE"] == "abc", "VIOLATION_TYPE"] == "sh:datatype").all()
    duplicated = violations.duplicated(subset=["ID", "KEY", "VALUE", "VIOLATION_TYPE"])
    assert not duplicated.any()


@pytest.mark.parametrize("engine", ["pandas", "polars", "duckdb", "pyshacl"])
def test_lexical_false_drops_lexical_form(lexical_data, shape_file, engine):
    """lexical=False: no triplets:lexicalForm rows, every other finding unchanged."""
    if engine in ("polars", "duckdb"):
        pytest.importorskip(engine)
    from triplets.export_schema import schemas

    def findings(**kwargs):
        violations = triplets.validation.validate_shacl(lexical_data, shape_file, engine=engine,
                                                        rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
                                                        rdf_map_scope="*", **kwargs)
        columns = ["ID", "KEY", "VALUE", "VIOLATION_TYPE", "SOURCE_SHAPE", "SEVERITY"]
        return set(violations[columns].itertuples(index=False, name=None))

    default, pure = findings(), findings(lexical=False)
    lexical = {row for row in default if row[3] == "triplets:lexicalForm"}
    assert {(row[2], row[5]) for row in lexical} == {("1", "Warning")}
    assert pure == default - lexical
    assert {row[2] for row in pure if row[3] == "sh:datatype"} == {"abc"}


NESTED_DATATYPE_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:NestedLengthShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:and ( [ sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ] ] ) .
"""


@pytest.mark.parametrize("engine", ["pandas", "polars", "duckdb"])
def test_lexical_false_nested(lexical_data, tmp_path, engine):
    """lexical=False reaches the nested datatype checks (the pandas fallback of
    polars/duckdb): only the segment holding the invalid value fails the sh:and."""
    if engine != "pandas":
        pytest.importorskip(engine)
    path = tmp_path / "nested.ttl"
    path.write_text(NESTED_DATATYPE_SHAPE)
    violations = triplets.validation.validate_shacl(lexical_data, str(path), engine=engine, lexical=False)
    assert set(zip(violations["ID"], violations["VIOLATION_TYPE"])) == {("a3", "sh:and")}


def test_lexical_polars_input(lexical_data, shape_file):
    polars = pytest.importorskip("polars")
    violations = triplets.validation.validate_shacl(polars.from_pandas(lexical_data), shape_file,
                                              engine="pandas")
    assert (violations["VALUE"] == "1").any()


@pytest.mark.performance
@pytest.mark.skipif(not os.environ.get("TRIPLETS_SLOW_TESTS"),
                    reason="pyshacl on the full complex CGMES SHACL takes >10 min — set TRIPLETS_SLOW_TESTS=1")
@pytest.mark.skipif(not CGMES_EQ_SHACL.exists(),
                    reason="external CGMES SHACL shapes not available")
def test_real_cgmes_eq_shapes(svedala_eq):
    """Validate Svedala EQ against the real CGMES Equipment SHACL profile."""
    from triplets.export_schema import schemas
    from triplets.validation.shacl_report import VIOLATION_COLUMNS
    violations = svedala_eq.shacl.validate_shacl(str(CGMES_EQ_SHACL), rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope="*")
    assert list(violations.columns) == [*VIOLATION_COLUMNS, "EXPECTED", "MESSAGE_SOURCE"]
