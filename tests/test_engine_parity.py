"""Cross-engine parity + timing for the triplet tools.

Functions are discovered dynamically (same inspection as test_api_surface.py). For
every tool, each engine's output is normalised to the *pandas output's data type* and
compared — pandas is the reference. There are intentionally **no xfails**: the target is
full parity, so current divergences (polars bugs, references_*/diff_triplets/types_dict
differences, and the pandas tableview_to_triplets regression) show up as real failures
to be fixed later.

Parity runs on the Svedala IGM model (correctness). The timing test
(``-m performance``) runs on RealGrid (~1.14M rows), where engine differences matter.
"""
import contextlib
import inspect
import io
from pathlib import Path

import pandas
import pytest

import triplets  # noqa: F401 — registers engines/accessors
from triplets.tools import duckdb_engine, pandas_engine, polars_engine

# ── dynamic discovery (mirrors tests/test_api_surface.py) ─────────────────────
ENGINE_MODULES = {"pandas": pandas_engine, "polars": polars_engine, "duckdb": duckdb_engine}


def implemented(module) -> set[str]:
    return {n for n, o in inspect.getmembers(module, inspect.isfunction)
            if not n.startswith("_") and o.__module__ == module.__name__}


ALL_FUNCTIONS = set().union(*(implemented(m) for m in ENGINE_MODULES.values()))
MUTATING = {"set_value_at_key", "set_value_at_key_and_id", "update_triplets_from_triplets",
            "update_triplets_from_tableview", "remove_triplets_from_triplets"}

SVEDALA_DIR = Path("test_data/relicapgrid/Instance/Grid/IGM_Svedala")
SVEDALA_FILES = [
    str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SSH_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_TP_1.xml"),
    str(SVEDALA_DIR / "20220615T2230Z_2D_Svedala_SV_1.xml"),
]
SKIP_REASON = "Svedala test data not available (needs git submodule)"


# ── dataset-derived call args (works on Svedala and RealGrid) ─────────────────
def make_context(df: pandas.DataFrame) -> dict:
    type_counts = df[df["KEY"] == "Type"]["VALUE"].value_counts()
    type_name = "ACLineSegment" if "ACLineSegment" in type_counts.index else str(type_counts.index[0])
    type_ids = df[(df["KEY"] == "Type") & (df["VALUE"] == type_name)]["ID"]
    reference = str(type_ids.iloc[0])
    key = "IdentifiedObject.name" if (df["KEY"] == "IdentifiedObject.name").any() else str(df["KEY"].iloc[0])
    instances = list(df["INSTANCE_ID"].astype(str).unique())
    subset = df[(df["KEY"] == "Type") & (df["VALUE"] == type_name)][["ID", "KEY", "VALUE"]]
    update_data = pandas.DataFrame({"ID": [reference, "NEWID"], "KEY": [key, "Type"],
                                    "VALUE": ["UPDATED", "NewClass"]})
    return {"type": type_name, "key": key, "id": reference, "reference": reference,
            "instances": instances, "subset": subset, "update_data": update_data,
            "new_data": df.iloc[100:]}


# ── engine helpers ────────────────────────────────────────────────────────────
def _to_engine(engine, frame):
    """A triplet frame in the engine's native form (duckdb registers pandas directly)."""
    if engine == "polars":
        import polars
        return polars.from_pandas(frame)
    return frame


def _result(engine, data, returned):
    """Resulting triplet set after a mutating call (engines differ on what they return)."""
    if engine == "duckdb":
        return data.sql("SELECT * FROM triplets")
    return returned if returned is not None else data


def _tableview_arg(engine, data, ctx):
    tv = data.type_tableview(ctx["type"])
    return tv.df() if engine == "duckdb" else tv


def _duckdb_wide_table(data, ctx):
    data.execute(
        "CREATE OR REPLACE TABLE _tv AS SELECT * FROM (PIVOT "
        "(SELECT x.ID, x.KEY, x.VALUE FROM triplets x JOIN "
        f"(SELECT DISTINCT ID FROM triplets WHERE KEY='Type' AND VALUE='{ctx['type']}') t "
        "ON x.ID = t.ID) ON KEY USING FIRST(VALUE) GROUP BY ID)")
    return data.tableview_to_triplets(table_name="_tv")


# ── call specs: name -> fn(engine, data, ctx) -> raw output ───────────────────
def _tv_kwargs(engine):
    return {} if engine == "duckdb" else {"string_to_number": False}


