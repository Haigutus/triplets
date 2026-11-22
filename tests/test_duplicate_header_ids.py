"""Two CIM XML files that share a header ID (the FullModel subject) are two instances.

The 552 serialization requires a unique header ID per file; real data reuses one ID in
two files with different content (a network-code file and a grid-situation file of the
same name). INSTANCE_ID is minted per parse and never derived from the header ID, so
every parser and loader, the exports, schema validation, the header helpers and the
source locations keep the two files apart. The twins below are small synthetic files:
``alpha`` (EQ, valid header) and ``beta`` (SSH, ``Model.scenarioTime`` not a dateTime),
read from memory, from paths and as two members of one zip, in both load orders.

``validate_shacl`` over both instances merges the two header nodes (one IRI is one RDF
node); its findings on the content objects still locate in their own files.
"""
import io
import json
import zipfile

import pandas
import pytest

import triplets
from triplets import cgmes_tools, export, validation
from triplets._header import distribution_labels
from triplets.export_schema import schemas
from triplets.iri import load_rdf_map
from triplets.parser import get_engine as get_parser_engine

from _parity import build_engine, to_pandas

HEADER = "6b3e2c8a-51f0-4c1d-9d2e-7a40c3e1d00b"
# the CGMES 3.0 schema cut to the EQ and SSH entries the twins use: a small compile
KEPT = {"ProfileMetadata", "ProfileNamespaceMap", "FullModel", "Model.scenarioTime", "Model.description",
        "Model.version", "Model.profile", "Model.DependentOn", "Substation", "IdentifiedObject.mRID",
        "IdentifiedObject.name"}
SCHEMA = {section: {name: entry for name, entry in entries.items() if name in KEPT}
          for section, entries in load_rdf_map(schemas.ENTSOE_CGMES_3_0_0_552_ED1).items() if section in ("EQ", "SSH")}
TWINS = {
    "alpha": {"path": "NetworkCode/X.xml", "profile": "http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0",
              "scenario": "2022-06-15T22:30:00Z", "description": "alpha header", "version": "1",
              "object": "aaaa", "name": "Alpha"},
    "beta": {"path": "GridSituation/cimxml/X.xml", "profile": "http://iec.ch/TC57/ns/CIM/SteadyStateHypothesis-EU/3.0",
             "scenario": "not-a-time", "description": "beta header", "version": "2",
             "object": "bbbb", "name": "Beta"},
}
ORDERS = {"alpha-first": ("alpha", "beta"), "beta-first": ("beta", "alpha")}
KINDS = ["memory", "paths", "zip"]


def cimxml(twin):
    spec = TWINS[twin]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:cim="http://iec.ch/TC57/CIM100#"
         xmlns:md="http://iec.ch/TC57/61970-552/ModelDescription/1#">
  <md:FullModel rdf:about="urn:uuid:{HEADER}">
    <md:Model.scenarioTime>{spec["scenario"]}</md:Model.scenarioTime>
    <md:Model.description>{spec["description"]}</md:Model.description>
    <md:Model.version>{spec["version"]}</md:Model.version>
    <md:Model.profile>{spec["profile"]}</md:Model.profile>
  </md:FullModel>
  <cim:Substation rdf:ID="_{spec["object"]}">
    <cim:IdentifiedObject.mRID>{spec["object"]}</cim:IdentifiedObject.mRID>
    <cim:IdentifiedObject.name>{spec["name"]}</cim:IdentifiedObject.name>
  </cim:Substation>
