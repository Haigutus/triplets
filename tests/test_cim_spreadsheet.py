"""cim-spreadsheet round trip: CIM XML -> xlsx / CSV -> CIM XML gives the same triples (issue #125)."""
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
    """The EQ copied into the test directory, by relative name: the export names its file
    after the parsed path, so the CIM XML goes to out/<name>."""
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
