"""Input-flavor parity (and timing) for cgmes_tools.

cgmes_tools is a single pandas implementation behind a ``@_pandas_boundary`` wrapper that
accepts any flavor (pandas / polars / pyarrow / duckdb) and converts the result back. So
"parity" here is **input-flavor invariance**: calling each data-function with pandas, polars
or duckdb input must give the same result. **No xfails** — a flavor crashing/differing where
pandas works is a boundary bug. If the pandas reference itself can't run on the data (an
unmet precondition, e.g. no ConformLoad for scale_load), the cell is skipped.

Shared helpers live in ``tests/_parity.py``.
"""
import pandas
import pytest

import triplets  # noqa: F401
import triplets.cgmes_tools as cgmes

from _parity import (
    REALGRID_SKIP_REASON, REALGRID_ZIP, SKIP_REASON, SVEDALA_DIR, SVEDALA_FILES,
    build_engine, parity, run_quiet, shape,
)

# discover the triplet-data functions; visualization helpers open a browser → excluded
DATA_FUNCTIONS = [f for f in cgmes.DATA_FUNCTIONS if not f.startswith("draw_")]


def make_context(df: pandas.DataFrame) -> dict:
    type_rows = df[df["KEY"] == "Type"]
    equip = type_rows[type_rows["VALUE"].isin(["ACLineSegment", "Breaker", "Disconnector", "PowerTransformer"])]
    equipment_id = str((equip if not equip.empty else type_rows)["ID"].iloc[0])
    return {
        "metadata": cgmes.get_metadata_from_FullModel(df),
        "models": cgmes.get_loaded_models(df),
        "equipment_id": equipment_id,
        "eic_type": "ACLineSegment",
        "setpoint": 100.0,
    }


# spec(data, ctx) -> raw output; data is the triplet set in the engine's flavor
CALL_SPECS = {
    "get_metadata_from_FullModel": lambda d, c: cgmes.get_metadata_from_FullModel(d),
    "update_FullModel_from_dict": lambda d, c: cgmes.update_FullModel_from_dict(d, c["metadata"]),
    "update_FullModel_from_filename": lambda d, c: cgmes.update_FullModel_from_filename(d),
    "update_filename_from_FullModel": lambda d, c: cgmes.update_filename_from_FullModel(d),
    "get_loaded_models": lambda d, c: cgmes.get_loaded_models(d),
    "get_model_triplets": lambda d, c: cgmes.get_model_triplets(d, c["models"]),
    "get_loaded_model_parts": lambda d, c: cgmes.get_loaded_model_parts(d),
    "get_EIC_to_mRID_map": lambda d, c: cgmes.get_EIC_to_mRID_map(d, c["eic_type"]),
    "get_GeneratingUnits": lambda d, c: cgmes.get_GeneratingUnits(d),
    "count_GeneratingUnit_types": lambda d, c: cgmes.count_GeneratingUnit_types(d),
    "get_limits": lambda d, c: cgmes.get_limits(d),
    "scale_load": lambda d, c: cgmes.scale_load(d, c["setpoint"]),
    "switch_equipment_terminals": lambda d, c: cgmes.switch_equipment_terminals(d, c["equipment_id"]),
    "get_dangling_references": lambda d, c: cgmes.get_dangling_references(d),
}


@pytest.fixture(scope="module")
def svedala_pandas():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


@pytest.fixture(scope="module")
def svedala_ctx(svedala_pandas):
    return make_context(svedala_pandas)


# ── guard: every triplet-data function (minus draw_*) has a call spec ─────────
def test_call_specs_cover_data_functions():
    expected = {f for f in cgmes.DATA_FUNCTIONS if not f.startswith("draw_")}
    assert set(CALL_SPECS) == expected, (
        f"missing: {sorted(expected - set(CALL_SPECS))}; stale: {sorted(set(CALL_SPECS) - expected)}")


# ── Test 1: input-flavor invariance — polars/duckdb == pandas ─────────────────
PARITY_PARAMS = [pytest.param(f, flavor, id=f"{f}-{flavor}")
                 for f in sorted(DATA_FUNCTIONS) for flavor in ("polars", "duckdb")]


@pytest.mark.parametrize("func,flavor", PARITY_PARAMS)
def test_parity(svedala_pandas, svedala_ctx, func, flavor):
    pytest.importorskip(flavor)
    spec = CALL_SPECS[func]

    try:
        ref = run_quiet(spec, svedala_pandas.copy(), svedala_ctx)
    except Exception as exc:                              # noqa: BLE001 — unmet precondition, not a parity bug
        pytest.skip(f"pandas reference can't run {func}: {type(exc).__name__}: {exc}")

    try:
        out = run_quiet(spec, build_engine(flavor, svedala_pandas), svedala_ctx)
    except Exception as exc:                              # noqa: BLE001
        pytest.fail(f"{flavor} input crashed {func} (pandas worked): {type(exc).__name__}: {exc}")

    assert parity(ref, out), (
        f"{flavor}-input {func} differs from pandas-input\n"
        f"  pandas: {type(ref).__name__} {shape(ref)}\n"
        f"  {flavor}: {type(out).__name__} {shape(out)}")


