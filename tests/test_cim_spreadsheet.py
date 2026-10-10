"""cim-spreadsheet round trip: CIM XML -> xlsx / CSV -> CIM XML gives the same triples."""
import shutil
import sys
from pathlib import Path

import pytest
from _parity import SKIP_REASON, SVEDALA_DIR, SVEDALA_FILES

import triplets
from triplets.cli import cim_spreadsheet
from triplets.export_schema import schemas

pytestmark = pytest.mark.skipif(not SVEDALA_DIR.exists(), reason=SKIP_REASON)

RDF_MAP = str(schemas.ENTSOE_CGMES_3_0_0_552_ED1)


def triples(paths):
    """(ID, KEY, VALUE) of the model objects; numbers compared by value (the written form may change)."""
    data = triplets.parse(paths)
    meta = data.loc[(data["KEY"] == "Type") & data["VALUE"].isin(["Distribution", "NamespaceMap"]), "ID"]
    data = data[~data["ID"].isin(meta) & (data["KEY"] != "Model.applicationSoftware")]

    def value(text):
        try:
            return float(text) if text.lower() not in ("nan", "inf", "-inf", "infinity") else text
        except ValueError:
            return text
    return {(str(i), str(k), value(str(v))) for i, k, v in zip(data["ID"], data["KEY"], data["VALUE"])}


EQ = str(Path(next(path for path in SVEDALA_FILES if "_EQ_" in path)).resolve())


@pytest.fixture(scope="module")
def reference():
    return triples(EQ)


@pytest.fixture
def source(tmp_path, monkeypatch):
    """The EQ copied into the test directory, so the to-cim output stays in tmp_path."""
    monkeypatch.chdir(tmp_path)
    return shutil.copy(EQ, Path(EQ).name)


@pytest.mark.parametrize("fmt, spreadsheet", [("excel", "model.xlsx"), ("csv", "csv")])
def test_round_trip_same_triples(source, reference, fmt, spreadsheet):
    cim_spreadsheet.cim_to_spreadsheet(source, spreadsheet, format=fmt, zip_output=False)
    cim_spreadsheet.spreadsheet_to_cim(spreadsheet, "out", format=fmt, rdf_map=RDF_MAP, zip_output=False)
    assert triples([str(path) for path in Path("out").glob("*.xml")]) == reference


def test_command_line_to_cim(source, reference, monkeypatch):
    """The documented command: cim-spreadsheet -i model.xlsx -o out/ --rdf-map <schema>."""
    cim_spreadsheet.cim_to_spreadsheet(source, "model.xlsx")
    monkeypatch.setattr(sys, "argv", ["cim-spreadsheet", "-i", "model.xlsx", "-o", "out/",
                                      "--rdf-map", RDF_MAP, "--no-zip"])
    cim_spreadsheet.main()
    assert triples([str(path) for path in Path("out").glob("*.xml")]) == reference


def sheets(path):
    import openpyxl
    return set(openpyxl.load_workbook(path, read_only=True).sheetnames)


def test_parser_metadata_left_out_by_default(source, monkeypatch):
    """Same options as cim-diff: NamespaceMap and Distribution (which holds the
    source path) are left out unless --keep-metadata; -ex adds model classes."""
    def run(output, *options):
        monkeypatch.setattr(sys, "argv", ["cim-spreadsheet", "-i", source, "-o", output, *options])
        cim_spreadsheet.main()
        return sheets(output)
    default = run("default.xlsx")
    assert not default & {"NamespaceMap", "Distribution"}
    assert run("fewer.xlsx", "-ex", "ACLineSegment") == default - {"ACLineSegment"}
    everything = run("all.xlsx", "--keep-metadata")
    assert everything == default | {"NamespaceMap", "Distribution"}
    assert run("both.xlsx", "--keep-metadata", "-ex", "ACLineSegment,Terminal") == everything - {"ACLineSegment", "Terminal"}


def test_to_cim_exclusions(source):
    cim_spreadsheet.cim_to_spreadsheet(source, "model.xlsx")
    cim_spreadsheet.spreadsheet_to_cim("model.xlsx", "out", rdf_map=RDF_MAP, zip_output=False,
                                       exclude_objects=["ACLineSegment"])
    types = {value for _, key, value in triples([str(path) for path in Path("out").glob("*.xml")]) if key == "Type"}
    assert "ACLineSegment" not in types and "Terminal" in types


