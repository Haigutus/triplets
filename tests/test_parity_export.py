"""Cross-engine / cross-input-flavor parity (and timing) for the EXPORT formats.

For each format we produce the in-memory output via every available variant (engine param
or input flavor) and require it to match the reference variant after **round-trip
canonicalisation** (re-parse XML / sort N-Quad lines / read CSV+Excel back / compare graph).
**No xfails** — divergences are real failures (e.g. P1: export_to_networkx on polars input).
Parity runs on the Svedala IGM model; the ``-m performance`` timing test runs on RealGrid.

Shared helpers live in ``tests/_parity.py``.
"""
import logging
from pathlib import Path

import pandas
import pytest

import triplets  # noqa: F401
from triplets import export
from triplets.export_schema import schemas
from triplets.iri import RDF_NS, load_rdf_map

from _parity import (
    REALGRID_SKIP_REASON, REALGRID_ZIP, SKIP_REASON, SVEDALA_DIR, SVEDALA_FILES,
    canon_frame,
)

RDF_MAP = schemas.ENTSOE_CGMES_3_0_0_552_ED1
_META_TYPES = {"Distribution", "NamespaceMap", "FullModel"}


def _polars(df):
    import polars
    return polars.from_pandas(df)


def _arrow(df):
    import pyarrow
    return pyarrow.Table.from_pandas(df, preserve_index=False)


# ── per-format canonicalisers → a comparable string ───────────────────────────
def _frame_str(df):
    cf = canon_frame(df)
    return "" if cf is None else cf.to_csv(index=False)


def _canon_triples(df):
    """Triples without the parser's metadata objects, which get new IDs on every parse."""
    df = df[["ID", "KEY", "VALUE"]]
    meta = set(df[(df["KEY"] == "Type") & (df["VALUE"].astype(str).isin(_META_TYPES))]["ID"])
    return _frame_str(df[~df["ID"].isin(meta)])


def _canon_cimxml(outputs):
    return _canon_triples(pandas.read_RDF(outputs))


def _canon_nquads(bio):
    bio.seek(0)
    return "\n".join(sorted(bio.getvalue().decode("utf-8").splitlines()))


def _canon_csv(outputs):
    parts = []
    for bio in sorted(outputs, key=lambda b: b.name):
        bio.seek(0)
        parts.append(bio.name + "\n" + _frame_str(pandas.read_csv(bio)))
    return "\n".join(parts)


def _canon_excel(outputs):
    parts = []
    for bio in sorted(outputs, key=lambda b: getattr(b, "name", "")):
        bio.seek(0)
        sheets = pandas.read_excel(bio, sheet_name=None)
        for name in sorted(sheets):
            parts.append(f"{getattr(bio, 'name', '')}::{name}\n" + _frame_str(sheets[name]))
    return "\n".join(parts)


def _canon_networkx(graph):
    nodes = sorted((str(n), sorted((str(k), str(v)) for k, v in d.items())) for n, d in graph.nodes(data=True))
    edges = sorted((str(u), str(v), sorted((str(k), str(val)) for k, val in d.items()))
                   for u, v, d in graph.edges(data=True))
    return repr((nodes, edges))


