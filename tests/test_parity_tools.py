"""Cross-engine parity + timing for the triplet TOOLS (pandas/polars/duckdb).

Tool functions are discovered dynamically; each engine's output is normalised to the
pandas output's data type and compared — pandas is the reference. **No xfails**: the
target is full parity, so any divergence is a real failure. Parity runs on the Svedala
IGM model; the ``-m performance`` timing test runs on RealGrid (~1.14M rows).

Shared discovery/normalisation/comparison helpers live in ``tests/_parity.py``.
"""
import re

import pandas
import pytest

import triplets  # noqa: F401 — registers engines/accessors
from triplets.tools import duckdb_engine, pandas_engine, polars_engine

from _parity import (
    SKIP_REASON, SVEDALA_DIR, SVEDALA_FILES,
    build_engine, implemented, parity, run_quiet, shape, to_pandas,
)

# ── dynamic discovery ─────────────────────────────────────────────────────────
ENGINE_MODULES = {"pandas": pandas_engine, "polars": polars_engine, "duckdb": duckdb_engine}
ALL_FUNCTIONS = set().union(*(implemented(m) for m in ENGINE_MODULES.values()))
MUTATING = {"set_value_at_key", "set_value_at_key_and_id", "update_triplets_from_triplets",
            "update_triplets_from_tableview", "remove_triplets_from_triplets"}


# ── dataset-derived call args (works on Svedala and RealGrid) ─────────────────
def make_context(df: pandas.DataFrame) -> dict:
    type_counts = df[df["KEY"] == "Type"]["VALUE"].value_counts()
    type_name = "ACLineSegment" if "ACLineSegment" in type_counts.index else str(type_counts.index[0])
    reference = str(df[(df["KEY"] == "Type") & (df["VALUE"] == type_name)]["ID"].iloc[0])
    key = "IdentifiedObject.name" if (df["KEY"] == "IdentifiedObject.name").any() else str(df["KEY"].iloc[0])
    name = str(df[df["KEY"] == key]["VALUE"].iloc[0])
    instances = list(df["INSTANCE_ID"].astype(str).unique())
    subset = df[(df["KEY"] == "Type") & (df["VALUE"] == type_name)][["ID", "KEY", "VALUE"]]
    update_data = pandas.DataFrame({"ID": [reference, "NEWID"], "KEY": [key, "Type"],
                                    "VALUE": ["UPDATED", "NewClass"]})
    return {"type": type_name, "key": key, "name": name, "id": reference, "reference": reference,
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


def _tv_kwargs(engine):
    return {} if engine == "duckdb" else {"string_to_number": False}


def _content_hash_spec(engine, data, ctx):
    """Digests are engine-specific by design (each engine uses its native
    row-hash primitive), so parity compares behaviour, not digests: shape,
    determinism, order-invariance (default), order-sensitivity (flag) and
    null != '' (a missing VALUE must not collide with an empty one)."""
    if engine == "duckdb":
        data.execute("CREATE OR REPLACE TABLE _reordered AS SELECT * FROM triplets"
                     " ORDER BY VALUE, KEY, ID, INSTANCE_ID")
        reordered = lambda **kw: data.content_hash(table_name="_reordered", **kw)  # noqa: E731
        for name, value in [("_null", "CAST(NULL AS VARCHAR)"), ("_empty", "''")]:
            data.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT"
                         f" 'a' AS ID, 'k' AS KEY, {value} AS VALUE, 'i' AS INSTANCE_ID")
        null_vs_empty = data.content_hash(table_name="_null") != data.content_hash(table_name="_empty")
    else:
        if engine == "polars":
            import polars
            reordered = data.sample(fraction=1.0, shuffle=True, seed=1).content_hash
            tiny = lambda v: polars.DataFrame(  # noqa: E731
                {"ID": ["a"], "KEY": ["k"], "VALUE": [v], "INSTANCE_ID": ["i"]},
                schema={c: polars.Utf8 for c in ("ID", "KEY", "VALUE", "INSTANCE_ID")})
        else:
            reordered = data.sample(frac=1, random_state=1).content_hash
            tiny = lambda v: pandas.DataFrame(  # noqa: E731
                {"ID": ["a"], "KEY": ["k"], "VALUE": [v], "INSTANCE_ID": ["i"]})
        null_vs_empty = tiny(None).content_hash() != tiny("").content_hash()
    return (len(data.content_hash()),
            data.content_hash() == data.content_hash(),
            data.content_hash() == reordered(),
            data.content_hash(order_sensitive=True) == reordered(order_sensitive=True),
            null_vs_empty)


# ── call specs: name -> fn(engine, data, ctx) -> raw output ───────────────────
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
    "filter_triplets_by_value": lambda e, d, c: d.filter_triplets_by_value(c["name"]),
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
    # tableview_to_triplets operates on a *tableview*, so call it on the tableview object
    # (duckdb has no relation method — it unpivots a wide table by name).
    "tableview_to_triplets": lambda e, d, c: (_duckdb_wide_table(d, c) if e == "duckdb"
                                              else d.type_tableview(c["type"]).tableview_to_triplets()),
    "set_value_at_key": lambda e, d, c: _result(e, d, d.set_value_at_key(c["key"], "X")),
    "set_value_at_key_and_id": lambda e, d, c: _result(e, d, d.set_value_at_key_and_id(c["key"], "X", c["id"])),
    "update_triplets_from_triplets": lambda e, d, c: _result(e, d, d.update_triplets_from_triplets(_to_engine(e, c["update_data"]))),
    "update_triplets_from_tableview": lambda e, d, c: _result(e, d, d.update_triplets_from_tableview(_tableview_arg(e, d, c))),
    "remove_triplets_from_triplets": lambda e, d, c: _result(e, d, d.remove_triplets_from_triplets(_to_engine(e, c["subset"]))),
    "content_hash": _content_hash_spec,
}


