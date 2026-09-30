"""parse(iri_form="local" | "prefixed" | "absolute") and every exporter reading every form.

The rules are the triplets.iri case tables (compact / expand / schema_name); the end-to-end
contract is "same graph": exporting a frame parsed in any form gives the quads the local form
gives (absolute: up to the node IRIs, which the local form writes as urn:uuid:).
"""
import re
import zipfile

import pandas
import pytest

import triplets
from triplets import iri

CIM16 = "http://iec.ch/TC57/2013/CIM-schema-cim16#"
CIM100 = "http://iec.ch/TC57/CIM100#"
DCT = "http://purl.org/dc/terms/"
UUID = "0f7a1c4e-3b2d-4c5a-9e8f-1a2b3c4d5e6f"
PREFIXES = {"cim": CIM100, "dct": DCT, "rdf": iri.RDF_NS}
PREFIX_OF = {namespace: prefix for prefix, namespace in PREFIXES.items()}
NAMESPACES = {"ACLineSegment.r": CIM100, "type": DCT, "Breaker": CIM100}


# ── rules ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    (CIM100 + "ACLineSegment.r", "cim:ACLineSegment.r"),
    (DCT + "issued", "dct:issued"),
    ("urn:uuid:" + UUID, "urn:uuid:" + UUID),              # URNs are never prefixed
    ("http://unbound.org/ns#x", "http://unbound.org/ns#x"),  # no prefix bound: stays absolute
    ("Breaker", "Breaker"),
    (None, None),
])
def test_compact_iri(text, expected):
    assert iri.compact_iri(text, PREFIX_OF) == expected


@pytest.mark.parametrize("text,expected", [
    ("cim:ACLineSegment.r", CIM100 + "ACLineSegment.r"),
    ("urn:uuid:" + UUID, "urn:uuid:" + UUID),
    ("http://x.org/a#b", "http://x.org/a#b"),
    ("zz:unbound", "zz:unbound"),
    ("Breaker", "Breaker"),
    ("cim:", CIM100),
    (None, None),
])
def test_expand_iri(text, expected):
    assert iri.expand_iri(text, PREFIXES) == expected


@pytest.mark.parametrize("text,expected", [
    ("ACLineSegment.r", "ACLineSegment.r"),                 # local, declared
    ("cim:ACLineSegment.r", "ACLineSegment.r"),             # prefixed, namespace matches
    (CIM100 + "ACLineSegment.r", "ACLineSegment.r"),        # absolute, namespace matches
    (CIM16 + "ACLineSegment.r", None),                      # same local name, other namespace
    ("dct:type", "type"),
    ("rdf:type", None),                                     # never dcterms:type
    (iri.RDF_TYPE, None),
    ("Unknown.x", None),
])
def test_schema_name_matches_only_the_declared_namespace(text, expected):
    assert iri.schema_name(text, NAMESPACES, PREFIXES) == expected


# ── parse ─────────────────────────────────────────────────────────────────────

FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="{cim}"
         xmlns:dct="http://purl.org/dc/terms/" xmlns="http://default.org/ns#">
  <cim:Breaker rdf:about="urn:uuid:{uuid}">
    <cim:IdentifiedObject.name>B1</cim:IdentifiedObject.name>
    <cim:Switch.kind rdf:resource="{cim}SwitchKind.breaker"/>
    <cim:Equipment.EquipmentContainer rdf:resource="#_vl1"/>
    <rdf:type rdf:resource="{cim}Switch"/>
    <dct:type>header type</dct:type>
    <Plain.note>default namespace</Plain.note>
  </cim:Breaker>