# ── Test 1b: native-path guard — polars input must NOT hit the pandas boundary ─
# Without this, a missing/buggy polars impl would silently fall back to _to_pandas
# and still pass test_parity, hiding that the native engine never ran.
@pytest.mark.parametrize("func", sorted(DATA_FUNCTIONS))
def test_polars_uses_native_engine(svedala_pandas, svedala_ctx, monkeypatch, func):
    pytest.importorskip("polars")
    spec = CALL_SPECS[func]
    try:
        run_quiet(spec, svedala_pandas.copy(), svedala_ctx)
    except Exception as exc:                              # noqa: BLE001 — unmet precondition
        pytest.skip(f"pandas reference can't run {func}: {type(exc).__name__}")

    calls = []
    real_to_pandas = cgmes._to_pandas
    monkeypatch.setattr(cgmes, "_to_pandas", lambda d: (calls.append(1), real_to_pandas(d))[1])
    run_quiet(spec, build_engine("polars", svedala_pandas), svedala_ctx)
    assert not calls, f"{func} on polars input fell back to the pandas boundary (_to_pandas called {len(calls)}x)"


# ── Test 1c: synthetic parity for functions Svedala can't exercise ────────────
# scale_load (needs ConformLoad), the filename round-trips (need CGMES-named labels)
# and get_model_triplets (the shared spec passes a dict) all skip on Svedala — cover
# them on a small hand-built dataset so the polars port can't silently regress.
@pytest.fixture
def synthetic_pandas():
    rows = []
    def add(i, k, v, inst): rows.append({"ID": i, "KEY": k, "VALUE": v, "INSTANCE_ID": inst})
    add("fm", "Type", "FullModel", "eq")
    for k, v in [("Model.scenarioTime", "20230101T0000Z"), ("Model.processType", "1D"),
                 ("Model.modelingEntity", "ENTSOE"), ("Model.messageType", "EQ"), ("Model.version", "1")]:
        add("fm", k, v, "eq")
    add("dist", "Type", "Distribution", "eq")
    add("dist", "label", "20230101T0000Z_1D_ENTSOE_EQ_001.xml", "eq")
    for cid, p, q in [("c1", "10", "5"), ("c2", "20", "8")]:
        add(cid, "Type", "ConformLoad", "ssh"); add(cid, "EnergyConsumer.p", p, "ssh"); add(cid, "EnergyConsumer.q", q, "ssh")
    add("n1", "Type", "NonConformLoad", "ssh"); add("n1", "EnergyConsumer.p", "3", "ssh"); add("n1", "EnergyConsumer.q", "1", "ssh")
    return pandas.DataFrame(rows)


SYNTHETIC_CALLS = {
    "scale_load": lambda d: cgmes.scale_load(d, 100.0),
    "update_FullModel_from_filename": lambda d: cgmes.update_FullModel_from_filename(d),
    "update_filename_from_FullModel": lambda d: cgmes.update_filename_from_FullModel(d),
}


@pytest.mark.parametrize("func", sorted(SYNTHETIC_CALLS))
def test_polars_parity_synthetic(synthetic_pandas, func):
    pytest.importorskip("polars")
    call = SYNTHETIC_CALLS[func]
    ref = run_quiet(call, synthetic_pandas.copy())
    out = run_quiet(call, build_engine("polars", synthetic_pandas))
    assert parity(ref, out), f"polars {func} differs from pandas on synthetic data"


def test_polars_parity_get_model_triplets(synthetic_pandas):
    polars = pytest.importorskip("polars")
    inst = pandas.DataFrame({"INSTANCE_ID": ["ssh"]})
    ref = cgmes.get_model_triplets(synthetic_pandas.copy(), inst)
    out = cgmes.get_model_triplets(build_engine("polars", synthetic_pandas), polars.from_pandas(inst))
    assert parity(ref, out), "polars get_model_triplets differs from pandas on synthetic data"


# ── Test 2: timing on RealGrid (opt-in via -m performance) ────────────────────
TIMING_PARAMS = [pytest.param(f, flavor, id=f"{f}-{flavor}")
                 for f in sorted(DATA_FUNCTIONS) for flavor in ("pandas", "polars", "duckdb")]


@pytest.mark.performance
@pytest.mark.benchmark(group="cgmes_tools")
@pytest.mark.parametrize("func,flavor", TIMING_PARAMS)
def test_benchmark(benchmark, func, flavor):
    from pathlib import Path
    if not Path(REALGRID_ZIP).exists():
        pytest.skip(REALGRID_SKIP_REASON)
    pytest.importorskip(flavor)
    base = pandas.read_RDF([REALGRID_ZIP])
    ctx = make_context(base)
    spec = CALL_SPECS[func]

    try:
        run_quiet(spec, build_engine(flavor, base), ctx)
    except Exception as exc:                              # noqa: BLE001
        pytest.skip(f"{flavor}.{func} errors: {type(exc).__name__}")

    benchmark.extra_info.update({"function": func, "flavor": flavor})
    benchmark(lambda: run_quiet(spec, build_engine(flavor, base), ctx))
