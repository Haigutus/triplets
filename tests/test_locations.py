"""Direct tests for the source-location pass (validation/locations.py) —
previously covered only indirectly through the SARIF export."""
import io
import os
import zipfile

import pandas
import pytest

import triplets  # noqa: F401 — registers the shacl accessor namespace
from triplets.validation import LOCATION_COLUMNS, locate_violations
from triplets.validation.locations import locate, relative_name, relative_sources

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
    assert (line["uri"], line["startLine"]) == (xml_path, 3)
    assert line["snippet"] == '<cim:ACLineSegment rdf:ID="_aaaa">'
    assert line["keyLines"]["Conductor.length"] \
        == (xml_path, 5, "<cim:Conductor.length>12.5</cim:Conductor.length>")
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


def test_key_in_a_later_file_is_located_there(tmp_path):
    """An object defined in an EQ file with a property in an SSH file (rdf:about
    continuation): a finding on the SSH KEY points at the SSH element, one on an EQ
    KEY at the EQ element, a KEY in neither at the EQ definition."""
    eq, ssh = tmp_path / "grid_EQ.xml", tmp_path / "grid_SSH.xml"
    eq.write_bytes(XML)
    ssh.write_bytes(b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://iec.ch/TC57/CIM100#">
  <cim:ACLineSegment rdf:about="#_aaaa">
    <cim:Equipment.inService>true</cim:Equipment.inService>
  </cim:ACLineSegment>
</rdf:RDF>
""")
    violations = pandas.DataFrame({"ID": ["aaaa"] * 3, "KEY": ["Equipment.inService", "Conductor.length", "Missing.key"]})
    located = locate_violations(violations, [str(eq), str(ssh)], targets=False)
    assert list(zip(located["SOURCE_URI"], located["SOURCE_LINE"])) == [(str(ssh), 4), (str(eq), 5), (str(eq), 3)]
    assert located["SOURCE_SNIPPET"][0] == "<cim:Equipment.inService>true</cim:Equipment.inService>"


def test_finding_is_located_in_its_instance_file(tmp_path):
    """A row naming its instance file (INSTANCE_LABEL) is located there: at its KEY's
    element in that file, else at the definition there — unless another file holds the
    KEY (a property of an object another file defines). The same name read from a zip
    is its member name."""
    eq, ssh = tmp_path / "grid_EQ.xml", tmp_path / "grid_SSH.xml"
    eq.write_bytes(XML)
    ssh.write_bytes(b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://iec.ch/TC57/CIM100#">
  <cim:ACLineSegment rdf:about="#_aaaa">
    <cim:IdentifiedObject.name>L1 again</cim:IdentifiedObject.name>
  </cim:ACLineSegment>
</rdf:RDF>
""")
    violations = pandas.DataFrame({"ID": ["aaaa"] * 4,
                                   "KEY": ["IdentifiedObject.name", "Conductor.length", "Absent.key",
                                           "IdentifiedObject.name"],
                                   "VIOLATION_TYPE": [None, None, "xsd:minOccurs", None],
                                   "INSTANCE_LABEL": [str(ssh)] * 3 + [None]})
    located = locate_violations(violations, [str(eq), str(ssh)], targets=False)
    assert list(zip(located["SOURCE_URI"], located["SOURCE_LINE"])) \
        == [(str(ssh), 4), (str(eq), 5), (str(ssh), 3), (str(eq), 4)]
    archive = tmp_path / "grid.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("EQ.xml", eq.read_bytes())
        bundle.writestr("SSH.xml", ssh.read_bytes())
    located = locate_violations(violations.assign(INSTANCE_LABEL="grid.zip/SSH.xml"), [str(archive)], targets=False)
    assert located["SOURCE_URI"].tolist() == ["grid.zip/SSH.xml", "grid.zip/EQ.xml", "grid.zip/SSH.xml",
                                              "grid.zip/SSH.xml"]