</rdf:RDF>
""".encode()


def header_rows(twin):
    spec = TWINS[twin]
    return {("Type", "FullModel"), ("Model.scenarioTime", spec["scenario"]),
            ("Model.description", spec["description"]), ("Model.version", spec["version"]),
            ("Model.profile", spec["profile"])}


def named(name, content):
    file = io.BytesIO(content)
    file.name = name
    return file


class Pair:
    """The twins as one kind of source, in one load order: ``inputs()`` gives fresh
    parser inputs, ``label`` the Distribution label and ``uri`` the located file name
    of each twin."""

    def __init__(self, kind, order, tmp_path):
        self.kind, self.order = kind, ORDERS[order]
        if kind == "paths":
            for twin in self.order:
                path = tmp_path / TWINS[twin]["path"]
                path.parent.mkdir(parents=True)
                path.write_bytes(cimxml(twin))
            self.label = {twin: str(tmp_path / TWINS[twin]["path"]) for twin in TWINS}
            self.uri = dict(self.label)
        else:
            self.label = {twin: TWINS[twin]["path"] for twin in TWINS}
            self.uri = {twin: f"twins.zip/{TWINS[twin]['path']}" if kind == "zip" else TWINS[twin]["path"]
                        for twin in TWINS}

    def _one(self, twin):
        return self.label[twin] if self.kind == "paths" else named(TWINS[twin]["path"], cimxml(twin))

    def inputs(self):
        if self.kind != "zip":
            return [self._one(twin) for twin in self.order]
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            for twin in self.order:
                zipped.writestr(TWINS[twin]["path"], cimxml(twin))
        archive.name = "twins.zip"
        archive.seek(0)
        return [archive]

    def instances(self, data):
        """{twin: INSTANCE_ID} by the Distribution label of each parsed file."""
        labels = distribution_labels(pandas_frame(data))
        twin_of = {label: twin for twin, label in self.label.items()}
        found = {twin_of[label]: str(instance) for label, instance in zip(labels["VALUE"], labels["INSTANCE_ID"])
                 if label in twin_of}
        assert len(found) == 2 and len(set(found.values())) == 2, found
        return found


def pandas_frame(data):
    """Any flavor's triplets (a DuckDB connection: its triplets table) or result as pandas."""
    if type(data).__name__ == "DuckDBPyConnection":
        return data.execute("SELECT * FROM triplets").df()
    return to_pandas(data)


def available(get_engine, name):
    try:
        get_engine(name)
        return True
    except Exception:                    # noqa: BLE001 — not built / dependency missing
        return False


PARSERS = [name for name in ("python_lxml_pandas", "python_lxml_arrow", "cython_pugixml_arrow")
           if available(get_parser_engine, name)]
CIMXML_ENGINES = [name for name in ("python_lxml", "cython_pugixml") if available(export.get_cimxml_engine, name)]
ENGINES = ["pandas", "polars", "duckdb"]


@pytest.fixture(params=[(kind, order) for kind in KINDS for order in ORDERS], ids=lambda param: "-".join(param))
def pair(request, tmp_path):
    return Pair(*request.param, tmp_path)


@pytest.fixture(params=ORDERS)
def path_pair(request, tmp_path):
    return Pair("paths", request.param, tmp_path)


@pytest.fixture(params=ENGINES)
def engine(request):
    if request.param != "pandas":
        pytest.importorskip(request.param)
    return request.param


def parsed(pair):
    return triplets.parse(pair.inputs(), engine="python_lxml_pandas")


def flavored(pair, engine):
    """(the twins in the *engine* flavor, {twin: INSTANCE_ID})."""
    data = parsed(pair)
    return build_engine(engine, data), pair.instances(data)


# ── loaders: one instance per file, each with its own rows ──────────────────

def load(loader, inputs):
    if loader == "load_RDF_to_dataframe":
        from triplets.rdf_parser import load_RDF_to_dataframe
        with pytest.warns(DeprecationWarning):
            return load_RDF_to_dataframe(inputs)
    if loader.startswith("duckdb-"):
        duckdb = pytest.importorskip("duckdb")
        connection = duckdb.connect()
        connection.read_rdf(inputs, engine=loader.removeprefix("duckdb-"))
        return connection
    engine, return_type = loader.rsplit("-", 1)
    if return_type == "polars":
        pytest.importorskip("polars")
    return triplets.parse(inputs, engine=engine, return_type=return_type)


LOADERS = ([f"{engine}-{return_type}" for engine in PARSERS for return_type in ("pandas", "arrow", "polars")]
           + ["load_RDF_to_dataframe"] + [f"duckdb-{engine}" for engine in PARSERS if engine.endswith("_arrow")])


