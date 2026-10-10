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
    """Same option and defaults as cim-diff: NamespaceMap and Distribution
    (which holds the source path) are not written unless asked for."""
    cim_spreadsheet.cim_to_spreadsheet(source, "default.xlsx")
    assert not sheets("default.xlsx") & {"NamespaceMap", "Distribution"}
    monkeypatch.setattr(sys, "argv", ["cim-spreadsheet", "-i", source, "-o", "all.xlsx", "-ex"])
    cim_spreadsheet.main()
    assert {"NamespaceMap", "Distribution"} <= sheets("all.xlsx")
    monkeypatch.setattr(sys, "argv", ["cim-spreadsheet", "-i", source, "-o", "fewer.xlsx", "-ex", "ACLineSegment"])
    cim_spreadsheet.main()
    assert sheets("fewer.xlsx") == sheets("all.xlsx") - {"ACLineSegment"}


SSH = str(Path(next(path for path in SVEDALA_FILES if "_SSH_" in path)).resolve())


@pytest.mark.parametrize("options, shown, hidden", [
    ([], ["  ACLineSegment"], ["  NamespaceMap", "  Distribution"]),
    (["-ex", "ACLineSegment"], ["  NamespaceMap", "  Distribution"], ["  ACLineSegment"]),
    (["-ex"], ["  ACLineSegment", "  NamespaceMap", "  Distribution"], []),
])
def test_cim_diff_exclusions(monkeypatch, capsys, options, shown, hidden):
    """cim-diff runs and takes the shared exclusion options (EQ vs SSH)."""
    from triplets.cli import cim_diff
    monkeypatch.setattr(sys, "argv", ["cim-diff", EQ, SSH, *options])
    cim_diff.main()
    out = capsys.readouterr().out
    assert out.startswith("--- ")
    assert all(text in out for text in shown)
    assert not any(text in out for text in hidden)
