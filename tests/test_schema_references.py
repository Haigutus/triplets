"""Reference types (triplets.validation.validate_schema_references): each source
instance's associations against the referenced objects in the source and target
instances, with the valid classes from the source and target profiles."""
import pandas
import pytest

from triplets.validation import compile_references, enrich, validate_schema_references

ENGINES = ["pandas", "polars", "duckdb"]
CIM = "http://iec.ch/TC57/CIM100#"


@pytest.fixture(params=ENGINES)
def engine(request):
    if request.param != "pandas":
        pytest.importorskip(request.param)
    return request.param


def association(range_class):
    return {"type": "Association", "multiplicity": "0..1", "xsd:minOccours": "0", "xsd:maxOccours": "1",
            "range": CIM + range_class}


def cls(name, *parents, parameters=()):
    return {"type": "Class", "namespace": CIM, "inheritance": [CIM + name, *(CIM + p for p in parents)],
            "parameters": list(parameters)}


SCHEMA = {
    "EQ": {"Breaker": cls("Breaker", "Switch", "Equipment", parameters=["Equipment.EquipmentContainer"]),
           "Bay": cls("Bay", "EquipmentContainer"), "VoltageLevel": cls("VoltageLevel", "EquipmentContainer"),
           "Substation": cls("Substation"),
           "Equipment.EquipmentContainer": association("EquipmentContainer")},
    "SV": {"SvVoltage": cls("SvVoltage", parameters=["SvVoltage.TopologicalNode"]),
           "SvVoltage.TopologicalNode": association("TopologicalNode")},
    "TP": {"TopologicalNode": cls("TopologicalNode", "IdentifiedObject")},
    "OP": {"Breaker": cls("Breaker", "Switch", parameters=["Breaker.backup"]),
           "Breaker.backup": association("Switch"), "Switch": cls("Switch")},
}


def frame(rows):
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def breaker(object_id, container, instance="eq"):
    return [(object_id, "Type", "Breaker", instance), (object_id, "Equipment.EquipmentContainer", container, instance)]


CONTAINERS = [("bay1", "Type", "Bay", "eq"), ("sub1", "Type", "Substation", "eq"),
              ("sub1", "IdentifiedObject.name", "Main Sub", "eq")]


def violating(violations, violation_type):
    rows = violations[violations["VIOLATION_TYPE"] == violation_type]
    return set(zip(rows["ID"], rows["VALUE"]))


def test_range_accepts_subclasses(engine):
    """Bay is an EquipmentContainer, Substation is not; the TARGET says what was found."""
    rows = breaker("b1", "bay1") + breaker("b2", "sub1") + CONTAINERS
    v = validate_schema_references(frame(rows), SCHEMA, ["eq"], ["EQ"], engine=engine)
    assert violating(v, "rdfs:range") == {("b2", "sub1")} and violating(v, "triplets:missing") == set()
    v = enrich(v, data=frame(rows), rdf_map=SCHEMA)              # the schema tells a reference from an enum
    assert v["TARGET"].tolist() == ['referenced object sub1 found — Substation "Main Sub"']
    assert v["EXPECTED"].tolist() == ["a reference to one of: Bay, VoltageLevel"]
    assert v["PROFILE"].tolist() == [["EQ"]]


def test_missing_targets(engine):
    rows = breaker("b1", "ghost") + CONTAINERS
    v = validate_schema_references(frame(rows), SCHEMA, ["eq"], ["EQ"], engine=engine)
    assert violating(v, "triplets:missing") == {("b1", "ghost")}
    v = enrich(v, data=frame(rows))
    assert v["TARGET"].tolist() == ["referenced object ghost not found in the data"]
    quiet = validate_schema_references(frame(rows), SCHEMA, ["eq"], ["EQ"], report_missing_targets=False,
                                       engine=engine)
    assert len(quiet) == 0