@pytest.mark.parametrize("loader", LOADERS)
def test_each_file_is_its_own_instance(pair, loader):
    """Two INSTANCE_IDs; each holds its own header rows, its own content object and
    its own Distribution label / fileName / container — nothing of the twin."""
    data = pandas_frame(load(loader, pair.inputs())).astype({"INSTANCE_ID": str})
    instances = pair.instances(data)
    for twin, instance in instances.items():
        rows = data[data["INSTANCE_ID"] == instance]
        header = rows[rows["ID"] == HEADER]
        assert set(zip(header["KEY"], header["VALUE"])) == header_rows(twin)
        assert set(rows.loc[rows["VALUE"] == "Substation", "ID"]) == {TWINS[twin]["object"]}
        metadata = rows[rows["KEY"].isin(["label", "fileName", "belongsToContainer"])]
        expected = {("label", pair.label[twin]), ("fileName", "X.xml")}
        if pair.kind == "zip":
            expected.add(("belongsToContainer", "twins.zip"))
        assert set(zip(metadata["KEY"], metadata["VALUE"])) == expected


# ── exports: each instance writes its own header and content ────────────────

@pytest.mark.parametrize("flavor", ["pandas", "polars"])
@pytest.mark.parametrize("cimxml_engine", CIMXML_ENGINES)
def test_cimxml_export_per_instance(pair, cimxml_engine, flavor):
    """Every exported file reads back as its own header and content object (beta's
    Substation is no SSH class: written as undefined)."""
    data = build_engine(flavor, parsed(pair))
    files = export.export_to_cimxml(data, rdf_map=SCHEMA, export_type="xml_per_instance", export_undefined=True,
                                    export_to_memory=True, engine=cimxml_engine)
    assert sorted(file.name for file in files) == sorted(pair.label.values())
    twin_of = {label: twin for twin, label in pair.label.items()}
    for file in files:
        twin = twin_of[file.name]
        back = triplets.parse([named("export.xml", file.getvalue())], engine="python_lxml_pandas")
        header = back[back["ID"] == HEADER]
        assert set(zip(header["KEY"], header["VALUE"])) == header_rows(twin)
        assert set(back.loc[back["VALUE"] == "Substation", "ID"]) == {TWINS[twin]["object"]}


@pytest.mark.parametrize("nquads_engine", ["pandas", "polars"])
def test_nquads_export_per_instance(pair, nquads_engine):
    """One named graph per instance; read back, each graph holds its own file's rows."""
    if nquads_engine == "polars":
        pytest.importorskip("polars")
    data = parsed(pair)
    instances = pair.instances(data)
    buffer = export.export_to_nquads(data, export_to_memory=True, engine=nquads_engine)
    back = triplets.read_nquads(buffer).astype({"INSTANCE_ID": str})
    assert set(back["INSTANCE_ID"]) == set(instances.values())
    original = data.astype({"INSTANCE_ID": str})

    def own(frame, twin):
        rows = frame[(frame["INSTANCE_ID"] == instances[twin]) & frame["ID"].isin([HEADER, TWINS[twin]["object"]])]
        return set(zip(rows["ID"], rows["KEY"], rows["VALUE"]))

    for twin in TWINS:
        assert own(back, twin) == own(original, twin)
        assert {(key, value) for identifier, key, value in own(back, twin) if identifier == HEADER} == header_rows(twin)


# ── schema validation: per instance ─────────────────────────────────────────

def header_findings(violations):
    rows = violations[violations["ID"] == HEADER]
    return sorted(zip(rows["KEY"], rows["VALUE"], rows["VIOLATION_TYPE"]))


BAD_HEADER = [("Model.scenarioTime", "not-a-time", "xsd:type")]


def test_validate_schema_per_instance(path_pair, engine):
    """The header property invalid in beta only is found in beta's run only, and once
    over both, on beta's INSTANCE_ID: each instance counts its own header rows (no
    maxOccurs from the twin)."""
    data, instances = flavored(path_pair, engine)

    def run(scope=None):
        return validation.validate_schema(data, SCHEMA, data_scope=scope, rdf_map_scope="EQ", engine=engine)

    assert header_findings(run([instances["alpha"]])) == []
    assert header_findings(run([instances["beta"]])) == BAD_HEADER
    both = run()
    assert header_findings(both) == BAD_HEADER
    assert list(both.loc[both["ID"] == HEADER, "INSTANCE_ID"]) == [instances["beta"]]