def test_absent_key_finding_is_located_at_its_definition():
    """A finding of an absent property is located at the object's definition, never at
    the element the same KEY has in another file defining the same ID."""
    def header(name, body):
        source = io.BytesIO(b'<rdf:RDF xmlns:rdf="r" xmlns:md="m">\n  <md:FullModel rdf:about="urn:uuid:hhhh">\n'
                            + body + b"  </md:FullModel>\n</rdf:RDF>\n")
        source.name = name
        return source

    violations = pandas.DataFrame({"ID": ["hhhh", "hhhh"], "KEY": ["Model.version"] * 2, "VALUE": [None, "x"],
                                   "VIOLATION_TYPE": ["xsd:minOccurs", "xsd:type"],
                                   "INSTANCE_LABEL": ["a.xml", "b.xml"]})
    sources = [header("a.xml", b""), header("b.xml", b"    <md:Model.version>x</md:Model.version>\n")]
    located = locate_violations(violations, sources, targets=False)
    assert list(zip(located["SOURCE_URI"], located["SOURCE_LINE"])) == [("a.xml", 2), ("b.xml", 3)]


def test_absent_key_finding_searches_only_its_id(xml_path):
    """A finding of an absent property (sh:minCount, xsd:minOccurs) searches only its ID:
    located at the definition, and the pass stops once every ID is found — the later
    sources are not read. A sh:maxCount finding (no VALUE, its KEY present) is located
    at its key line."""
    class Counting(io.BytesIO):
        reads = 0

        def read(self, *args):
            Counting.reads += 1
            return super().read(*args)

    sources = [xml_path]
    for number in range(3):
        sources.append(Counting(XML))
        sources[-1].name = f"other{number}.xml"
    violations = pandas.DataFrame({"ID": ["aaaa", "aaaa", "aaaa"],
                                   "KEY": ["Absent.key", "Other.absent", "IdentifiedObject.name"],
                                   "VALUE": [None, None, None],
                                   "VIOLATION_TYPE": ["sh:minCount", "xsd:minOccurs", "sh:maxCount"]})
    located = locate_violations(violations, sources, targets=False)
    assert located["SOURCE_LINE"].tolist() == [3, 3, 4] and Counting.reads == 0


def test_file_like_without_seekable_and_spent_stream(xml_path, caplog):
    """A source with only read() and a name is read as it is. A stream that
    cannot be rewound serves one pass: the second finds nothing and one
    warning names the source."""
    class Reader:
        name = "reader.xml"

        def __init__(self):
            self.stream = io.BytesIO(XML)

        def read(self):
            return self.stream.read()

    reader = Reader()
    with caplog.at_level("WARNING", logger="triplets.validation.locations"):
        assert locate({"aaaa": set()}, [reader])["aaaa"]["startLine"] == 3
        assert not caplog.records
        assert locate({"aaaa": set()}, [reader]) == {}
    assert [record.getMessage() for record in caplog.records] \
        == ["Nothing to read from source reader.xml: no object is located in it"]


def test_zip_member(tmp_path):
    archive = tmp_path / "grid.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("inner/grid.xml", XML)
    located = locate({"aaaa": set()}, [str(archive)])
    assert located["aaaa"]["startLine"] == 3
    assert located["aaaa"]["uri"] == "grid.zip/inner/grid.xml"


def test_zip_member_name_is_no_file_under_the_root(tmp_path):
    """A zip member is named <zip file name>/<member name>, also from a zip in a
    zip: under source_root the name stays as it is, and it is not the name of
    the file on disk that has the member's own name."""
    root = tmp_path / "repo"
    (root / "models").mkdir(parents=True)
    (root / "models" / "grid.xml").write_bytes(XML.replace(b"_aaaa", b"_cccc"))
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as bundle:
        bundle.writestr("deep.xml", XML.replace(b"_aaaa", b"_dddd"))
    with zipfile.ZipFile(root / "models" / "grid.zip", "w") as bundle:
        bundle.writestr("models/grid.xml", XML)
        bundle.writestr("nested.zip", inner.getvalue())
    violations = pandas.DataFrame({"ID": ["aaaa", "cccc", "dddd"], "KEY": None})
    located = locate_violations(violations, [str(root / "models" / "grid.xml"), str(root / "models" / "grid.zip")])
    frame, _ = relative_sources(located, {}, root)
    assert frame["SOURCE_URI"].tolist() == ["grid.zip/models/grid.xml", "models/grid.xml", "nested.zip/deep.xml"]