# ── format registry: variants (engine param / input flavor) + canon + deps ────
FORMATS = {
    "cimxml": {
        "ref": "python_lxml",
        "variants": {
            "python_lxml": lambda d: export.export_to_cimxml(d, rdf_map=RDF_MAP, engine="python_lxml", export_to_memory=True),
            "cython_pugixml": lambda d: export.export_to_cimxml(d, rdf_map=RDF_MAP, engine="cython_pugixml", export_to_memory=True),
            # polars input: cython consumes it directly (arrow large_utf8 via the
            # shared accessor), python_lxml converts to pandas — both must match
            "cython_pugixml_polars_input": lambda d: export.export_to_cimxml(_polars(d), rdf_map=RDF_MAP, engine="cython_pugixml", export_to_memory=True),
            "python_lxml_polars_input": lambda d: export.export_to_cimxml(_polars(d), rdf_map=RDF_MAP, engine="python_lxml", export_to_memory=True),
            "cython_pugixml_datatypes": lambda d: export.export_to_cimxml(d, rdf_map=RDF_MAP, engine="cython_pugixml", datatypes=True, export_to_memory=True),
            "arrow_input": lambda d: export.export_to_cimxml(_arrow(d), rdf_map=RDF_MAP, export_to_memory=True),
        },
        "needs": {"cython_pugixml": ["pyarrow", "triplets.export.cimxml_cython_pugixml"],
                  "cython_pugixml_datatypes": ["pyarrow", "triplets.export.cimxml_cython_pugixml"],
                  "cython_pugixml_polars_input": ["polars", "pyarrow", "triplets.export.cimxml_cython_pugixml"],
                  "arrow_input": ["pyarrow"],
                  "python_lxml_polars_input": ["polars"]},
        "canon": _canon_cimxml,
    },
    "nquads": {
        "ref": "pandas",
        "variants": {
            "pandas": lambda d: export.export_to_nquads(d, rdf_map=RDF_MAP, engine="pandas", export_to_memory=True),
            "polars": lambda d: export.export_to_nquads(d, rdf_map=RDF_MAP, engine="polars", export_to_memory=True),
            "arrow_input": lambda d: export.export_to_nquads(_arrow(d), rdf_map=RDF_MAP, export_to_memory=True),
            "reader_input": lambda d: export.export_to_nquads(_arrow(d).to_reader(), rdf_map=RDF_MAP, export_to_memory=True),
        },
        "needs": {"polars": ["polars"], "arrow_input": ["pyarrow"],
                  "reader_input": ["pyarrow", "polars"]},
        "canon": _canon_nquads,
    },
    "csv": {
        "ref": "pandas_input",
        "variants": {
            "pandas_input": lambda d: export.export_to_csv(d, export_to_memory=True),
            "polars_input": lambda d: export.export_to_csv(_polars(d), export_to_memory=True),
            "arrow_input": lambda d: export.export_to_csv(_arrow(d), export_to_memory=True),
        },
        "needs": {"polars_input": ["polars"], "arrow_input": ["pyarrow"]},
        "canon": _canon_csv,
    },
    "excel": {
        "ref": "pandas_input",
        "variants": {
            "pandas_input": lambda d: export.export_to_excel(d, export_to_memory=True),
            "polars_input": lambda d: export.export_to_excel(_polars(d), export_to_memory=True),
        },
        "needs": {"pandas_input": ["openpyxl"], "polars_input": ["openpyxl", "polars"]},
        "canon": _canon_excel,
    },
    "networkx": {
        "ref": "pandas_input",
        "variants": {
            "pandas_input": lambda d: export.export_to_networkx(d),
            "polars_input": lambda d: export.export_to_networkx(_polars(d)),
        },
        "needs": {"pandas_input": ["networkx"], "polars_input": ["networkx", "polars"]},
        "canon": _canon_networkx,
    },
}

# non-reference (format, variant) cells to compare against the format's reference
PARITY_PARAMS = [pytest.param(fmt, v, id=f"{fmt}-{v}")
                 for fmt, spec in FORMATS.items()
                 for v in spec["variants"] if v != spec["ref"]]


@pytest.fixture(scope="module")
def svedala_pandas():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


def _require(spec, *variants):
    for v in variants:
        for mod in spec["needs"].get(v, []):
            pytest.importorskip(mod)


# ── guard: every public export_to_* has a format entry ────────────────────────
def test_formats_cover_public_exports():
    public = {n[len("export_to_"):] for n in export.__all__ if n.startswith("export_to_")}
    assert set(FORMATS) == public, f"format registry {set(FORMATS)} != public exports {public}"


# ── Test 1: parity — each variant == the format's reference ───────────────────
@pytest.mark.parametrize("fmt,variant", PARITY_PARAMS)
def test_parity(svedala_pandas, fmt, variant):
    spec = FORMATS[fmt]
    _require(spec, spec["ref"], variant)

    ref = spec["canon"](spec["variants"][spec["ref"]](svedala_pandas.copy()))
    try:
        out = spec["canon"](spec["variants"][variant](svedala_pandas.copy()))
    except Exception as exc:                              # noqa: BLE001
        pytest.fail(f"{fmt}/{variant} raised: {type(exc).__name__}: {exc}")
    assert ref == out, f"{fmt}/{variant} output differs from {fmt}/{spec['ref']}"


# ── the belongsToContainer row stays out of CIM XML, as label does ────────────
@pytest.mark.parametrize("engine", ["python_lxml", "cython_pugixml"])
def test_cimxml_of_zipped_input_unchanged(tmp_path, engine):
    """A model parsed from a zip (Distribution belongsToContainer row) exports byte-identical to
    the same files parsed unzipped under the same names."""
    import io
    import zipfile
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    _require(FORMATS["cimxml"], engine)
    archive = tmp_path / "Svedala.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in SVEDALA_FILES:
            bundle.write(path, Path(path).name)
    members = []
    for path in SVEDALA_FILES:
        member = io.BytesIO(Path(path).read_bytes())
        member.name = Path(path).name
        members.append(member)

    def exported(data):
        files = export.export_to_cimxml(data, rdf_map=RDF_MAP, engine=engine, export_type="xml_per_instance",
                                        export_to_memory=True)
        return {file.name: file.getvalue() for file in files}

    zipped = pandas.read_RDF([str(archive)])
    assert set(zipped.loc[zipped["KEY"] == "belongsToContainer", "VALUE"]) == {"Svedala.zip"}
    assert exported(zipped) == exported(pandas.read_RDF(members))