SSH = str(Path(next(path for path in SVEDALA_FILES if "_SSH_" in path)).resolve())


def cim_diff(monkeypatch, capsys, *argv):
    """Run cim-diff; returns (exit code, stdout)."""
    from triplets.cli import cim_diff
    monkeypatch.setattr(sys, "argv", ["cim-diff", *argv])
    with pytest.raises(SystemExit) as exit:
        cim_diff.main()
    return exit.value.code, capsys.readouterr().out


@pytest.mark.parametrize("options, shown, hidden", [
    ([], ["  ACLineSegment"], ["  NamespaceMap", "  Distribution"]),
    (["-ex", "ACLineSegment"], [], ["  ACLineSegment", "  NamespaceMap", "  Distribution"]),
    (["--keep-metadata"], ["  ACLineSegment", "  NamespaceMap", "  Distribution"], []),
    (["--keep-metadata", "-ex", "ACLineSegment,NamespaceMap"], ["  Distribution"], ["  ACLineSegment", "  NamespaceMap"]),
])
def test_cim_diff_exclusions(monkeypatch, capsys, options, shown, hidden):
    """cim-diff takes the shared exclusion options (EQ vs SSH), before or after the files."""
    for argv in ([EQ, SSH, *options], [*options, EQ, SSH]):
        code, out = cim_diff(monkeypatch, capsys, *argv)
        assert code == 1 and out.startswith("--- ")
        assert all(text in out for text in shown)
        assert not any(text in out for text in hidden)


def test_cim_diff_exit_codes(monkeypatch, capsys):
    """As diff: 0 and no output when equal, 1 different, 2 error."""
    assert cim_diff(monkeypatch, capsys, EQ, EQ) == (0, "")
    assert cim_diff(monkeypatch, capsys, EQ, SSH)[0] == 1
    assert cim_diff(monkeypatch, capsys, EQ, "missing.xml")[0] == 2


def test_cim_diff_report_options(monkeypatch, capsys):
    """--stat prints only the counts; --include narrows the types; --context adds unchanged values."""
    _, full = cim_diff(monkeypatch, capsys, EQ, SSH)
    _, stat = cim_diff(monkeypatch, capsys, EQ, SSH, "--stat")
    assert full.startswith(stat) and len(full) > len(stat)
    assert not any(" -> " in line for line in stat.splitlines())
    _, only = cim_diff(monkeypatch, capsys, EQ, SSH, "--include", "ACLineSegment", "--stat")
    assert "  ACLineSegment" in only and "  Terminal" not in only


def test_cim_diff_context(tmp_path, monkeypatch, capsys):
    changed = tmp_path / "changed.xml"
    text = Path(EQ).read_text(encoding="utf-8")
    changed.write_text(text.replace("<cim:ACLineSegment.r>", "<cim:ACLineSegment.r>1", 1), encoding="utf-8")
    _, plain = cim_diff(monkeypatch, capsys, EQ, str(changed))
    _, context = cim_diff(monkeypatch, capsys, EQ, str(changed), "--context", "IdentifiedObject.name")
    assert "\n IdentifiedObject.name -> " not in plain
    assert "\n IdentifiedObject.name -> " in context


def test_version(monkeypatch, capsys):
    code, out = cim_diff(monkeypatch, capsys, "--version")
    assert code == 0 and triplets.__version__ in out


def test_to_cim_without_rdf_map(source, caplog):
    """No schema: a warning, and every class written in the triplets namespace."""
    cim_spreadsheet.cim_to_spreadsheet(source, "model.xlsx")
    cim_spreadsheet.spreadsheet_to_cim("model.xlsx", "out", zip_output=False)
    assert "--rdf-map" in caplog.text
    assert len(list(Path("out").glob("*.xml"))) == 1


def test_round_trip_with_metadata_writes_to_label(source, reference):
    """--keep-metadata: to-cim writes each instance to its Distribution label, a relative
    label under -o (folders created)."""
    Path("src").mkdir()
    relative = shutil.move(source, "src")
    cim_spreadsheet.cim_to_spreadsheet(relative, "model.xlsx")  # Python API keeps the metadata
    cim_spreadsheet.spreadsheet_to_cim("model.xlsx", "out", rdf_map=RDF_MAP, zip_output=False)
    assert triples([str(Path("out") / relative)]) == reference
