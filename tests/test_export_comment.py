"""``comment=`` is written by every CIM XML engine, before the root element."""
import io

import pandas
import pytest

import triplets
from triplets.export_schema import schemas

SCHEMA = schemas.ENTSOE_CGMES_3_0_0_552_ED2
EQ_PROFILE = "http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0"
LINE = "11111111-1111-1111-1111-111111111111"
ROWS = [
    ("i", "Type", "FullModel", "i"), ("i", "Model.profile", EQ_PROFILE, "i"),
    (LINE, "Type", "ACLineSegment", "i"), (LINE, "IdentifiedObject.name", "L1", "i"),
]
COMMENT = "Copyright 2026 Example\nSPDX-License-Identifier: Apache-2.0"


def cimxml(engine):
    try:
        triplets.export.get_cimxml_engine(engine)
    except Exception as error:      # extension not built in this environment
        pytest.skip(f"{engine} not available: {error}")
    data = pandas.DataFrame(ROWS, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    out = data.export_to_cimxml(rdf_map=SCHEMA, engine=engine, export_type="xml_per_instance",
                                export_to_memory=True, comment=COMMENT)[0]
    return out.getvalue().decode()


@pytest.mark.parametrize("engine", ["python_lxml", "cython_pugixml"])
def test_comment_before_root(engine):
    xml = cimxml(engine)
    assert xml.startswith("<?xml")
    assert f"<!--{COMMENT}-->" in xml
    assert xml.index(f"<!--{COMMENT}-->") < xml.index("<rdf:RDF")
    parsed = io.BytesIO(xml.encode())
    parsed.name = "out.xml"
    assert pandas.read_RDF([parsed]).query("KEY == 'IdentifiedObject.name'")["VALUE"].tolist() == ["L1"]