# ── a schema namespace the file does not bind ─────────────────────────────────
def test_cimxml_schema_namespace_unbound_in_the_file():
    """minimal_cim.xml binds cim: to CIM16; exported against the CIM100 schema, both engines
    write the same expanded names — no element or attribute loses its namespace."""
    _require(FORMATS["cimxml"], "python_lxml", "cython_pugixml")
    data = pandas.read_RDF([str(Path(__file__).parent / "data" / "minimal_cim.xml")])

    def expanded(engine):
        file, = export.export_to_cimxml(data, rdf_map=RDF_MAP, engine=engine, export_type="xml_per_instance",
                                        export_to_memory=True)
        return _expanded(file)

    names = expanded("cython_pugixml")
    assert names == expanded("python_lxml")
    assert ("{http://iec.ch/TC57/CIM100#}Substation", [(f"{{{RDF_NS}}}ID", "_SS1")], "") in names
    assert all(name.startswith("{") for tag, attributes, _ in names for name in (tag, *dict(attributes)))


CIM100 = "http://iec.ch/TC57/CIM100#"


def _expanded(file):
    """Every element of an exported file: (expanded tag, expanded attributes, text)."""
    from lxml import etree
    return [(element.tag, sorted(element.attrib.items()), (element.text or "").strip())
            for element in etree.fromstring(file.getvalue()).iter()]


@pytest.mark.parametrize("namespace_map", [
    {"cim": CIM100}, {"r": RDF_NS, "cim": CIM100}, {"rdf": "http://other#", "cim": CIM100},
    {"": RDF_NS, "cim": CIM100}, {None: RDF_NS, "cim": CIM100}, {"rdf": RDF_NS, "": CIM100}, {"rdf": RDF_NS, None: CIM100},
], ids=["no-rdf", "rdf-as-r", "rdf-prefix-taken", "rdf-default", "rdf-default-none", "cim-default", "cim-default-none"])
def test_cimxml_rdf_names_under_any_namespace_map(namespace_map):
    """The root and the rdf: attribute are in the RDF namespace whatever the map binds: RDF left
    out, under another prefix, the rdf prefix on another namespace, or a default namespace
    (prefix "" or None) — which an attribute never takes."""
    _require(FORMATS["cimxml"], "python_lxml", "cython_pugixml")
    data = pandas.DataFrame([("b1", "Type", "Breaker", "i1"), ("b1", "IdentifiedObject.name", "B1", "i1")],
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])

    def expanded(engine):
        file, = export.export_to_cimxml(data, namespace_map=namespace_map, undefined_namespace=CIM100, engine=engine,
                                        export_type="xml_per_instance", export_to_memory=True)
        return _expanded(file)

    assert expanded("cython_pugixml") == expanded("python_lxml") == [
        (f"{{{RDF_NS}}}RDF", [], ""),
        (f"{{{CIM100}}}Breaker", [(f"{{{RDF_NS}}}about", "urn:uuid:b1")], ""),
        (f"{{{CIM100}}}IdentifiedObject.name", [], "B1")]


def test_cimxml_of_a_file_with_a_default_namespace(tmp_path):
    """A file with CIM as its default namespace (xmlns="…") exports with that declaration: both
    engines write the same expanded names, unprefixed."""
    _require(FORMATS["cimxml"], "python_lxml", "cython_pugixml")
    source = tmp_path / "default_namespace.xml"
    source.write_text(
        f'<rdf:RDF xmlns:rdf="{RDF_NS}" xmlns="{CIM100}" xmlns:md="http://iec.ch/TC57/61970-552/ModelDescription/1#">\n'
        '  <md:FullModel rdf:about="urn:uuid:m1">\n'
        '    <md:Model.profile>http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/3.0</md:Model.profile>\n'
        '  </md:FullModel>\n'
        '  <Substation rdf:ID="_s1"><IdentifiedObject.name>S1</IdentifiedObject.name></Substation>\n'
        '</rdf:RDF>\n')
    data = pandas.read_RDF([str(source)])
    files = {engine: export.export_to_cimxml(data, rdf_map=RDF_MAP, engine=engine, export_type="xml_per_instance",
                                             export_to_memory=True)[0]
             for engine in ("python_lxml", "cython_pugixml")}
    names = _expanded(files["cython_pugixml"])
    assert names == _expanded(files["python_lxml"])
    assert names[-2:] == [(f"{{{CIM100}}}Substation", [(f"{{{RDF_NS}}}ID", "_s1")], ""),
                          (f"{{{CIM100}}}IdentifiedObject.name", [], "S1")]
    for file in files.values():
        text = file.getvalue().decode()
        assert f'xmlns="{CIM100}"' in text and "<Substation " in text


