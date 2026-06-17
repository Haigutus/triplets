"""Cross-engine + cross-return-type parity (and timing) for the PARSER.

Every available (engine × return_type) parses the same RDF/XML and must yield the same
triplet data as the reference (``python_lxml_pandas`` / ``pandas``). Outputs are normalised
to a pandas frame and compared (numeric-aware, null-normalised). **No xfails** — a divergence
is a real failure. Parity runs on the committed ``minimal_cim`` and (if present) the Svedala
IGM model; the ``-m performance`` timing test parses RealGrid (~1.14M rows).

Shared helpers live in ``tests/_parity.py``.
"""
from pathlib import Path

import pytest

import triplets.parser as parser
from triplets.parser import parse

from _parity import (
    REALGRID_SKIP_REASON, REALGRID_ZIP, SKIP_REASON, SVEDALA_DIR, SVEDALA_FILES,
    frames_equal, shape, to_pandas,
)

# Per-parse synthetic metadata: the parser mints a fresh INSTANCE_ID and random
# Distribution/NamespaceMap object IDs on every call, so those are not stable across
# parses. Parity compares the real parsed content — object triplets [ID, KEY, VALUE].
META_TYPES = {"Distribution", "NamespaceMap"}


def _content(obj):
    df = to_pandas(obj)[["ID", "KEY", "VALUE"]].copy()
    meta_ids = set(df[(df["KEY"] == "Type") & (df["VALUE"].astype(str).isin(META_TYPES))]["ID"])
    return df[~df["ID"].isin(meta_ids)]

# ── dynamic discovery: engines from the registry, return types from deps ──────
REGISTRY_ENGINES = list(parser._ENGINE_MODULES)


def _engine_available(name):
    try:
        parser.get_engine(name)
        return True
    except Exception:                                    # noqa: BLE001 — ImportError / build missing
        return False


AVAILABLE_ENGINES = [e for e in REGISTRY_ENGINES if _engine_available(e)]
SKIPPED_ENGINES = [e for e in REGISTRY_ENGINES if e not in AVAILABLE_ENGINES]

RETURN_TYPES = ["pandas"]
for _mod, _rt in (("pyarrow", "arrow"), ("polars", "polars")):
    try:
        __import__(_mod)
        RETURN_TYPES.append(_rt)
    except ImportError:
        pass

REFERENCE = ("python_lxml_pandas", "pandas")
PARITY_PARAMS = [pytest.param(e, r, id=f"{e}-{r}")
                 for e in AVAILABLE_ENGINES for r in RETURN_TYPES]


@pytest.fixture(params=["minimal", "svedala"])
def parser_files(request, minimal_cim):
    """A list of input paths to parse — committed minimal_cim, or the Svedala IGM."""
    if request.param == "minimal":
        return [minimal_cim]
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return SVEDALA_FILES


# ── guard: the matrix is built from the live registry (new engines auto-tested) ──
def test_engine_matrix_covers_registry():
    assert REFERENCE[0] in AVAILABLE_ENGINES, "reference engine must be available"
    assert set(AVAILABLE_ENGINES) | set(SKIPPED_ENGINES) == set(REGISTRY_ENGINES)
    assert PARITY_PARAMS, "no engine/return_type combinations to test"


# ── Test 1: parity — every (engine, return_type) == reference ─────────────────
@pytest.mark.parametrize("engine,return_type", PARITY_PARAMS)
def test_parity(parser_files, engine, return_type):
    ref = parse(parser_files, engine=REFERENCE[0], return_type=REFERENCE[1])
    out = parse(parser_files, engine=engine, return_type=return_type)
    assert frames_equal(_content(ref), _content(out)), (
        f"{engine}/{return_type} content differs from {REFERENCE[0]}/{REFERENCE[1]}\n"
        f"  reference: {shape(ref)}\n  {engine}/{return_type}: {shape(out)}")


# ── Test 2: timing on RealGrid (opt-in via -m performance) ────────────────────
TIMING_PARAMS = [pytest.param(e, r, id=f"{e}-{r}")
                 for e in AVAILABLE_ENGINES for r in RETURN_TYPES]


@pytest.mark.performance
@pytest.mark.benchmark(group="parse-realgrid")
@pytest.mark.parametrize("engine,return_type", TIMING_PARAMS)
def test_benchmark(benchmark, engine, return_type):
    if not Path(REALGRID_ZIP).exists():
        pytest.skip(REALGRID_SKIP_REASON)
    benchmark.extra_info.update({"engine": engine, "return_type": return_type})
    out = benchmark(lambda: parse([REALGRID_ZIP], engine=engine, return_type=return_type))
    assert len(to_pandas(out)) > 1_000_000
