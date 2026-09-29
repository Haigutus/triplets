"""Undefined names are one concept in every exporter: ``export_undefined`` says whether
they are written, ``undefined_namespace`` says where. SPARQL and validation always load
everything and default the namespace to CIM100 so schema-less data answers ``cim:`` queries."""
import zipfile

import pandas
import pytest

import triplets
from triplets.export_schema import schemas
from triplets.iri import CIM_NS, TRIPLETS_NS

SCHEMA = schemas.ENTSOE_CGMES_3_0_0_552_ED2
EQ_PROFILE = "http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0"
LINE = "11111111-1111-1111-1111-111111111111"
CUSTOM = "22222222-2222-2222-2222-222222222222"
ROWS = [
    ("i", "Type", "FullModel", "i"), ("i", "Model.profile", EQ_PROFILE, "i"),
    (LINE, "Type", "ACLineSegment", "i"),
    (LINE, "IdentifiedObject.name", "L1", "i"),
    (LINE, "Custom.foo", "bar", "i"),                                     # undefined KEY
    (CUSTOM, "Type", "CustomThing", "i"),                                 # undefined class
    (LINE, "Equipment.EquipmentContainer", "33333333-3333-3333-3333-333333333333", "i"),
    (LINE, "Type", "Breaker", "i"),                                       # second Type row only to carry the enum below
    (LINE, "Switch.kind", "SwitchKind.nope", "i"),                        # undefined enum value under a defined enum key
]


def frame():
    return pandas.DataFrame(ROWS, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def nquads(**kwargs):
    return frame().export_to_nquads(rdf_map=SCHEMA, export_to_memory=True, **kwargs).read().decode().splitlines()


def cimxml(engine, **kwargs):
    out = frame().export_to_cimxml(rdf_map=SCHEMA, engine=engine, export_to_memory=True, **kwargs)[0]
    out.seek(0)
    archive = zipfile.ZipFile(out)
    return archive.read(archive.namelist()[0]).decode()


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_nquads_undefined_switch_and_namespace(engine):
    pytest.importorskip(engine)
    kept = nquads(engine=engine)
    assert any(f"<{TRIPLETS_NS}Custom.foo>" in line for line in kept)
    assert any(f"<{TRIPLETS_NS}CustomThing>" in line for line in kept)
    dropped = nquads(engine=engine, export_undefined=False)
    assert not any("Custom" in line for line in dropped)
    assert len(kept) - len(dropped) == 3                                   # undefined key, class and enum value
    custom = nquads(engine=engine, undefined_namespace="http://acme#")
    assert any("<http://acme#Custom.foo>" in line for line in custom)
    assert any("<http://acme#CustomThing>" in line for line in custom)


def test_nquads_schema_less_every_name_is_undefined():
    lines = frame().export_to_nquads(export_to_memory=True).read().decode().splitlines()
    assert all(TRIPLETS_NS in line for line in lines if "rdf-syntax-ns#type" in line)
    lines = frame().export_to_nquads(export_to_memory=True, undefined_namespace=CIM_NS).read().decode().splitlines()
    assert any(f"<{CIM_NS}ACLineSegment>" in line for line in lines)


@pytest.mark.parametrize("engine", ["python_lxml", "cython_pugixml"])
def test_cimxml_undefined_switch_and_namespace(engine):
    try:
        triplets.export.get_cimxml_engine(engine)
    except Exception as error:      # extension not built in this environment
        pytest.skip(f"{engine} not available: {error}")
    kept = cimxml(engine, export_undefined=True)
    assert "<triplets:Custom.foo>bar</triplets:Custom.foo>" in kept
    assert f'<triplets:CustomThing rdf:about="urn:uuid:{CUSTOM}"' in kept
    assert f'xmlns:triplets="{TRIPLETS_NS}"' in kept
    assert "SwitchKind.nope" in kept
    dropped = cimxml(engine, export_undefined=False)
    assert "Custom" not in dropped and "xmlns:triplets" not in dropped
    assert "Switch.kind" not in dropped                                  # no empty element left behind either
    custom = cimxml(engine, export_undefined=True, undefined_namespace="http://acme#")
    assert "<triplets:Custom.foo>" in custom and 'xmlns:triplets="http://acme#"' in custom
    # a namespace the document already binds keeps its prefix: defined tags stay cim:
    bound = cimxml(engine, export_undefined=True, undefined_namespace=CIM_NS)
    assert "<cim:Custom.foo>bar</cim:Custom.foo>" in bound and "<cim:IdentifiedObject.name>" in bound
    assert "xmlns:triplets" not in bound


def test_cimxml_engines_agree_on_undefined_names():
    for engine in ("python_lxml", "cython_pugixml"):
        try:
            triplets.export.get_cimxml_engine(engine)
        except Exception as error:
            pytest.skip(f"{engine} not available: {error}")
    lxml, cython = (cimxml(engine, export_undefined=True) for engine in ("python_lxml", "cython_pugixml"))
    pick = lambda xml: sorted(line.strip().replace(" />", "/>") for line in xml.splitlines() if "Custom" in line)  # noqa: E731
    assert pick(lxml) == pick(cython)


def test_sparql_query_undefined_namespace():
    pytest.importorskip("pyoxigraph")
    data = frame()
    cim = triplets.sparql.query(data, f"SELECT ?s WHERE {{ ?s a <{CIM_NS}CustomThing> }}", engine="oxigraph")
    assert cim["s"].tolist() == [f"urn:uuid:{CUSTOM}"]                     # CIM100 by default, even without a schema
    acme = triplets.sparql.query(data, "SELECT ?s WHERE { ?s a <http://acme#CustomThing> }",
                                 engine="oxigraph", undefined_namespace="http://acme#")
    assert acme["s"].tolist() == [f"urn:uuid:{CUSTOM}"]


def test_validate_undefined_namespace_reaches_sparql_constraints():
    rdflib = pytest.importorskip("rdflib")
    shape = """@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
    @prefix acme: <http://acme#> .
    acme:S a sh:NodeShape ; sh:targetClass acme:ACLineSegment ; sh:property [ sh:path acme:Custom.foo ;
        sh:sparql [ sh:message "no bar" ; sh:prefixes <http://p> ;
                    sh:select 'SELECT $this ?value WHERE { $this acme:Custom.foo ?value . FILTER (str(?value) = "bar") }' ] ] .
    <http://p> sh:declare [ sh:prefix "acme" ; sh:namespace "http://acme#"^^xsd:anyURI ] ."""
    graph = rdflib.Graph()
    graph.parse(data=shape, format="turtle")
    violations = triplets.validation.validate(frame(), graph, engine="pandas", undefined_namespace="http://acme#")
    assert set(violations.loc[violations["VIOLATION_TYPE"] == "sh:sparql", "VALUE"]) == {"bar"}