def test_locate_violations_columns(xml_path):
    violations = pandas.DataFrame([
        ("aaaa", "Conductor.length", "12.5", "sh:maxInclusive", "m", "Warning", "s"),
        ("aaaa", "IdentifiedObject.name", "L1", "sh:minLength", "m", "Violation", "s"),
        ("bbbb", "Type", None, "sh:closed", "m", "Violation", "s"),   # Type → definition line
        ("cccc", "IdentifiedObject.name", None, "sh:minCount", "m", "Violation", "s"),
        (None, "IdentifiedObject.name", None, "triplets:invalidSparql", "m", "Warning", "s"),
    ], columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])

    frame = violations.shacl.locate(sources=[xml_path])
    assert list(frame.columns[-len(LOCATION_COLUMNS):]) == LOCATION_COLUMNS
    assert frame.loc[0, "SOURCE_LINE"] == 5
    assert frame.loc[1, "SOURCE_LINE"] == 4
    assert frame.loc[2, "SOURCE_LINE"] == 7          # object definition
    assert frame.loc[3, "SOURCE_URI"] is None        # object not in sources
    assert frame.loc[4, "SOURCE_URI"] is None        # no focus node
    assert frame[["TARGET_URI", "TARGET_LINE", "TARGET_SNIPPET"]].isna().all().all()   # no TARGET_ID column

    # standalone function and accessor agree
    pandas.testing.assert_frame_equal(frame, locate_violations(violations, [xml_path]))


def test_locate_violations_empty_frame(xml_path):
    empty = pandas.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE",
                                      "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    frame = locate_violations(empty, [xml_path])
    assert set(LOCATION_COLUMNS) <= set(frame.columns)
    assert frame.empty


def test_names_are_stamped_as_given(tmp_path, monkeypatch):
    """locate / locate_violations write the source name they were given: an
    absolute path, a relative path, a file-like's name; a zip member after
    the file name of its zip."""
    (tmp_path / "models").mkdir()
    absolute = tmp_path / "models" / "grid.xml"
    absolute.write_bytes(XML)
    (tmp_path / "models" / "other.xml").write_bytes(XML.replace(b"_aaaa", b"_cccc"))
    with zipfile.ZipFile(tmp_path / "grid.zip", "w") as bundle:
        bundle.writestr("inner/grid.xml", XML.replace(b"_aaaa", b"_dddd"))
    handle = io.BytesIO(XML.replace(b"_aaaa", b"_eeee"))
    handle.name = str(tmp_path / "memory.xml")
    monkeypatch.chdir(tmp_path / "models")

    located = locate({"aaaa": set(), "cccc": set(), "dddd": set(), "eeee": set()},
                     [str(absolute), "other.xml", "../grid.zip", handle])
    assert {object_id: entry["uri"] for object_id, entry in located.items()} \
        == {"aaaa": str(absolute), "cccc": "other.xml", "dddd": "grid.zip/inner/grid.xml", "eeee": handle.name}

    violations = pandas.DataFrame([("cccc", "Conductor.length", "12.5", "sh:maxInclusive", "m", "Warning", "s")],
                                  columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY",
                                           "SOURCE_SHAPE"])
    assert violations.shacl.locate(sources=["other.xml"]).loc[0, "SOURCE_URI"] == "other.xml"


def test_relative_name(tmp_path, monkeypatch):
    """An absolute path of a file in the root or below it → the name relative
    to the root, forward slashes; anything else → None. '..' is collapsed,
    a relative name is never joined with the working directory."""
    root = os.path.realpath(tmp_path / "repo")          # the contract: a resolved root
    monkeypatch.chdir(tmp_path)
    assert relative_name(f"{root}/data/x.xml", root) == "data/x.xml"
    assert relative_name(f"{root}/x.xml", f"{root}/") == "x.xml"
    assert relative_name(f"{root}/data/../x.xml", root) == "x.xml"
    assert relative_name("repo/x.xml", root) is None                     # relative, although cwd/repo/x.xml is inside
    assert relative_name("x.xml", root) is None
    assert relative_name(f"{root}-other/x.xml", root) is None            # sibling with the root as name prefix
    assert relative_name(f"{root}/../other/x.xml", root) is None
    assert relative_name(root, root) is None                             # the root itself is no file in it


