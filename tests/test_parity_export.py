"""Cross-engine / cross-input-flavor parity (and timing) for the EXPORT formats.

For each format we produce the in-memory output via every available variant (engine param
or input flavor) and require it to match the reference variant after **round-trip
canonicalisation** (re-parse XML / sort N-Quad lines / read CSV+Excel back / compare graph).
**No xfails** — divergences are real failures (e.g. P1: export_to_networkx on polars input).
Parity runs on the Svedala IGM model; the ``-m performance`` timing test runs on RealGrid.

Shared helpers live in ``tests/_parity.py``.
"""
from pathlib import Path

import pandas
import pytest

import triplets  # noqa: F401
from triplets import export
from triplets.export_schema import schemas

from _parity import (
    REALGRID_SKIP_REASON, REALGRID_ZIP, SKIP_REASON, SVEDALA_DIR, SVEDALA_FILES,
    canon_frame,
)

RDF_MAP = schemas.ENTSOE_CGMES_3_0_0_552_ED1
_META_TYPES = {"Distribution", "NamespaceMap", "FullModel"}


def _polars(df):
    import polars
    return polars.from_pandas(df)


# ── per-format canonicalisers → a comparable string ───────────────────────────
def _frame_str(df):
    cf = canon_frame(df)
    return "" if cf is None else cf.to_csv(index=False)


def _canon_cimxml(outputs):
    df = pandas.read_RDF(outputs)[["ID", "KEY", "VALUE"]]
    meta = set(df[(df["KEY"] == "Type") & (df["VALUE"].astype(str).isin(_META_TYPES))]["ID"])
    return _frame_str(df[~df["ID"].isin(meta)])


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
        },
        "needs": {"cython_pugixml": ["pyarrow", "triplets.export.cimxml_cython_pugixml"],
                  "cython_pugixml_polars_input": ["polars", "pyarrow", "triplets.export.cimxml_cython_pugixml"],
                  "python_lxml_polars_input": ["polars"]},
        "canon": _canon_cimxml,
    },
    "nquads": {
        "ref": "pandas",
        "variants": {
            "pandas": lambda d: export.export_to_nquads(d, rdf_map=RDF_MAP, engine="pandas", export_to_memory=True),
            "polars": lambda d: export.export_to_nquads(d, rdf_map=RDF_MAP, engine="polars", export_to_memory=True),
        },
        "needs": {"polars": ["polars"]},
        "canon": _canon_nquads,
    },
    "csv": {
        "ref": "pandas_input",
        "variants": {
            "pandas_input": lambda d: export.export_to_csv(d, export_to_memory=True),
            "polars_input": lambda d: export.export_to_csv(_polars(d), export_to_memory=True),
        },
        "needs": {"polars_input": ["polars"]},
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
    for f in from_polars:
        assert f.getvalue() == by_name[f.name]


def test_cimxml_datatypes_polars_routes_to_lxml(svedala_pandas):
    """datatypes=True on polars input auto-picks python_lxml and still works."""
    pytest.importorskip("polars")
    out = export.export_to_cimxml(_polars(svedala_pandas), rdf_map=RDF_MAP, datatypes=True,
                                  export_type="xml_per_instance", export_to_memory=True)
    assert out and b"rdf:datatype" in out[0].getvalue()


def test_cimxml_polars_input_parallel(svedala_pandas):
    """polars per-instance frames survive the ProcessPoolExecutor round-trip."""
    _require(FORMATS["cimxml"], "cython_pugixml", "cython_pugixml_polars_input")
    out = export.export_to_cimxml(_polars(svedala_pandas), rdf_map=RDF_MAP,
                                  engine="cython_pugixml", max_workers=2,
                                  export_type="xml_per_instance", export_to_memory=True)
    assert len(out) == svedala_pandas["INSTANCE_ID"].nunique()


def test_cimxml_threaded_matches_sequential(svedala_pandas):
    """threads>=2 hash-partitions objects across per-thread pugixml documents:
    content must be identical to the sequential build (order is hash-grouped)."""
    _require(FORMATS["cimxml"], "cython_pugixml")
    from triplets.export import get_cimxml_engine
    engine = get_cimxml_engine("cython_pugixml")[1]

    instance_id = svedala_pandas["INSTANCE_ID"].value_counts().index[0]
    instance = svedala_pandas[svedala_pandas["INSTANCE_ID"] == instance_id]
    seq = engine.generate_xml(instance, rdf_map=RDF_MAP, threads=0)
    par = engine.generate_xml(instance, rdf_map=RDF_MAP, threads=4)

    def canonical(document):
        bio = __import__("io").BytesIO(document["file"])
        bio.name = document["filename"]
        return _canon_cimxml([bio])

    assert canonical(seq) == canonical(par)
    # the FullModel header object stays first in the parallel output
    if b"FullModel" in seq["file"]:
        head = par["file"][:2000]
        assert b"FullModel" in head
