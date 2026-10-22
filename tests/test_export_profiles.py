"""CIM XML export against every profile an instance header declares: a CGMES 3.0 EQ file
declaring CoreEquipment, Operation and ShortCircuit exports the keys of all three."""
import logging
import zipfile

import pandas
import pytest

import triplets
from triplets.export_schema import schemas
from triplets.iri import CIM_NS, load_rdf_map

SCHEMA = load_rdf_map(schemas.ENTSOE_CGMES_3_0_0_552_ED1)
PROFILES = ["http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0", "http://iec.ch/TC57/ns/CIM/Operation-EU/3.0",
            "http://iec.ch/TC57/ns/CIM/ShortCircuit-EU/3.0"]
LINE = "11111111-1111-1111-1111-111111111111"
ANALOG = "22222222-2222-2222-2222-222222222222"
OBJECTS = [
    (LINE, "Type", "ACLineSegment", "i"), (LINE, "IdentifiedObject.name", "L1", "i"),   # EQ
    (LINE, "ACLineSegment.r0", "0.1", "i"),                                             # SC
    (ANALOG, "Type", "Analog", "i"), (ANALOG, "Measurement.PowerSystemResource", LINE, "i"),  # OP
]


def header(profiles=PROFILES):
    return [("i", "Type", "FullModel", "i"), *(("i", "Model.profile", profile, "i") for profile in profiles)]


ENGINES = ["python_lxml", "cython_pugixml"]


def frame(rows):
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def cimxml(data, engine, rdf_map=SCHEMA):
    try:
        triplets.export.get_cimxml_engine(engine)
    except Exception as error:      # extension not built in this environment
        pytest.skip(f"{engine} not available: {error}")
    out = data.export_to_cimxml(rdf_map=rdf_map, engine=engine, export_to_memory=True)[0]
    out.seek(0)
    archive = zipfile.ZipFile(out)
    return archive.read(archive.namelist()[0]).decode()


@pytest.mark.parametrize("profiles", [PROFILES, PROFILES[::-1]], ids=["EQ-OP-SC", "SC-OP-EQ"])
@pytest.mark.parametrize("engine", ENGINES)
def test_every_declared_profile_is_exported(engine, profiles):
    """ACLineSegment is concrete in EQ and a Description in SC: rdf:ID whatever the header order."""
    xml = cimxml(frame(header(profiles) + OBJECTS), engine)
    assert "<cim:ACLineSegment.r0>0.1</cim:ACLineSegment.r0>" in xml
    assert f'<cim:ACLineSegment rdf:ID="_{LINE}"' in xml
    assert f'<cim:Analog rdf:ID="_{ANALOG}"' in xml
    assert f'<cim:Measurement.PowerSystemResource rdf:resource="#_{LINE}"' in xml
    assert f'xmlns:cim="{CIM_NS}"' in xml and "triplets" not in xml


@pytest.mark.parametrize("engine", ENGINES)
def test_headerless_frame_warns_on_sectioned_schema(engine, caplog):
    with caplog.at_level(logging.WARNING, logger="triplets.export.cimxml_utils"):
        cimxml(frame(OBJECTS), engine)
    assert "header [] matches none of the schema profiles ['DL', 'DY', 'EQ'," in caplog.text
    assert "nothing is exported unless export_undefined=True" in caplog.text


@pytest.mark.parametrize("rows", [[], header()], ids=["headerless", "header"])
@pytest.mark.parametrize("engine", ENGINES)
def test_flat_schema_exports_its_entries_without_warning(engine, rows, caplog):
    with caplog.at_level(logging.WARNING, logger="triplets.export.cimxml_utils"):
        xml = cimxml(frame(rows + OBJECTS[:2]), engine, rdf_map=SCHEMA["EQ"])
    assert "matches none of the schema profiles" not in caplog.text
    assert f'xmlns:cim="{CIM_NS}"' in xml                                     # the flat schema's ProfileNamespaceMap
    assert "<cim:IdentifiedObject.name>L1</cim:IdentifiedObject.name>" in xml