# ── regex variants: search semantics, like pandas str.contains ────────────────
# Kept out of CALL_SPECS so the coverage guard still checks exact function names.
# The escaped mid-substring of a real IdentifiedObject.name only matches under
# search semantics — a full-match engine (duckdb SIMILAR TO) would return nothing.
def _name_pattern(ctx):
    return re.escape(ctx["name"][1:-1] or ctx["name"])


EXTRA_SPECS = {
    "filter_triplets[regex]": lambda e, d, c: d.filter_triplets(KEY=c["key"], VALUE=_name_pattern(c), regex=True),
    "filter_triplets[regex-list]": lambda e, d, c: d.filter_triplets(VALUE=[_name_pattern(c), "^no-such-value$"], regex=True),
    "filter_triplets_by_value[regex]": lambda e, d, c: d.filter_triplets_by_value(_name_pattern(c), regex=True),
}
SPECS = {**CALL_SPECS, **EXTRA_SPECS}


# ── module-level dispatch: triplets.tools.<fn>(obj), all flavors ──────────────
# The accessor methods above bind the engine by object type; these go through
# tools._get_engine, which must route duckdb connections to the duckdb engine.
MODULE_DISPATCH_SPECS = {
    "types_dict": lambda e, d, c: triplets.tools.types_dict(d),
    "type_tableview": lambda e, d, c: triplets.tools.type_tableview(d, c["type"], **_tv_kwargs(e)),
    "filter_triplets": lambda e, d, c: triplets.tools.filter_triplets(d, KEY="Type", VALUE=c["type"]),
    "references_all": lambda e, d, c: triplets.tools.references_all(d),
}


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def svedala_pandas():
    if not SVEDALA_DIR.exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF(SVEDALA_FILES)


@pytest.fixture(scope="module")
def svedala_ctx(svedala_pandas):
    return make_context(svedala_pandas)


# ── coverage guard: every discovered function has a call spec ─────────────────
def test_call_specs_cover_all_functions():
    missing = ALL_FUNCTIONS - set(CALL_SPECS)
    extra = set(CALL_SPECS) - ALL_FUNCTIONS
    assert not missing and not extra, f"missing specs: {sorted(missing)}; stale specs: {sorted(extra)}"


# ── Test 1: parity (Svedala) — no xfails, mismatches FAIL ─────────────────────
PARITY_PARAMS = [pytest.param(f, e, id=f"{f}-{e}")
                 for f in sorted(SPECS) for e in ("polars", "duckdb")]


@pytest.mark.parametrize("func,engine", PARITY_PARAMS)
def test_parity(svedala_pandas, svedala_ctx, func, engine):
    pytest.importorskip(engine)
    spec = SPECS[func]

    try:
        ref = run_quiet(spec, "pandas", svedala_pandas.copy(), svedala_ctx)
    except Exception as exc:                              # noqa: BLE001
        pytest.fail(f"pandas reference raised for {func}: {type(exc).__name__}: {exc}")

    try:
        out = run_quiet(spec, engine, build_engine(engine, svedala_pandas), svedala_ctx)
    except Exception as exc:                              # noqa: BLE001
        pytest.fail(f"{engine}.{func} raised: {type(exc).__name__}: {exc}")

    assert parity(ref, out), (
        f"{engine}.{func} output differs from pandas\n"
        f"  pandas: {type(ref).__name__} {shape(ref)}\n"
        f"  {engine}: {type(out).__name__} {shape(out)}")


@pytest.mark.parametrize("func,engine", [pytest.param(f, e, id=f"{f}-{e}")
                                         for f in sorted(MODULE_DISPATCH_SPECS)
                                         for e in ("polars", "duckdb")])
def test_module_dispatch_parity(svedala_pandas, svedala_ctx, func, engine):
    pytest.importorskip(engine)
    spec = MODULE_DISPATCH_SPECS[func]
    ref = run_quiet(spec, "pandas", svedala_pandas.copy(), svedala_ctx)
    out = run_quiet(spec, engine, build_engine(engine, svedala_pandas), svedala_ctx)
    assert parity(ref, out), f"tools.{func}({engine}) differs from pandas"


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
        run_quiet(spec, engine, build_engine(engine, realgrid_data), ctx)
    except Exception as exc:                              # noqa: BLE001
        pytest.skip(f"{engine}.{func} errors: {type(exc).__name__}")

    benchmark.extra_info.update({"engine": engine, "function": func})

    def realise(data):
        to_pandas(run_quiet(lambda: spec(engine, data, ctx)))   # force lazy compute (+ mute stdout)

    if func in MUTATING:
        benchmark.pedantic(realise, setup=lambda: ((build_engine(engine, realgrid_data),), {}),
                           rounds=3, iterations=1)
    else:
        data = build_engine(engine, realgrid_data)
        benchmark(lambda: realise(data))
