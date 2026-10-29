"""Cross-engine SHACL parity on real data: the sh:datatype lexical-form allowance
(`triplets:lexicalForm` Warnings are extra rows the vectorized engines add; they
must never lose a violation pyshacl reports), Svedala datatype / inheritance /
two-winding SPARQL target, input flavors, and the engine benchmarks. Synthetic
per-component cases live in test_parity_shacl_matrix.py.
"""
import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

import pandas
import triplets

from pathlib import Path

from _parity import SVEDALA_DIR, SKIP_REASON, frames_equal

SVEDALA_EQ = str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml")

DATATYPE_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ] .
"""


@pytest.fixture(scope="module")
def shape_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("shapes") / "datatype.ttl"
    path.write_text(DATATYPE_SHAPE)
    return str(path)


@pytest.fixture(scope="module")
def mixed_data():
    """Canonical float, integer-form float, and a broken value per object."""
    rows = [
        ("a1", "Type", "ACLineSegment", "eq"), ("a1", "Conductor.length", "10.5", "eq"),
        ("a2", "Type", "ACLineSegment", "eq"), ("a2", "Conductor.length", "1", "eq"),
        ("a3", "Type", "ACLineSegment", "eq"), ("a3", "Conductor.length", "abc", "eq"),
        ("a4", "Type", "ACLineSegment", "eq"), ("a4", "Conductor.length", "-2.5e3", "eq"),
    ]
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def _violation_set(violations):
    """Comparable set of Violation-severity datatype findings."""
    rows = violations[violations["VIOLATION_TYPE"] == "sh:datatype"]
    return set(zip(rows["ID"], rows["KEY"], rows["VALUE"]))


def test_datatype_parity_pyshacl_vs_pandas(mixed_data, shape_file):
    """Same sh:datatype violations; lexicalForm Warnings are documented extras."""
    from triplets.export_schema import schemas

    reference = triplets.validation.validate_shacl(mixed_data, shape_file, engine="pyshacl",
                                             rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope="*",
                                             lexical=False)
    ours = triplets.validation.validate_shacl(mixed_data, shape_file, engine="pandas")

    assert _violation_set(reference) == _violation_set(ours) == {("a3", "Conductor.length", "abc")}

    extras = ours[ours["VIOLATION_TYPE"] == "triplets:lexicalForm"]
    assert set(extras["VALUE"]) == {"1"}
    assert (extras["SEVERITY"] == "Warning").all()


def test_datatype_parity_on_real_data(shape_file):
    """Svedala EQ: typed pyshacl run and the pandas engine agree on sh:datatype."""
    if not Path(SVEDALA_EQ).exists():
        pytest.skip(SKIP_REASON)
    from triplets.export_schema import schemas

    data = pandas.read_RDF([SVEDALA_EQ])
    reference = triplets.validation.validate_shacl(data, shape_file, engine="pyshacl",
                                             rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope="*",
                                             lexical=False)
    ours = triplets.validation.validate_shacl(data, shape_file, engine="pandas")
    assert _violation_set(reference) == _violation_set(ours)


# ── timing: cross-engine + compile + real-profile (phase C baseline) ──────────
# Follows the test_parity_tools.py pattern: -m performance benchmarks grouped so
# engines line up in one table. These are the numbers the polars engine (phase C)
# must beat.
TIMING_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ; sh:maxCount 1 ] ;
    sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ; sh:minInclusive 0.0 ] ;
    sh:property [ sh:path cim:ACLineSegment.r ; sh:datatype xsd:float ] .
"""

CGMES_SHACL_DIR = Path("test_data/CGMES-AP/CGMES/SHACL")
CGMES_EQ_SHACL_FILES = [CGMES_SHACL_DIR / "61970-600-2_Equipment-AP-Con-Simple-SHACL.ttl",
                        CGMES_SHACL_DIR / "61970-301_Equipment-AP-Con-Complex-SHACL.ttl"]


@pytest.fixture(scope="module")
def svedala_eq():
    if not Path(SVEDALA_EQ).exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF([SVEDALA_EQ])


@pytest.fixture(scope="module")
def timing_shape(tmp_path_factory):
    path = tmp_path_factory.mktemp("shapes") / "timing.ttl"
    path.write_text(TIMING_SHAPE)
    return str(path)