</rdf:RDF>
"""


def _parse(tmp_path, iri_form, cim=CIM100, name="a.xml", **kwargs):
    path = tmp_path / name
    path.write_text(FIXTURE.format(cim=cim, uuid=UUID))
    frame = triplets.parse(str(path), engine="python_lxml_pandas", iri_form=iri_form, **kwargs)
    return frame[frame["ID"].astype(str).isin([UUID, "urn:uuid:" + UUID])]


def test_prefixed_form(tmp_path):
    rows = set(_parse(tmp_path, "prefixed")[["ID", "KEY", "VALUE"]].astype(str).itertuples(index=False, name=None))
    assert rows == {
        (UUID, "Type", "cim:Breaker"),                                   # CIM ID convention stays local
        (UUID, "cim:IdentifiedObject.name", "B1"),
        (UUID, "cim:Switch.kind", "cim:SwitchKind.breaker"),
        (UUID, "cim:Equipment.EquipmentContainer", "vl1"),
        (UUID, "rdf:type", "cim:Switch"),                                # explicit rdf:type: no collision
        (UUID, "dct:type", "header type"),
        (UUID, "http://default.org/ns#Plain.note", "default namespace"), # default namespace: no prefix
    }


def test_absolute_form(tmp_path):
    rows = set(_parse(tmp_path, "absolute")[["ID", "KEY", "VALUE"]].astype(str).itertuples(index=False, name=None))
    assert ("urn:uuid:" + UUID, iri.RDF_TYPE, CIM100 + "Switch") in rows
    assert ("urn:uuid:" + UUID, CIM100 + "Equipment.EquipmentContainer", "http://triplets#_vl1") in rows


def test_prefixes_override_the_document_map(tmp_path):
    rows = _parse(tmp_path, "prefixed", prefixes={"c": CIM100})
    assert "c:IdentifiedObject.name" in set(rows["KEY"].astype(str))


# ── export: the same graph from every form ────────────────────────────────────

DCAT = "http://www.w3.org/ns/dcat#"
RESOURCE = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource"
SCHEMA = {"EQ": {
    "ProfileMetadata": {"keyword": "EQ"},
    "Dataset": {"type": "Class", "namespace": DCAT,
                "attrib": {"attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "value_prefix": "urn:uuid:"}},
    "keyword": {"type": "Attribute", "xsd:type": "xsd:string", "namespace": DCAT},
    "Breaker": {"type": "Class", "namespace": CIM100,
                "attrib": {"attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "value_prefix": "urn:uuid:"}},
    "IdentifiedObject.name": {"type": "Attribute", "xsd:type": "xsd:string", "namespace": CIM100},
    "Switch.kind": {"type": "Enumeration", "namespace": CIM100, "attrib": {"attribute": RESOURCE, "value_prefix": ""}},
    "SwitchKind.breaker": {"type": "EnumerationValue", "namespace": CIM100},
    "Equipment.EquipmentContainer": {"type": "Association", "namespace": CIM100,
                                     "attrib": {"attribute": RESOURCE, "value_prefix": "urn:uuid:"}},
    "type": {"type": "Attribute", "xsd:type": "xsd:string", "namespace": DCT},
}}

# schema-declared names only (plus a header the CIM XML exporter resolves the profile from):
# every form must then give one graph
SAME = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="{cim}" xmlns:dcat="http://www.w3.org/ns/dcat#">
  <dcat:Dataset rdf:about="urn:uuid:11111111-1111-1111-1111-111111111111"><dcat:keyword>EQ</dcat:keyword></dcat:Dataset>
  <cim:Breaker rdf:about="urn:uuid:{uuid}">
    <cim:IdentifiedObject.name>B1</cim:IdentifiedObject.name>
    <cim:Switch.kind rdf:resource="{cim}SwitchKind.breaker"/>
    <cim:Equipment.EquipmentContainer rdf:resource="#_vl1"/>
  </cim:Breaker>
</rdf:RDF>
"""


def _quads(frame, engine):
    if engine == "polars":
        polars = pytest.importorskip("polars")
        frame = polars.from_pandas(frame.astype(str))
    text = triplets.export.export_to_nquads(frame, rdf_map=SCHEMA, engine=engine, export_to_memory=True).getvalue().decode()
    return sorted(re.sub(r" <urn:uuid:[0-9a-f-]{36}> \.$", " G .", line) for line in text.splitlines())


def _full(tmp_path, iri_form, template=SAME, **kwargs):
    path = tmp_path / f"{iri_form}.xml"
    path.write_text(template.format(cim=CIM100, uuid=UUID))
    frame = triplets.parse(str(path), engine="python_lxml_pandas", iri_form=iri_form, **kwargs)
    meta = set(frame.loc[(frame["KEY"] == "Type") & frame["VALUE"].isin(["Distribution", "NamespaceMap"]), "ID"].astype(str))
    return frame, meta