CALL_SPECS = {
    "type_tableview": lambda e, d, c: d.type_tableview(c["type"], **_tv_kwargs(e)),
    "key_tableview": lambda e, d, c: d.key_tableview(c["key"], **_tv_kwargs(e)),
    "id_tableview": lambda e, d, c: d.id_tableview(c["id"], **_tv_kwargs(e)),
    "types_dict": lambda e, d, c: d.types_dict(),
    "get_object_data": lambda e, d, c: d.get_object_data(c["id"]),
    "get_namespace_map": lambda e, d, c: d.get_namespace_map(),
    "triplets_to_tableviews": lambda e, d, c: d.triplets_to_tableviews(),
    "filter_triplets": lambda e, d, c: d.filter_triplets(KEY="Type", VALUE=c["type"]),
    "filter_triplets_by_type": lambda e, d, c: d.filter_triplets_by_type(c["type"]),
    "filter_triplets_by_triplets": lambda e, d, c: d.filter_triplets_by_triplets(_to_engine(e, c["subset"])),
    "references_to": lambda e, d, c: d.references_to(c["reference"]),
    "references_from": lambda e, d, c: d.references_from(c["reference"]),
    "references": lambda e, d, c: d.references(c["reference"]),
    "references_to_simple": lambda e, d, c: d.references_to_simple(c["reference"]),
    "references_from_simple": lambda e, d, c: d.references_from_simple(c["reference"]),
    "references_simple": lambda e, d, c: d.references_simple(c["reference"]),
    "references_all": lambda e, d, c: d.references_all(),
    "diff_triplets": lambda e, d, c: d.diff_triplets(_to_engine(e, c["new_data"])),
    "diff_triplets_by_instance": lambda e, d, c: d.diff_triplets_by_instance(c["instances"][0], c["instances"][1]),
    "print_triplets_diff": lambda e, d, c: d.print_triplets_diff(_to_engine(e, c["new_data"])),
    # tableview_to_triplets operates on a *tableview*, so call it on the tableview
    # object (duckdb has no relation method — it unpivots a wide table by name).
    "tableview_to_triplets": lambda e, d, c: (_duckdb_wide_table(d, c) if e == "duckdb"
                                              else d.type_tableview(c["type"]).tableview_to_triplets()),
    "set_value_at_key": lambda e, d, c: _result(e, d, d.set_value_at_key(c["key"], "X")),
    "set_value_at_key_and_id": lambda e, d, c: _result(e, d, d.set_value_at_key_and_id(c["key"], "X", c["id"])),
    "update_triplets_from_triplets": lambda e, d, c: _result(e, d, d.update_triplets_from_triplets(_to_engine(e, c["update_data"]))),
    "update_triplets_from_tableview": lambda e, d, c: _result(e, d, d.update_triplets_from_tableview(_tableview_arg(e, d, c))),
    "remove_triplets_from_triplets": lambda e, d, c: _result(e, d, d.remove_triplets_from_triplets(_to_engine(e, c["subset"]))),
}


# ── normalise to the pandas output's data type, then compare ──────────────────
def _to_pandas(obj):
    if obj is None:
        return None
    if type(obj).__name__ == "DuckDBPyRelation":
        return obj.df()
    if type(obj).__module__.startswith("polars"):
        return obj.to_pandas()
    if isinstance(obj, pandas.Series):
        return obj.rename("VALUE").rename_axis("KEY").reset_index()
    if isinstance(obj, pandas.DataFrame):
        return obj
    return None


def _is_frame_like(v):
    return (isinstance(v, (pandas.DataFrame, pandas.Series))
            or type(v).__module__.startswith("polars")
            or type(v).__name__ == "DuckDBPyRelation")


def _canon_frame(obj):
    df = _to_pandas(obj)
    if df is None:
        return None
    df = df.copy()
    df = df.reset_index() if df.index.name is not None else df.reset_index(drop=True)
    df.columns = [str(c) for c in df.columns]
    df = df.astype(str).replace({"nan": "", "None": "", "<NA>": "", "NaN": "", "NaT": ""})
    df = df.reindex(sorted(df.columns), axis=1)
    return df.sort_values(by=list(df.columns)).reset_index(drop=True)


def _frames_equal(a, b):
    fa, fb = _canon_frame(a), _canon_frame(b)
    if fa is None or fb is None:
        return fa is None and fb is None
    return list(fa.columns) == list(fb.columns) and fa.equals(fb)