@pytest.mark.performance
@pytest.mark.benchmark(group="shacl-engines")
@pytest.mark.parametrize("engine", ["pyshacl", "pandas", "polars", "duckdb"])
def test_benchmark_engines(benchmark, svedala_eq, timing_shape, engine):
    """Same shape + data per engine — pyshacl (reference) vs pandas (compiled IR)."""
    from triplets.export_schema import schemas
    compiled = triplets.validation.compile(timing_shape)
    benchmark.extra_info.update({"engine": engine})
    benchmark(lambda: triplets.validation.validate_shacl(
        svedala_eq, compiled, engine=engine,
        rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope="*", lexical=False))


@pytest.mark.performance
@pytest.mark.benchmark(group="shacl-engines")
def test_benchmark_pyshacl_oxigraph_store(benchmark, svedala_eq, timing_shape):
    """pyshacl with the data graph loaded through the oxigraph engine's store
    (store="oxigraph") — measured slower than Memory (pyshacl clones the graph
    into Memory regardless; the wrapper clone outweighs the Rust parse), which
    is why "memory" stays the engine default."""
    pytest.importorskip("pyoxigraph")
    pytest.importorskip("oxrdflib")
    from triplets.export_schema import schemas
    compiled = triplets.validation.compile(timing_shape)
    benchmark.extra_info.update({"engine": "pyshacl", "store": "oxigraph"})
    benchmark(lambda: triplets.validation.validate_shacl(
        svedala_eq, compiled, engine="pyshacl", store="oxigraph",
        rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope="*", lexical=False))


@pytest.mark.performance
@pytest.mark.benchmark(group="shacl-compile")
@pytest.mark.skipif(not all(f.exists() for f in CGMES_EQ_SHACL_FILES),
                    reason="external CGMES SHACL shapes not available")
def test_benchmark_compile(benchmark):
    """Shapes → IR on the real Equipment profiles (cache cleared — measures the parse)."""
    from triplets.validation.shacl_ir import _COMPILE_CACHE
    paths = [str(f) for f in CGMES_EQ_SHACL_FILES]
    benchmark.pedantic(lambda: triplets.validation.compile(paths),
                       setup=_COMPILE_CACHE.clear, rounds=3, iterations=1)


@pytest.mark.performance
@pytest.mark.benchmark(group="shacl-real-profile")
@pytest.mark.skipif(not all(f.exists() for f in CGMES_EQ_SHACL_FILES),
                    reason="external CGMES SHACL shapes not available")
@pytest.mark.parametrize("engine", ["pandas", "polars", "duckdb"])
def test_benchmark_real_profile_vectorized(benchmark, svedala_eq, engine):
    """Vectorized engines, real Simple+Complex Equipment profiles, sh:sparql
    excluded (rdflib sparql is minutes-scale — measured separately, and it's
    the qlever motivation, not a vectorization target)."""
    if engine != "pandas":
        pytest.importorskip(engine)
    from triplets.export_schema import schemas
    compiled = triplets.validation.compile([str(f) for f in CGMES_EQ_SHACL_FILES])
    vectorized = [c for c in compiled.ir["component"].unique() if c != "sh:sparql"]
    benchmark.extra_info.update({"ir_rows": len(compiled.ir), "engine": engine})
    benchmark.pedantic(
        lambda: triplets.validation.validate_shacl(svedala_eq, compiled, engine=engine,
                                             components=vectorized,
                                             rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1, rdf_map_scope="*"),
        rounds=2, iterations=1)


def test_input_flavor_parity(mixed_data, shape_file):
    """pandas engine gives identical findings for pandas / polars / duckdb input."""
    reference = triplets.validation.validate_shacl(mixed_data, shape_file, engine="pandas")

    polars = pytest.importorskip("polars")
    from_polars = triplets.validation.validate_shacl(polars.from_pandas(mixed_data), shape_file,
                                               engine="pandas")
    assert _violation_set(from_polars) == _violation_set(reference)

    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.register("src", mixed_data)
    con.execute("CREATE TABLE triplets AS SELECT * FROM src")
    from_duckdb = con.shacl.validate_shacl(shape_file, engine="pandas")
    assert _violation_set(from_duckdb) == _violation_set(reference)

    native_duckdb = con.shacl.validate_shacl(shape_file, engine="duckdb")   # SQL directly on the table
    assert _violation_set(native_duckdb) == _violation_set(reference)