def test_targets_are_looked_up_in_the_target_instances(engine):
    """An SV voltage references a TP node: the node's class comes from the TP
    profile, the node itself from the TP instance."""
    rows = [("v1", "Type", "SvVoltage", "sv"), ("v1", "SvVoltage.TopologicalNode", "n1", "sv"),
            ("v2", "Type", "SvVoltage", "sv"), ("v2", "SvVoltage.TopologicalNode", "bay1", "sv"),
            ("n1", "Type", "TopologicalNode", "tp")] + CONTAINERS
    alone = validate_schema_references(frame(rows), SCHEMA, ["sv"], ["SV"], engine=engine)
    assert len(alone) == 0                         # the range class is defined by no given profile
    assert any("SvVoltage.TopologicalNode" in entry for entry in alone.attrs["validation"]["skipped_shapes"])
    v = validate_schema_references(frame(rows), SCHEMA, ["sv"], ["SV"], ["tp", "eq"], ["TP"], engine=engine)
    assert violating(v, "rdfs:range") == {("v2", "bay1")}
    without_targets = validate_schema_references(frame(rows), SCHEMA, ["sv"], ["SV"], [], ["TP"], engine=engine)
    assert violating(without_targets, "triplets:missing") == {("v1", "n1"), ("v2", "bay1")}


def test_findings_are_on_source_rows_only(engine):
    """EQ and SSH share object IDs: a bad reference stored only in the target file
    is not the source instance's finding."""
    rows = breaker("b1", "bay1") + CONTAINERS + breaker("b1", "sub1", instance="ssh")
    assert len(validate_schema_references(frame(rows), SCHEMA, ["eq"], ["EQ"], ["ssh"], engine=engine)) == 0
    v = validate_schema_references(frame(rows), SCHEMA, ["ssh"], ["EQ"], ["eq"], engine=engine)
    assert violating(v, "rdfs:range") == {("b1", "sub1")}


def test_source_profiles_are_merged(engine):
    """EQ + OP: both profiles' associations of Breaker are checked."""
    rows = breaker("b1", "sub1") + [("b1", "Breaker.backup", "bay1", "eq")] + CONTAINERS
    v = validate_schema_references(frame(rows), SCHEMA, ["eq"], ["EQ", "OP"], engine=engine)
    assert set(zip(v["KEY"], v["VALUE"])) == {("Equipment.EquipmentContainer", "sub1"), ("Breaker.backup", "bay1")}
    assert {tuple(profile) for profile in v["PROFILE"]} == {("EQ", "OP")}


def test_multi_typed_target_conforms(engine):
    """SSH/TP re-type EQ objects (issue #100): ANY of the target's types in the range set conforms."""
    rows = breaker("b1", "x1") + [("x1", "Type", "Substation", "eq"), ("x1", "Type", "Bay", "eq")]
    assert len(validate_schema_references(frame(rows), SCHEMA, ["eq"], ["EQ"], engine=engine)) == 0


def test_without_inheritance(engine):
    """A concrete range (Switch) accepts Switch only; an abstract one is skipped."""
    rows = breaker("b1", "bay1") + [("b1", "Breaker.backup", "b2", "eq")] + breaker("b2", "bay1") + CONTAINERS
    v = validate_schema_references(frame(rows), SCHEMA, ["eq"], ["EQ", "OP"], inheritance=False, engine=engine)
    assert violating(v, "rdfs:range") == {("b1", "b2")}
    assert any("EquipmentContainer" in entry for entry in v.attrs["validation"]["skipped_shapes"])
    assert v.attrs["validation"]["inheritance"] is False


def test_compiled_references_cache():
    compiled = compile_references(SCHEMA, ["SV"], ["TP"])
    assert compiled is compile_references(SCHEMA, ["SV"], ["TP"])
    assert compiled is not compile_references(SCHEMA, ["SV"], ["TP"], report_missing_targets=False)
    assert set(compiled.ir["component"]) == {"triplets:range", "triplets:missing"}
    assert compiled.ir["params"].iloc[0] == ["TopologicalNode"]


def test_accessor():
    v = frame(breaker("b1", "sub1") + CONTAINERS).shacl.validate_schema_references(SCHEMA, ["eq"], ["EQ"])
    assert violating(v, "rdfs:range") == {("b1", "sub1")}
