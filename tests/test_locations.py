"""Direct tests for the source-location pass (validation/locations.py) —
previously covered only indirectly through the SARIF export."""
import io
import zipfile

import pandas
import pytest

import triplets  # noqa: F401 — registers the shacl accessor namespace
from triplets.validation import LOCATION_COLUMNS, locate_violations
from triplets.validation.locations import locate

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://iec.ch/TC57/CIM100#">
  <cim:ACLineSegment rdf:ID="_aaaa">
    <cim:IdentifiedObject.name>L1</cim:IdentifiedObject.name>
    <cim:Conductor.length>12.5</cim:Conductor.length>
  </cim:ACLineSegment>
  <cim:Breaker rdf:about="urn:uuid:bbbb">
    <cim:IdentifiedObject.name>B1</cim:IdentifiedObject.name>
  </cim:Breaker>
</rdf:RDF>
"""


@pytest.fixture
def xml_path(tmp_path):
    path = tmp_path / "grid.xml"
    path.write_bytes(XML)
    return str(path)


def test_definition_and_key_positions(xml_path):
    located = locate({"aaaa": {"Conductor.length"}, "bbbb": set()}, [xml_path])
    line = located["aaaa"]
    assert (line["uri"], line["startLine"], line["startColumn"]) == (xml_path, 3, 3)
    assert line["keyLines"]["Conductor.length"] == (5, 5)
    assert located["bbbb"]["startLine"] == 7


def test_missing_key_and_missing_object(xml_path):
    located = locate({"aaaa": {"NotThere.key"}, "cccc": set()}, [xml_path])
    assert located["aaaa"]["keyLines"] == {}          # key falls back to the definition
    assert "cccc" not in located                      # object absent from the sources


def test_first_definition_wins(tmp_path, xml_path):
    other = tmp_path / "other.xml"
    other.write_bytes(XML.replace(b"12.5", b"99.9"))
    located = locate({"aaaa": set()}, [xml_path, str(other)])
    assert located["aaaa"]["uri"] == xml_path


def test_zip_member(tmp_path):
    archive = tmp_path / "grid.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("inner/grid.xml", XML)
    located = locate({"aaaa": set()}, [str(archive)])
    assert located["aaaa"]["startLine"] == 3
    assert located["aaaa"]["uri"].endswith("grid.xml")


def test_locate_violations_columns(xml_path):
    violations = pandas.DataFrame([
        ("aaaa", "Conductor.length", "12.5", "sh:maxInclusive", "m", "Warning", "s"),
        ("aaaa", "IdentifiedObject.name", "L1", "sh:minLength", "m", "Violation", "s"),
        ("bbbb", "Type", None, "sh:closed", "m", "Violation", "s"),   # Type → definition line
        ("cccc", "IdentifiedObject.name", None, "sh:minCount", "m", "Violation", "s"),
        (None, "IdentifiedObject.name", None, "triplets:invalidSparql", "m", "Warning", "s"),
    ], columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])

    frame = violations.shacl.locate(sources=[xml_path])
    assert list(frame.columns[-3:]) == LOCATION_COLUMNS
    assert frame.loc[0, "SOURCE_LINE"] == 5 and frame.loc[0, "SOURCE_COLUMN"] == 5
    assert frame.loc[1, "SOURCE_LINE"] == 4
    assert frame.loc[2, "SOURCE_LINE"] == 7          # object definition
    assert frame.loc[3, "SOURCE_URI"] is None        # object not in sources
    assert frame.loc[4, "SOURCE_URI"] is None        # no focus node

    # standalone function and accessor agree
    pandas.testing.assert_frame_equal(frame, locate_violations(violations, [xml_path]))


def test_locate_violations_empty_frame(xml_path):
    empty = pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE",
                                      "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    frame = locate_violations(empty, [xml_path])
    assert set(LOCATION_COLUMNS) <= set(frame.columns)
    assert frame.empty