def test_cimxml_generated_prefixes_are_numbered_by_use():
    """cython_pugixml numbers the generated prefixes in the order the namespaces are first
    written: a schema namespace that no row uses takes no number."""
    _require(FORMATS["cimxml"], "cython_pugixml")
    attrib = {"attribute": f"{{{RDF_NS}}}about", "value_prefix": "urn:uuid:"}
    schema = {"Unused": {"type": "Class", "namespace": "http://unused#", "attrib": attrib},
              "Breaker": {"type": "Class", "namespace": CIM100, "attrib": attrib},
              "Breaker.unused": {"type": "Attribute", "namespace": "http://unused2#"},
              "IdentifiedObject.name": {"type": "Attribute", "namespace": "http://names#"}}
    data = pandas.DataFrame([("b1", "Type", "Breaker", "i1"), ("b1", "IdentifiedObject.name", "B1", "i1")],
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    file, = export.export_to_cimxml(data, rdf_map=schema, namespace_map={"rdf": RDF_NS}, engine="cython_pugixml",
                                    export_type="xml_per_instance", export_to_memory=True)
    text = file.getvalue().decode()
    assert f'<rdf:RDF xmlns:rdf="{RDF_NS}" xmlns:ns0="{CIM100}" xmlns:ns1="http://names#">' in text
    assert '<ns0:Breaker rdf:about="urn:uuid:b1">' in text and "<ns1:IdentifiedObject.name>B1<" in text


# ── Test 2: timing on RealGrid (opt-in via -m performance) ────────────────────
TIMING_PARAMS = [pytest.param(fmt, v, id=f"{fmt}-{v}")
                 for fmt, spec in FORMATS.items() for v in spec["variants"]]


@pytest.mark.performance
@pytest.mark.benchmark(group="export")
@pytest.mark.parametrize("fmt,variant", TIMING_PARAMS)
def test_benchmark(benchmark, fmt, variant):
    if not Path(REALGRID_ZIP).exists():
        pytest.skip(REALGRID_SKIP_REASON)
    spec = FORMATS[fmt]
    _require(spec, variant)
    data = pandas.read_RDF([REALGRID_ZIP])
    producer = spec["variants"][variant]
    try:
        producer(data.copy())
    except Exception as exc:                              # noqa: BLE001
        pytest.skip(f"{fmt}/{variant} errors: {type(exc).__name__}")
    benchmark.extra_info.update({"format": fmt, "variant": variant})
    benchmark(lambda: producer(data.copy()))


# ── cimxml: polars-native path specifics ──────────────────────────────────────

def test_cimxml_cython_byte_identical_across_input_flavors(svedala_pandas):
    """Same data as pandas vs polars input → byte-identical cython XML output."""
    _require(FORMATS["cimxml"], "cython_pugixml", "cython_pugixml_polars_input")
    kwargs = dict(rdf_map=RDF_MAP, engine="cython_pugixml",
                  export_type="xml_per_instance", export_to_memory=True)
    from_pandas = export.export_to_cimxml(svedala_pandas, **kwargs)
    from_polars = export.export_to_cimxml(_polars(svedala_pandas), **kwargs)
    assert len(from_pandas) == len(from_polars)
    by_name = {f.name: f.getvalue() for f in from_pandas}
    assert all(b"rdf:datatype" not in xml for xml in by_name.values())     # datatypes=False writes none
    for f in from_polars:
        assert f.getvalue() == by_name[f.name]


def test_cimxml_datatypes_polars_stays_on_cython(svedala_pandas):
    """datatypes=True on polars input: engine="auto" writes the cython engine's bytes, typed."""
    _require(FORMATS["cimxml"], "cython_pugixml_polars_input")
    kwargs = dict(rdf_map=RDF_MAP, datatypes=True, export_type="xml_per_instance", export_to_memory=True)
    auto = export.export_to_cimxml(_polars(svedala_pandas), **kwargs)
    cython = {f.name: f.getvalue() for f in export.export_to_cimxml(_polars(svedala_pandas), engine="cython_pugixml", **kwargs)}
    assert {f.name: f.getvalue() for f in auto} == cython
    assert all(b"rdf:datatype" in xml for xml in cython.values())


def test_cimxml_polars_input_parallel(svedala_pandas):
    """polars per-instance frames survive the ProcessPoolExecutor round-trip."""
    _require(FORMATS["cimxml"], "cython_pugixml", "cython_pugixml_polars_input")
    out = export.export_to_cimxml(_polars(svedala_pandas), rdf_map=RDF_MAP,
                                  engine="cython_pugixml", max_workers=2,
                                  export_type="xml_per_instance", export_to_memory=True)
    assert len(out) == svedala_pandas["INSTANCE_ID"].nunique()


@pytest.mark.parametrize("engine, variant, to_input", [
    ("python_lxml", "python_lxml", lambda d: d),                       # fork workers
    ("cython_pugixml", "cython_pugixml_polars_input", _polars),        # spawn workers
])
def test_cimxml_parallel_dict_rdf_map_matches_sequential(svedala_pandas, engine, variant, to_input):
    """max_workers with an in-memory rdf_map dict → the sequential bytes."""
    _require(FORMATS["cimxml"], variant)
    data = to_input(svedala_pandas)
    kwargs = dict(rdf_map=load_rdf_map(RDF_MAP), engine=engine, datatypes=True,
                  export_type="xml_per_instance", export_to_memory=True)
    sequential = {f.name: f.getvalue() for f in export.export_to_cimxml(data, **kwargs)}
    parallel = {f.name: f.getvalue() for f in export.export_to_cimxml(data, max_workers=2, **kwargs)}
    assert parallel == sequential and len(sequential) == svedala_pandas["INSTANCE_ID"].nunique()


# ── cimxml without a schema: every name is undefined, so all of it is written ──
@pytest.mark.parametrize("engine", ["python_lxml", "cython_pugixml"])
def test_cimxml_without_schema_exports_everything(svedala_pandas, engine):
    """No rdf_map: export_undefined False, True and the default write the same full model
    under the undefined namespace, and it parses back to the input triples."""
    _require(FORMATS["cimxml"], engine)
    default, dropped, kept = (export.export_to_cimxml(svedala_pandas, engine=engine, export_type="xml_per_instance",
                                                      export_to_memory=True, **flag)
                              for flag in ({}, {"export_undefined": False}, {"export_undefined": True}))
    contents = [{file.name: file.getvalue() for file in files} for files in (default, dropped, kept)]
    assert contents[0] == contents[1] == contents[2]
    assert len(contents[0]) == svedala_pandas["INSTANCE_ID"].nunique()
    assert any(b"<triplets:IdentifiedObject.name>CL15</triplets:IdentifiedObject.name>" in xml
               for xml in contents[0].values())
    assert _canon_cimxml(dropped) == _canon_triples(svedala_pandas)


def test_cimxml_without_schema_warns_once(svedala_pandas, caplog):
    """One warning per export, not per instance; none when a schema is given."""
    with caplog.at_level(logging.WARNING, logger="triplets.export"):
        export.export_to_cimxml(svedala_pandas, export_to_memory=True)
    assert [record.name for record in caplog.records] == ["triplets.export"]
    assert "http://triplets#" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="triplets.export"):
        export.export_to_cimxml(svedala_pandas, rdf_map=RDF_MAP, export_to_memory=True)
    assert not caplog.records


def test_split_instances_skips_phantom_dictionary_groups(tmp_path):
    """A filtered arrow-parsed frame must not export empty phantom instances:
    ArrowDtype dictionary groupby yields every dictionary value (observed=True
    is Categorical-only), so _split_instances skips empty groups."""
    xml = ('<?xml version="1.0"?>\n'
           '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
           'xmlns:cim="http://iec.ch/TC57/CIM100#">\n'
           '  <cim:Breaker rdf:ID="_b1"><cim:IdentifiedObject.name>B</cim:IdentifiedObject.name></cim:Breaker>\n'
           '</rdf:RDF>\n')
    files = []
    for name in ("one.xml", "two.xml"):
        path = tmp_path / name
        path.write_text(xml)
        files.append(str(path))
    data = pandas.read_RDF(files)
    keep = str(data["INSTANCE_ID"].iloc[0])
    filtered = data[data["INSTANCE_ID"] == keep]
    assert str(filtered["INSTANCE_ID"].dtype).startswith("dictionary")   # the footgun dtype
    frames = list(export._split_instances(filtered))
    assert len(frames) == 1 and all(len(frame) for frame in frames)