def test_source_root_resolves_symlinks(tmp_path):
    """Symlinks are resolved in the root and in the names: a root given through
    a symlink matches files given by their real path, and the other way round.
    A symlink in the root to a file outside it is a name outside."""
    real, link = tmp_path / "repo", tmp_path / "link"
    (real / "models").mkdir(parents=True)
    (real / "models" / "grid.xml").write_bytes(XML)
    (tmp_path / "outside.xml").write_bytes(XML)
    link.symlink_to(real, target_is_directory=True)
    (real / "models" / "alias.xml").symlink_to(tmp_path / "outside.xml")
    names = {"source": [str(real / "models" / "grid.xml"), str(link / "models" / "grid.xml"),
                        str(real / "models" / "alias.xml")]}
    violations = pandas.DataFrame({"ID": ["aaaa"], "SOURCE_URI": [str(link / "models" / "grid.xml")]})
    for source_root in (real, link):
        frame, relative = relative_sources(violations, names, source_root)
        assert frame["SOURCE_URI"].tolist() == ["models/grid.xml"]
        assert relative == {"source": ["models/grid.xml", "models/grid.xml", str(real / "models" / "alias.xml")]}


def test_relative_sources(tmp_path, monkeypatch, caplog):
    """One rule for the SOURCE_URI and INSTANCE_LABEL columns and every list of
    names: absolute under the root → relative; every other name as given.
    One warning names the absolute names outside the root, each once, with
    the root as an absolute path. The inputs are not changed."""
    root = tmp_path / "repo"
    (root / "work").mkdir(parents=True)
    monkeypatch.chdir(root / "work")                   # a name joined with cwd would gain "work/"
    inside, outside = str(root / "models" / "grid.xml"), str(tmp_path / "outside.xml")
    given = [inside, outside, "models/grid.xml", "../models/grid.xml", "inner/grid.xml", "grid.xml", None]
    written = ["models/grid.xml", outside, "models/grid.xml", "../models/grid.xml", "inner/grid.xml", "grid.xml",
               None]
    violations = pandas.DataFrame({"ID": range(len(given)), "SOURCE_URI": given, "INSTANCE_LABEL": given[::-1]})
    violations.attrs["validation"] = {"source": [inside]}
    names = {"source": [inside, outside, "grid.xml"], "references": (str(root / "shapes" / "shapes.ttl"),)}
    before = violations.copy()

    for source_root in (root, str(root), ".."):
        caplog.clear()
        with caplog.at_level("WARNING", logger="triplets.validation.locations"):
            frame, relative = relative_sources(violations, names, source_root)
        assert [None if pandas.isna(name) else name for name in frame["SOURCE_URI"]] == written
        assert [None if pandas.isna(name) else name for name in frame["INSTANCE_LABEL"]] == written[::-1]
        assert relative == {"source": ["models/grid.xml", outside, "grid.xml"], "references": ["shapes/shapes.ttl"]}
        assert [record.getMessage() for record in caplog.records] \
            == [f"1 file name(s) outside source_root {root} keep their names: {outside}"]
    pandas.testing.assert_frame_equal(violations, before)
    assert names["source"][0] == inside
    assert violations.attrs["validation"] == {"source": [inside]}

    bare = violations[["ID"]]                          # no name columns: nothing to map
    frame, relative = relative_sources(bare, {"source": ["grid.xml"]}, root)
    assert list(frame.columns) == ["ID"] and relative == {"source": ["grid.xml"]}
    same_frame, same_names = relative_sources(violations, names, None)
    assert same_frame is violations and same_names is names          # default: the names as given
    assert len(caplog.records) == 1                                    # relative names never warn