# CGMES 61970-452 EQ shape on a parent class (RegulatingControl): with the
# schema inheritance it reaches every TapChangerControl, which never has a
# RegulatingCondEq — pyshacl (ont_graph) and the vectorized engines must agree
REGULATING_CONTROL_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix eq452: <http://iec.ch/TC57/ns/CIM/Equipment-EU/constraints/IEC61970-452/3.0#> .

eq452:RegulatingControl a sh:NodeShape ;
    sh:property eq452:RegulatingControl-RegulatingEquipment ;
    sh:targetClass cim:RegulatingControl .
eq452:RegulatingControl-RegulatingEquipment a sh:PropertyShape ;
    sh:path [ sh:inversePath cim:RegulatingCondEq.RegulatingControl ] ;
    sh:minCount 1 ;
    sh:severity sh:Violation .
"""


@pytest.mark.parametrize("inheritance", [True, False])
def test_inheritance_parity_on_real_data(svedala_eq, tmp_path, inheritance):
    """Svedala EQ: all engines report the same TapChangerControls with the
    inheritance walk, and none without it."""
    pytest.importorskip("polars")
    pytest.importorskip("duckdb")
    from triplets.export_schema import schemas

    path = tmp_path / "regulating_control.ttl"
    path.write_text(REGULATING_CONTROL_SHAPE)
    rdf_map = triplets.iri.load_rdf_map(schemas.ENTSOE_CGMES_3_0_0_552_ED1)
    found = {engine: set(triplets.validation.validate_shacl(
                 svedala_eq, str(path), engine=engine, rdf_map=rdf_map, rdf_map_scope=["EQ"],
                 inheritance=inheritance).query("VIOLATION_TYPE == 'sh:minCount'")["ID"])
             for engine in ("pyshacl", "pandas", "polars", "duckdb")}
    assert all(ids == found["pyshacl"] for ids in found.values()), {k: len(v) for k, v in found.items()}
    types = svedala_eq.query("KEY == 'Type'").set_index("ID")["VALUE"]
    if inheritance:
        assert found["pyshacl"] and set(types[list(found["pyshacl"])]) == {"TapChangerControl"}
    else:
        assert found["pyshacl"] == set()


# the CGMES Equipment two-winding SPARQL target over a constraint the Svedala
# names break: 51 of its 53 transformers have a name longer than 5 characters
TWO_WINDING_NAMES_SHAPE = '''
@prefix rdf:   <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix owl:   <http://www.w3.org/2002/07/owl#> .
@prefix sh:    <http://www.w3.org/ns/shacl#> .
@prefix cim:   <http://iec.ch/TC57/CIM100#> .
@prefix ex:    <http://example.org/#> .
@prefix xsd:   <http://www.w3.org/2001/XMLSchema#> .

cim:
    a           owl:Ontology ;
    sh:declare [ sh:namespace "http://iec.ch/TC57/CIM100#"^^xsd:anyURI ; sh:prefix "cim" ] ;
    sh:declare [ sh:namespace "http://www.w3.org/1999/02/22-rdf-syntax-ns#"^^xsd:anyURI ; sh:prefix "rdf" ] .

ex:TwoWindingNames
        a               sh:NodeShape ;
        sh:property     [ sh:path cim:IdentifiedObject.name ; sh:maxLength 5 ] ;
        sh:target
        [
              a sh:SPARQLTarget ;
              sh:prefixes cim: ;
              sh:select """
                SELECT DISTINCT ?this
                WHERE {
                  ?this rdf:type cim:PowerTransformer   .
                  FILTER NOT EXISTS {?this ^cim:PowerTransformerEnd.PowerTransformer/cim:TransformerEnd.endNumber 3}.
                }
                """ ;
        ] .
'''


def test_sparql_target_on_real_data(svedala_eq, tmp_path):
    """Svedala EQ: every engine reports the same 51 transformer names."""
    pytest.importorskip("polars")
    pytest.importorskip("duckdb")
    from triplets.export_schema import schemas

    path = tmp_path / "two_winding_names.ttl"
    path.write_text(TWO_WINDING_NAMES_SHAPE)
    found = {}
    for engine in ("pyshacl", "pandas", "polars", "duckdb"):
        violations = triplets.validation.validate_shacl(
            svedala_eq, str(path), engine=engine, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1,
            rdf_map_scope=["EQ"], lexical=False)
        assert set(violations["VIOLATION_TYPE"]) == {"sh:maxLength"}, engine
        meta = violations.attrs["validation"]
        assert (meta["constraints"], meta["skipped_shapes"]) == (1, []), engine
        found[engine] = set(violations["ID"])
    assert all(ids == found["pyshacl"] for ids in found.values()), {k: len(v) for k, v in found.items()}
    assert len(found["pyshacl"]) == 51


# ── CGMES 2.4: RealGrid against the 2.4 Enhanced SHACL ────────────────────────
# Each RealGrid instance on its own triplets against the Enhanced SHACL of its
# header's profile, exact types only (inheritance=False: the shapes list concrete
# classes in sh:in and close each concrete class on its own). Known artefacts:
# references into the other instances are absent (the TP / SV sh:class findings),
# and the 2 sh:in Info rows per profile are triplets' parser-metadata rows
# (NamespaceMap, Distribution) flagged by AllowedClasses-property.
# pyshacl runs on SV by default; SSH, TP and EQ under -m performance.
CGMES24_SHACL_DIR = Path("test_data/application-profiles-library-cgmes-v2-4/CGMES/Enhanced/SHACL")
CGMES24_PROFILES = {   # rdf_map section: (Model.profile of the RealGrid instance, shapes file)
    "EQ": ("http://entsoe.eu/CIM/EquipmentCore/3/1", "EquipmentCoreShortCircuitProfile.ttl"),
    "SSH": ("http://entsoe.eu/CIM/SteadyStateHypothesis/1/1", "SteadyStateHypothesisProfile.ttl"),
    "TP": ("http://entsoe.eu/CIM/Topology/4/1", "TopologyProfile.ttl"),
    "SV": ("http://entsoe.eu/CIM/StateVariables/4/1", "StateVariablesProfile.ttl"),
}
FINDING_COLUMNS = ["ID", "KEY", "VALUE", "VIOLATION_TYPE", "SOURCE_SHAPE", "SEVERITY"]


def _cgmes24_findings(realgrid_data, profile, engine):
    if not CGMES24_SHACL_DIR.exists():
        pytest.skip("application-profiles-library-cgmes-v2-4 submodule not checked out")
    from triplets.export_schema import schemas
    header, shapes = CGMES24_PROFILES[profile]
    instance = realgrid_data.query("KEY == 'Model.profile' and VALUE == @header")["INSTANCE_ID"].iloc[0]
    violations = triplets.validation.validate_shacl(
        realgrid_data[realgrid_data["INSTANCE_ID"] == instance], str(CGMES24_SHACL_DIR / shapes), engine=engine,
        rdf_map=schemas.ENTSOE_CGMES_2_4_15_552_ED1, rdf_map_scope=[profile], inheritance=False)
    return violations[FINDING_COLUMNS]


@pytest.mark.parametrize("profile", sorted(CGMES24_PROFILES))
def test_cgmes24_engine_parity(realgrid_data, profile):
    """pandas, polars and duckdb report the same findings on each RealGrid instance."""
    pytest.importorskip("polars")
    pytest.importorskip("duckdb")
    reference = _cgmes24_findings(realgrid_data, profile, "pandas")
    assert len(reference)
    for engine in ("polars", "duckdb"):
        assert frames_equal(reference, _cgmes24_findings(realgrid_data, profile, engine)), engine


@pytest.mark.parametrize("profile", [profile if profile == "SV" else pytest.param(profile, marks=pytest.mark.performance)
                                     for profile in sorted(CGMES24_PROFILES)])
def test_cgmes24_pyshacl_parity(realgrid_data, profile):
    """pyshacl and the pandas engine agree (lexicalForm Warnings, a triplets extra, aside)."""
    theirs, ours = (_cgmes24_findings(realgrid_data, profile, engine) for engine in ("pyshacl", "pandas"))
    assert frames_equal(*(found[found["VIOLATION_TYPE"] != "triplets:lexicalForm"] for found in (theirs, ours)))