def test_validate_by_header_per_instance(path_pair, engine):
    """Each twin is validated against the profile its own header declares."""
    data, instances = flavored(path_pair, engine)
    violations = cgmes_tools.validate_by_header(data, SCHEMA, engine=engine)
    rows = violations[violations["ID"] == HEADER]
    assert header_findings(violations) == BAD_HEADER and list(rows["PROFILE"].iloc[0]) == ["SSH"]
    assert list(rows["INSTANCE_ID"]) == [instances["beta"]]
    scoped = cgmes_tools.validate_by_header(data, SCHEMA, data_scope=[instances["alpha"]], engine=engine)
    assert header_findings(scoped) == []


DANGLING = "deadbeef-0000-4000-8000-000000000000"


def test_schema_references_per_instance(path_pair, engine):
    """beta's header depends on a part nobody loaded: validate_schema_references and
    validate_by_header(references=True) report it once, on beta's INSTANCE_ID and file —
    alpha's run does not see beta's association on the shared ID."""
    beta = cimxml("beta").replace(b"<md:Model.version>",
                                  f'<md:Model.DependentOn rdf:resource="urn:uuid:{DANGLING}"/>\n    '
                                  "<md:Model.version>".encode())
    inputs = [named(path_pair.label[twin], beta if twin == "beta" else cimxml(twin)) for twin in path_pair.order]
    data = triplets.parse(inputs, engine="python_lxml_pandas")
    instances = path_pair.instances(data)
    data = build_engine(engine, data)
    expected = [(HEADER, "Model.DependentOn", DANGLING, "triplets:missing", instances["beta"])]
    for violations in (validation.validate_schema_references(data, SCHEMA, rdf_map_scope="SSH", engine=engine),
                       cgmes_tools.validate_by_header(data, SCHEMA, references=True, engine=engine)):
        missing = violations[violations["VIOLATION_TYPE"] == "triplets:missing"]
        assert list(zip(missing["ID"], missing["KEY"], missing["VALUE"], missing["VIOLATION_TYPE"],
                        missing["INSTANCE_ID"])) == expected
        assert list(validation.enrich(missing, data=data)["INSTANCE_LABEL"]) == [path_pair.label["beta"]]


def test_scoped_finding_enriches_and_locates_in_its_file(path_pair, engine):
    """beta's run, enriched with its scope and located in its file, names beta's
    INSTANCE_ID, file and line — in the frame and in the SARIF log."""
    data, instances = flavored(path_pair, engine)
    scope = [instances["beta"]]
    violations = validation.validate_schema(data, SCHEMA, data_scope=scope, rdf_map_scope="EQ", engine=engine)
    violations = violations[violations["ID"] == HEADER]
    enriched = validation.enrich(violations, data=data, data_scope=scope)
    assert list(enriched["INSTANCE_ID"].astype(str)) == scope
    assert list(enriched["INSTANCE_LABEL"]) == [path_pair.uri["beta"]]
    located = validation.locate_violations(enriched, path_pair.inputs())
    assert list(located["SOURCE_URI"]) == [path_pair.uri["beta"]]
    assert "not-a-time" in located["SOURCE_SNIPPET"].iloc[0]
    log = json.loads(validation.export_to_sarif(violations, data=data, data_scope=scope, group=False,
                                                sources=path_pair.inputs(), export_to_memory=True).getvalue())
    [result] = log["runs"][0]["results"]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == path_pair.uri["beta"]


def enriched_header_finding(pair_, engine):
    """(data, {twin: INSTANCE_ID}, the header finding of validate_schema over both twins, enriched)."""
    data, instances = flavored(pair_, engine)
    violations = validation.validate_schema(data, SCHEMA, rdf_map_scope="EQ", engine=engine)
    return data, instances, validation.enrich(violations[violations["ID"] == HEADER], data=data)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("order", ORDERS)