def _data_quads(tmp_path, form, engine, template=SAME):
    frame, meta = _full(tmp_path, form, template)
    return [line for line in _quads(frame, engine) if not any(f"<urn:uuid:{m}>" in line for m in meta)]


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_nquads_same_graph_from_every_form(tmp_path, engine):
    local, prefixed, absolute = (_data_quads(tmp_path, form, engine) for form in ("local", "prefixed", "absolute"))
    assert prefixed == local
    to_node = lambda line: line.replace("<http://triplets#_vl1>", "<urn:uuid:vl1>")  # noqa: E731 — base#_x ↔ urn:uuid:x
    assert sorted(map(to_node, absolute)) == local


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_nquads_prefixed_keeps_what_the_local_form_merges(tmp_path, engine):
    """The prefixed form exports an explicit rdf:type as rdf:type and dct:type as dcterms:type
    (the local form merges both into the KEY "type"), and a default-namespace element in its
    own namespace (the local form loses it)."""
    lines = _data_quads(tmp_path, "prefixed", engine, FIXTURE)
    assert any(f"<{iri.RDF_TYPE}> <{CIM100}Switch>" in line for line in lines)
    assert any(f'<{DCT}type> "header type"' in line for line in lines)
    assert any('<http://default.org/ns#Plain.note> "default namespace"' in line for line in lines)


@pytest.mark.parametrize("engine", ["python_lxml", "cython_pugixml"])
def test_cimxml_prefixed_frame_exports_like_local(tmp_path, engine):
    try:
        triplets.export.get_cimxml_engine(engine)
    except Exception as error:
        pytest.skip(f"{engine} not available: {error}")

    def xml(form):
        frame, _ = _full(tmp_path, form)
        out = triplets.export.export_to_cimxml(frame, rdf_map=SCHEMA, engine=engine, export_to_memory=True)[0]
        out.seek(0)
        archive = zipfile.ZipFile(out)
        return sorted(line.strip() for line in archive.read(archive.namelist()[0]).decode().splitlines()
                      if "urn:uuid:" in line or "cim:" in line)
    local = xml("local")
    assert any("cim:Breaker" in line for line in local) and any("SwitchKind.breaker" in line for line in local)
    assert xml("prefixed") == local


def test_per_instance_prefix_maps(tmp_path):
    """cim: binds CIM16 in one file and CIM100 in another — each expands with its own map."""
    one = _parse(tmp_path, "prefixed", cim=CIM16, name="one.xml")
    frames = pandas.concat([
        triplets.parse(str(tmp_path / "one.xml"), engine="python_lxml_pandas", iri_form="prefixed"),
        triplets.parse(str(_write(tmp_path, "two.xml", CIM100)), engine="python_lxml_pandas", iri_form="prefixed"),
    ], ignore_index=True)
    assert set(one["KEY"].astype(str)) >= {"cim:IdentifiedObject.name"}
    lines = _quads(frames, "pandas")
    assert any(f"<{CIM16}IdentifiedObject.name>" in line for line in lines)
    assert any(f"<{CIM100}IdentifiedObject.name>" in line for line in lines)


def _write(tmp_path, name, cim):
    path = tmp_path / name
    path.write_text(FIXTURE.format(cim=cim, uuid=UUID))
    return path


def test_sparql_query_reads_a_prefixed_frame(tmp_path):
    pytest.importorskip("pyoxigraph")
    frame, _ = _full(tmp_path, "prefixed")
    local, _ = _full(tmp_path, "local")
    query = f"SELECT ?name WHERE {{ ?s <{CIM100}IdentifiedObject.name> ?name }}"
    for engine in ("oxigraph", "qlever", "rdflib"):
        try:
            triplets.sparql.get_engine(engine)
        except Exception as error:      # engine not built / installed here
            pytest.skip(f"{engine} not available: {error}")
        got = triplets.sparql.query(frame, query, rdf_map=SCHEMA, engine=engine)["name"].tolist()
        assert got == triplets.sparql.query(local, query, rdf_map=SCHEMA, engine=engine)["name"].tolist() == ["B1"]
