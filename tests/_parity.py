"""Shared helpers for the cross-engine parity harnesses (``tests/test_parity_*.py``).

pandas is the reference; other engines'/flavors' outputs are normalised to the pandas
output's data type and compared. Comparison is **numeric-aware** (numbers compared by
value, not string repr) and **null-normalised**. There are intentionally no xfails in the
harnesses — divergences surface as real failures to fix.
"""
import contextlib
import inspect
import io
from pathlib import Path

import pandas
import pytest

# ── datasets ──────────────────────────────────────────────────────────────────
SVEDALA_DIR = Path("test_data/relicapgrid/Instance/Grid/IGM_Svedala")
SVEDALA_FILES = [str(SVEDALA_DIR / name) for name in (
    "20220615T2230Z__Svedala_EQ_1.xml",
    "20220615T2230Z_2D_Svedala_SSH_1.xml",
    "20220615T2230Z_2D_Svedala_TP_1.xml",
    "20220615T2230Z_2D_Svedala_SV_1.xml",
)]
SKIP_REASON = "Svedala test data not available (needs git submodule)"

REALGRID_ZIP = "test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"
REALGRID_SKIP_REASON = "RealGrid test data not available"


# ── dynamic discovery (mirrors tests/test_api_surface.py) ─────────────────────
def implemented(module) -> set[str]:
    """Public functions defined in *module* (not imported into it)."""
    return {n for n, o in inspect.getmembers(module, inspect.isfunction)
            if not n.startswith("_") and o.__module__ == module.__name__}


# ── normalise any engine output to a comparable pandas frame ──────────────────
def to_pandas(obj):
    if obj is None:
        return None
    if type(obj).__name__ == "DuckDBPyRelation":
        return obj.df()
    if type(obj).__module__.startswith("polars"):
        return obj.to_pandas()
    if type(obj).__module__.startswith("pyarrow"):
        return obj.to_pandas()
    if isinstance(obj, pandas.Series):
        return obj.rename("VALUE").rename_axis("KEY").reset_index()
    if isinstance(obj, pandas.DataFrame):
        return obj
    return None


def is_frame_like(v):
    return (isinstance(v, (pandas.DataFrame, pandas.Series))
            or type(v).__module__.startswith("polars")
            or type(v).__name__ == "DuckDBPyRelation")


def _num_cell(v):
    """One canonical float repr so '1', '1.0', '1e-6' and '0.000001' compare equal across
    engines (pandas int/float, duckdb VARCHAR, polars Float64); non-numerics pass through.
    Per-cell because a VALUE column mixes numeric and text values."""
    if v == "":
        return v
    try:
        return repr(float(v))
    except (ValueError, TypeError):
        return v


def canon_frame(obj):
    """Normalise to a sorted, string-cast, numeric-aware pandas frame for comparison."""
    df = to_pandas(obj)
    if df is None:
        return None
    df = df.copy()
    df = df.reset_index() if df.index.name is not None else df.reset_index(drop=True)
    df.columns = [str(c) for c in df.columns]
    df = df.astype(str).replace({"nan": "", "None": "", "<NA>": "", "NaN": "", "NaT": ""})
    for c in df.columns:
        df[c] = df[c].map(_num_cell)
    if "VALUE" in df.columns:                       # engines differ on emitting null-VALUE rows
        df = df[df["VALUE"] != ""]
    df = df.reindex(sorted(df.columns), axis=1)
    return df.sort_values(by=list(df.columns)).reset_index(drop=True)


def frames_equal(a, b):
    fa, fb = canon_frame(a), canon_frame(b)
    if fa is None or fb is None:
        return fa is None and fb is None
    return list(fa.columns) == list(fb.columns) and fa.equals(fb)


def parity(ref, other) -> bool:
    """Is *other* equal to the pandas reference, compared in the reference's data type?
    Handles dict (scalars or frames), tuple, and frame-like outputs."""
    if isinstance(ref, dict):
        if not isinstance(other, dict) or set(ref) != set(other):
            return False
        if ref and all(is_frame_like(v) for v in ref.values()):
            return all(frames_equal(ref[k], other[k]) for k in ref)
        return {str(k): str(v) for k, v in ref.items()} == {str(k): str(v) for k, v in other.items()}
    if isinstance(ref, tuple):
        return isinstance(other, tuple) and len(ref) == len(other) and all(parity(r, o) for r, o in zip(ref, other))
    return frames_equal(ref, other)


def shape(obj):
    """Short description of an output for assertion messages."""
    df = to_pandas(obj)
    if df is not None:
        return f"{df.shape} cols={sorted(str(c) for c in df.columns)[:8]}"
    if isinstance(obj, dict):
        return f"dict(n={len(obj)})"
    return repr(obj)[:80]


def run_quiet(fn, *args, **kwargs):
    """Run fn muting stdout (e.g. print_triplets_diff / draw_relations)."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


# ── engine-flavor builders (from a pandas triplet frame) ──────────────────────
def make_duckdb(df, table_name="triplets"):
    duckdb = pytest.importorskip("duckdb")
    import triplets  # noqa: F401 — registers the duckdb accessor
    con = duckdb.connect()
    con.register("_src", df)
    con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM _src")
    con.unregister("_src")
    return con


def build_engine(engine, base):
    """A pandas triplet frame as pandas / polars / duckdb-connection."""
    if engine == "pandas":
        return base.copy()
    if engine == "polars":
        polars = pytest.importorskip("polars")
        return polars.from_pandas(base)
    if engine == "duckdb":
        return make_duckdb(base)
    raise ValueError(f"unknown engine: {engine}")