def test_enrich_names_the_instance_of_the_finding(kind, order, tmp_path, engine):
    """Over both twins, the header finding is enriched with beta's INSTANCE_ID and file."""
    pair_ = Pair(kind, order, tmp_path)
    _, instances, enriched = enriched_header_finding(pair_, engine)
    assert list(enriched["INSTANCE_ID"].astype(str)) == [instances["beta"]]
    assert list(enriched["INSTANCE_LABEL"]) == [pair_.uri["beta"]]


def test_enrich_without_data_keeps_the_instance(path_pair):
    """enrich with only an rdf_map (no data) keeps each schema finding's INSTANCE_ID."""
    data, instances = flavored(path_pair, "pandas")
    violations = validation.validate_schema(data, SCHEMA, rdf_map_scope="EQ")
    enriched = validation.enrich(violations, rdf_map=SCHEMA)
    assert list(enriched["INSTANCE_ID"]) == list(violations["INSTANCE_ID"])
    assert list(enriched.loc[enriched["ID"] == HEADER, "INSTANCE_ID"]) == [instances["beta"]]


MAX_DESCRIPTION = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix md: <http://iec.ch/TC57/61970-552/ModelDescription/1#> .
md:FullModelShape a sh:NodeShape ; sh:targetClass md:FullModel ;
    sh:property [ sh:path md:Model.description ; sh:maxCount 1 ] .
