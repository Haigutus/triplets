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
REGISTRY_ENGINES = list(parser._REGISTRY.modules)


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


# ── string_type: every layout carries the same content, in the right schema ───
STRING_TYPES = ["utf8", "large_utf8", "string_view"]
ARROW_ENGINES = [e for e in AVAILABLE_ENGINES if e in parser._ARROW_ENGINES]


@pytest.mark.parametrize("engine", ARROW_ENGINES)
@pytest.mark.parametrize("string_type", STRING_TYPES)
def test_string_type_parity(minimal_cim, engine, string_type):
    pyarrow = pytest.importorskip("pyarrow")
    if string_type == "string_view" and not hasattr(pyarrow, "string_view"):
        pytest.skip("string_view needs pyarrow >= 16")
    ref = parse([minimal_cim], engine=REFERENCE[0], return_type=REFERENCE[1])
    out = parse([minimal_cim], engine=engine, return_type="arrow", string_type=string_type)
    target = {"utf8": pyarrow.string(), "large_utf8": pyarrow.large_string(),
              "string_view": pyarrow.string_view()}[string_type]
    assert out.schema.field("ID").type == target
    assert out.schema.field("VALUE").type == target
    assert frames_equal(_content(ref), _content(out)), f"{engine}/{string_type} content differs"


def test_string_type_auto_and_errors(minimal_cim):
    pyarrow = pytest.importorskip("pyarrow")
    # auto: arrow output keeps the stable utf8 contract
    out = parse([minimal_cim], return_type="arrow")
    assert out.schema.field("ID").type == pyarrow.string()
    # auto: polars output gets its native layout (zero-copy adoption)
    if hasattr(pyarrow, "string_view"):
        assert parser._resolve_string_type("auto", "polars") == "string_view"
    with pytest.raises(ValueError, match="Unknown string_type"):
        parse([minimal_cim], string_type="bogus")


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