def parity(ref, other) -> bool:
    """Is *other* equal to the pandas reference, compared in the reference's data type?"""
    if isinstance(ref, dict):
        if not isinstance(other, dict) or set(ref) != set(other):
            return False
        if ref and all(_is_frame_like(v) for v in ref.values()):
            return all(_frames_equal(ref[k], other[k]) for k in ref)
        return {str(k): str(v) for k, v in ref.items()} == {str(k): str(v) for k, v in other.items()}
    if isinstance(ref, tuple):
        return isinstance(other, tuple) and len(ref) == len(other) and all(parity(r, o) for r, o in zip(ref, other))
    return _frames_equal(ref, other)


def _run(spec, engine, data, ctx):
    with contextlib.redirect_stdout(io.StringIO()):       # mute print_triplets_diff
        return spec(engine, data, ctx)


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def svedala_pandas():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


@pytest.fixture(scope="module")
def svedala_ctx(svedala_pandas):
    return make_context(svedala_pandas)


def _make_duckdb(df):
    duckdb = pytest.importorskip("duckdb")
    import triplets  # noqa: F401
    con = duckdb.connect()
    con.register("_src", df)
    con.execute("CREATE TABLE triplets AS SELECT * FROM _src")
    con.unregister("_src")
    return con


def _build(engine, base):
    if engine == "pandas":
        return base.copy()
    if engine == "polars":
        polars = pytest.importorskip("polars")
        return polars.from_pandas(base)
    return _make_duckdb(base)


# ── coverage guard: every discovered function has a call spec ─────────────────
def test_call_specs_cover_all_functions():
    missing = ALL_FUNCTIONS - set(CALL_SPECS)
    extra = set(CALL_SPECS) - ALL_FUNCTIONS
    assert not missing and not extra, f"missing specs: {sorted(missing)}; stale specs: {sorted(extra)}"


# ── Test 1: parity (Svedala) — no xfails, mismatches FAIL ─────────────────────
PARITY_PARAMS = [pytest.param(f, e, id=f"{f}-{e}")
                 for f in sorted(ALL_FUNCTIONS) for e in ("polars", "duckdb")]


@pytest.mark.parametrize("func,engine", PARITY_PARAMS)
def test_parity(svedala_pandas, svedala_ctx, func, engine):
    pytest.importorskip(engine)
    spec = CALL_SPECS[func]

    try:
        ref = _run(spec, "pandas", svedala_pandas.copy(), svedala_ctx)
    except Exception as exc:                              # noqa: BLE001
        pytest.fail(f"pandas reference raised for {func}: {type(exc).__name__}: {exc}")

    try:
        out = _run(spec, engine, _build(engine, svedala_pandas), svedala_ctx)
    except Exception as exc:                              # noqa: BLE001
        pytest.fail(f"{engine}.{func} raised: {type(exc).__name__}: {exc}")

    assert parity(ref, out), (
        f"{engine}.{func} output differs from pandas\n"
        f"  pandas: {type(ref).__name__} {_shape(ref)}\n"
        f"  {engine}: {type(out).__name__} {_shape(out)}")


def _shape(obj):
    df = _to_pandas(obj)
    if df is not None:
        return f"{df.shape} cols={sorted(str(c) for c in df.columns)[:8]}"
    if isinstance(obj, dict):
        return f"dict(n={len(obj)})"
    return repr(obj)[:80]


# ── Test 2: timing on RealGrid (opt-in via -m performance) ────────────────────
TIMING_PARAMS = [pytest.param(f, e, id=f"{f}-{e}")
                 for f in sorted(ALL_FUNCTIONS) for e in ("pandas", "polars", "duckdb")]


@pytest.mark.performance
@pytest.mark.benchmark(group="engine-tools")
@pytest.mark.parametrize("func,engine", TIMING_PARAMS)
def test_benchmark(benchmark, realgrid_data, func, engine):
    pytest.importorskip(engine)
    ctx = make_context(realgrid_data)
    spec = CALL_SPECS[func]

    # skip cells that crash on this engine — timing can't measure a crash
    try:
        _run(spec, engine, _build(engine, realgrid_data), ctx)
    except Exception as exc:                              # noqa: BLE001
        pytest.skip(f"{engine}.{func} errors: {type(exc).__name__}")

    benchmark.extra_info.update({"engine": engine, "function": func})

    def realise(data):
        _to_pandas(_run(spec, engine, data, ctx))         # force lazy compute

    if func in MUTATING:
        benchmark.pedantic(realise, setup=lambda: ((_build(engine, realgrid_data),), {}),
                           rounds=3, iterations=1)
    else:
        data = _build(engine, realgrid_data)
        benchmark(lambda: realise(data))