"""


def test_enrich_twice_equals_enrich_once(path_pair, engine):
    """SHACL over both twins merges the two headers: two descriptions break maxCount 1
    on the shared ID, and enrich lists both values. Enriching the result again keeps
    every filled cell."""
    rdflib = pytest.importorskip("rdflib")
    data, _ = flavored(path_pair, engine)
    shapes = rdflib.Graph().parse(data=MAX_DESCRIPTION, format="turtle")
    once = validation.enrich(validation.validate_shacl(data, shapes, engine=engine), data=data)
    values = ", ".join(repr(TWINS[twin]["description"]) for twin in path_pair.order)
    assert list(once["TARGET"]) == [f"found 2 values: {values}"]
    pandas.testing.assert_frame_equal(validation.enrich(once, data=data), once, check_dtype=False)


def test_enrich_found_values_are_the_instance_values(path_pair):
    """beta writes Model.description twice: the maxOccurs finding lists beta's two values."""
    twice = cimxml("beta").replace(b"<md:Model.version>",
                                   b"<md:Model.description>beta again</md:Model.description>\n    <md:Model.version>")
    inputs = [named(TWINS[twin]["path"], twice if twin == "beta" else cimxml(twin)) for twin in path_pair.order]
    data = triplets.parse(inputs, engine="python_lxml_pandas")
    violations = validation.validate_schema(data, SCHEMA, rdf_map_scope="EQ")
    enriched = validation.enrich(violations[violations["ID"] == HEADER], data=data)
    found = enriched.loc[enriched["VIOLATION_TYPE"] == "xsd:maxOccurs", "TARGET"]
    assert list(found) == ["found 2 values: 'beta header', 'beta again'"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("order", ORDERS)
def test_locate_violations_finds_the_file_of_the_finding(kind, order, tmp_path):
    """Over both twins, the enriched header finding is located at beta's scenarioTime line."""
    pair_ = Pair(kind, order, tmp_path)
    _, _, enriched = enriched_header_finding(pair_, "pandas")
    located = validation.locate_violations(enriched, pair_.inputs())
    assert list(located["SOURCE_URI"]) == [pair_.uri["beta"]]
    assert "not-a-time" in located["SOURCE_SNIPPET"].iloc[0]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("order", ORDERS)
def test_locate_without_enrich_falls_back_to_the_first_definition(kind, order, tmp_path):
    """A schema finding not enriched names no file (INSTANCE_ID only): it is located at
    the first definition in read order, the first-loaded twin's header."""
    pair_ = Pair(kind, order, tmp_path)
    data, _ = flavored(pair_, "pandas")
    violations = validation.validate_schema(data, SCHEMA, rdf_map_scope="EQ")
    located = validation.locate_violations(violations[violations["ID"] == HEADER], pair_.inputs())
    assert list(located["SOURCE_URI"]) == [pair_.uri[pair_.order[0]]]


@pytest.mark.parametrize("order", ORDERS)
def test_sarif_names_the_file_of_the_finding(order, tmp_path):
    """Over both twins, without sources the SARIF result of the header finding names
    beta's file (the instance file of the enrichment)."""
    pair_ = Pair("paths", order, tmp_path)
    data, _ = flavored(pair_, "pandas")
    violations = validation.validate_schema(data, SCHEMA, rdf_map_scope="EQ")
    log = json.loads(validation.export_to_sarif(violations[violations["ID"] == HEADER], data=data,
                                                group=False, export_to_memory=True).getvalue())
    [result] = log["runs"][0]["results"]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == pair_.uri["beta"]


@pytest.mark.parametrize("order", ORDERS)
def test_sarif_puts_the_finding_in_its_file(order, tmp_path):
    """Over both twins, with data and sources the SARIF result of the header finding
    points at beta's scenarioTime line."""
    pair_ = Pair("paths", order, tmp_path)
    data, _ = flavored(pair_, "pandas")
    violations = validation.validate_schema(data, SCHEMA, rdf_map_scope="EQ")
    log = json.loads(validation.export_to_sarif(violations[violations["ID"] == HEADER], data=data,
                                                sources=pair_.inputs(), group=False, export_to_memory=True).getvalue())
    [result] = log["runs"][0]["results"]
    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == pair_.uri["beta"]
    assert "not-a-time" in location["region"]["snippet"]["text"]


SHAPES = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:SubstationShape a sh:NodeShape ; sh:targetClass cim:Substation ;
    sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .
"""


def test_shacl_findings_locate_in_their_own_files(pair, engine):
    """validate_shacl over both twins: each content object's finding is enriched with
    its own instance and located in its own file."""
    rdflib = pytest.importorskip("rdflib")
    data, instances = flavored(pair, engine)
    shapes = rdflib.Graph().parse(data=SHAPES, format="turtle")
    violations = validation.validate_shacl(data, shapes, engine=engine)
    enriched = validation.enrich(violations, data=data)
    located = validation.locate_violations(enriched, pair.inputs())
    by_object = {TWINS[twin]["object"]: twin for twin in TWINS}
    assert sorted(located["ID"]) == sorted(by_object)
    for object_id, instance, uri in zip(located["ID"], located["INSTANCE_ID"].astype(str), located["SOURCE_URI"]):
        twin = by_object[object_id]
        assert (instance, uri) == (instances[twin], pair.uri[twin])


# ── header helpers ──────────────────────────────────────────────────────────

def test_declared_profiles(pair, engine):
    data, instances = flavored(pair, engine)
    declared = cgmes_tools.declared_profiles(data, SCHEMA)
    assert declared == {instances["alpha"]: ["EQ"], instances["beta"]: ["SSH"]}


def test_get_loaded_profiles(pair, engine):
    """One declaration per twin, on its own INSTANCE_ID with its own label."""
    data, instances = flavored(pair, engine)
    profiles = pandas_frame(cgmes_tools.get_loaded_profiles(data))
    assert sorted(zip(profiles["INSTANCE_ID"].astype(str), profiles["label"], profiles["HEADER_ID"],
                      profiles["VALUE"])) == sorted(
        (instances[twin], pair.label[twin], HEADER, TWINS[twin]["profile"]) for twin in TWINS)


def test_dependency_on_a_shared_header_id_resolves_to_both_twins(path_pair, engine):
    """A part that depends on the shared ID cannot tell the twins apart: one edge to
    each, and its model holds both."""
    dependent = (cimxml("alpha").replace(HEADER.encode(), b"c0ffee00-0000-4000-8000-000000000001")
                 .replace(b"<md:Model.description>alpha header</md:Model.description>",
                          f'<md:Model.DependentOn rdf:resource="urn:uuid:{HEADER}"/>'.encode()))
    data = triplets.parse([*path_pair.inputs(), named("dependent.xml", dependent)], engine="python_lxml_pandas")
    instances = path_pair.instances(data)
    data = build_engine(engine, data)
    relations = pandas_frame(cgmes_tools.get_model_relations(data))
    assert set(relations["ID_TO"]) == {HEADER}
    assert sorted(relations["INSTANCE_ID_TO"].astype(str)) == sorted(instances.values())
    [model] = cgmes_tools.get_loaded_models(data).values()
    assert set(instances.values()) < set(pandas_frame(model)["INSTANCE_ID"].astype(str))


def test_get_loaded_model_parts_lists_both_twins(path_pair, engine):
    """One row per header instance, each with its own values."""
    data, instances = flavored(path_pair, engine)
    parts = pandas_frame(cgmes_tools.get_loaded_model_parts(data))
    assert sorted(zip(parts["INSTANCE_ID"].astype(str), parts["Model.description"])) == sorted(
        (instances[twin], TWINS[twin]["description"]) for twin in TWINS)


def test_get_loaded_models_one_model_per_twin(path_pair, engine):
    """Each twin is a root of its own: models are keyed by INSTANCE_ID."""
    data, instances = flavored(path_pair, engine)
    models = cgmes_tools.get_loaded_models(data)
    assert sorted(sorted(set(pandas_frame(parts)["INSTANCE_ID"].astype(str))) for parts in models.values()) == \
        sorted([instance] for instance in instances.values())


CGMES_NAMES = {"alpha": "20220615T2230Z_1D_ALPHA_EQ_001.xml", "beta": "20220616T0030Z_2D_BETA_SSH_002.xml"}


@pytest.mark.parametrize("flavor", ["pandas", "polars"])
def test_filename_metadata_round_trip_per_instance(flavor):
    """update_FullModel_from_filename stamps each twin's header from its own file name,
    update_filename_from_FullModel rebuilds each label from its own header."""
    data = triplets.parse([named(CGMES_NAMES[twin], cimxml(twin)) for twin in TWINS], engine="python_lxml_pandas")
    data = build_engine(flavor, data)
    updated = cgmes_tools.update_FullModel_from_filename(data, update=True)
    frame = pandas_frame(updated).astype({"INSTANCE_ID": str})
    label = dict(zip(distribution_labels(frame)["INSTANCE_ID"].astype(str), distribution_labels(frame)["VALUE"]))
    header = frame[(frame["ID"] == HEADER) & frame["KEY"].isin(["Model.modelingEntity", "Model.messageType"])]
    assert sorted((label[instance], key, value) for instance, key, value in
                  zip(header["INSTANCE_ID"], header["KEY"], header["VALUE"])) == sorted(
        [(CGMES_NAMES["alpha"], "Model.messageType", "EQ"), (CGMES_NAMES["alpha"], "Model.modelingEntity", "ALPHA"),
         (CGMES_NAMES["beta"], "Model.messageType", "SSH"), (CGMES_NAMES["beta"], "Model.modelingEntity", "BETA")])
    renamed = distribution_labels(pandas_frame(cgmes_tools.update_filename_from_FullModel(updated)))
    assert dict(zip(renamed["INSTANCE_ID"].astype(str), renamed["VALUE"])) == label


@pytest.mark.parametrize("flavor", ["pandas", "polars"])
def test_get_metadata_from_FullModel_reads_the_first_header(path_pair, flavor):
    """Over several instances the metadata is the first-loaded header's, as it is for
    files with distinct header IDs."""
    data = build_engine(flavor, parsed(path_pair))
    metadata = cgmes_tools.get_metadata_from_FullModel(data)
    assert set(metadata.items()) == header_rows(path_pair.order[0]) - {("Type", "FullModel")}


@pytest.mark.parametrize("flavor", ["pandas", "polars"])
def test_get_metadata_from_FullModel_per_instance(pair, flavor):
    """Read from one instance's rows, the header metadata is that twin's."""
    data = parsed(pair).astype({"INSTANCE_ID": str})
    for twin, instance in pair.instances(data).items():
        one = build_engine(flavor, data[data["INSTANCE_ID"] == instance].reset_index(drop=True))
        metadata = cgmes_tools.get_metadata_from_FullModel(one)
        assert set(metadata.items()) == header_rows(twin) - {("Type", "FullModel")}