def test_locate_violations_targets(xml_path, tmp_path):
    """The referenced object of a reference finding found in the data (TARGET_ID)
    is located in the same pass, at its definition; a dangling one (no TARGET_ID)
    is not wanted — the pass still stops once every wanted object and KEY is found
    (the second source, which does not exist, is never opened)."""
    violations = pandas.DataFrame([
        ("aaaa", "IdentifiedObject.name", "bbbb", "sh:class", "m", "Violation", "s", "bbbb"),
        ("aaaa", "IdentifiedObject.name", "zzzz", "triplets:missing", "m", "Violation", "s", None),
        ("aaaa", "Conductor.length", "12.5", "sh:maxInclusive", "m", "Warning", "s", None),
    ], columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE", "TARGET_ID"])
    frame = locate_violations(violations, [xml_path, str(tmp_path / "absent.xml")])
    assert frame[["TARGET_URI", "TARGET_LINE", "TARGET_SNIPPET"]].values.tolist() == [
        [xml_path, 7, '<cim:Breaker rdf:about="urn:uuid:bbbb">'], [None, None, None], [None, None, None]]
    assert frame["SOURCE_LINE"].tolist() == [4, 4, 5]


def test_located_lines_stay_integers(xml_path):
    """A line column mixing located and not located rows holds int and None,
    not floats: csv and Excel write 7, not 7.0."""
    violations = pandas.DataFrame([
        ("aaaa", "Line.Breaker", "bbbb", "sh:class", "m", "Violation", "s", "bbbb"),
        ("zzzz", "Conductor.length", "12.5", "sh:maxInclusive", "m", "Warning", "s", None),
    ], columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE", "TARGET_ID"])
    frame = locate_violations(violations, [xml_path])
    for column, line in (("SOURCE_LINE", 3), ("TARGET_LINE", 7)):
        assert frame[column].dtype == object
        assert frame[column].tolist() == [line, None] and type(frame[column].iloc[0]) is int
    assert frame[["SOURCE_LINE", "TARGET_LINE"]].to_csv(index=False).splitlines() \
        == ["SOURCE_LINE,TARGET_LINE", "3,7", ","]


SPLIT_EQ = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://iec.ch/TC57/CIM100#">
  <cim:BusbarSection rdf:ID="_bb1">
    <cim:IdentifiedObject.mRID>bb1</cim:IdentifiedObject.mRID>
  </cim:BusbarSection>
  <cim:Terminal rdf:ID="_t1">
    <cim:Terminal.ConductingEquipment rdf:resource="#_bb1"/>
  </cim:Terminal>
</rdf:RDF>
"""
SPLIT_SSH = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://iec.ch/TC57/CIM100#">
  <cim:BusbarSection rdf:about="#_bb1">
    <cim:Equipment.inService>true</cim:Equipment.inService>
  </cim:BusbarSection>
</rdf:RDF>
"""
SPLIT_SHAPE = """@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:S a sh:NodeShape ; sh:targetClass cim:BusbarSection ;
    sh:property [ sh:path {path} ; sh:minCount 1 ] ."""


@pytest.mark.parametrize("path, file, line", [
    ("cim:IdentifiedObject.mRID", "SSH.xml", 3),                        # EQ holds it
    ("[ sh:inversePath cim:Terminal.ConductingEquipment ]", "SSH.xml", 3),   # EQ's Terminal refers to it
    ("cim:IdentifiedObject.name", "EQ.xml", 3)])                        # neither holds it
def test_absent_key_finding_of_part_of_the_data_is_in_a_file_lacking_the_key(tmp_path, path, file, line):
    """A sh:minCount finding of a validation of one file, enriched and located with every
    file: in the first file defining the object without the KEY (on an inverse path, without
    a reference to it by the KEY), not in one holding it."""
    import rdflib
    paths = []
    for name, content in (("EQ.xml", SPLIT_EQ), ("SSH.xml", SPLIT_SSH)):
        paths.append(str(tmp_path / name))
        (tmp_path / name).write_bytes(content)
    data = pandas.read_RDF(paths)
    ssh = data.loc[(data["KEY"] == "label") & data["VALUE"].str.endswith("SSH.xml"), "INSTANCE_ID"].iloc[0]
    shapes = rdflib.Graph().parse(data=SPLIT_SHAPE.format(path=path), format="turtle")
    violations = triplets.validation.validate_shacl(data, shapes, data_scope=[ssh])
    located = violations.shacl.enrich(data=data).shacl.locate(sources=paths)
    assert [(os.path.basename(uri), row) for uri, row in zip(located["SOURCE_URI"], located["SOURCE_LINE"])] \
        == [(file, line)]
