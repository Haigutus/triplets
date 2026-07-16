"""Per-method parity + performance harness across the tools engines.

Runs every triplet tool on the real Svedala IGM model through the pandas, polars
and duckdb engines, normalises each result to a comparable pandas table (pandas
is the reference), times each call (compute realised to pandas), and prints a
table: method | time per engine | output matches pandas?

Run:  uv run python examples/tool_engine_benchmark.py
"""
import contextlib
import io
import statistics
import time
from pathlib import Path

import pandas
import polars
import duckdb

import triplets  # noqa: F401 — registers engines

REPO = Path(__file__).resolve().parent.parent
FILES = sorted(str(p) for p in (REPO / "test_data/relicapgrid/Instance/Grid/IGM_Svedala").glob("*.xml"))
RUNS = 3

pdf = pandas.read_RDF(FILES)
TYPE = "ACLineSegment"
KEY = "IdentifiedObject.name"
ID = pdf[(pdf.KEY == "Type") & (pdf.VALUE == TYPE)]["ID"].iloc[0]
INSTANCES = list(pdf["INSTANCE_ID"].astype(str).unique())
SUBSET = pdf[(pdf.KEY == "Type") & (pdf.VALUE == TYPE)][["ID", "KEY", "VALUE"]]
NEW = pdf.iloc[100:]                       # diff target: first 100 rows "removed"
UPDATE = pandas.DataFrame({"ID": [ID, "NEWID"], "KEY": [KEY, "Type"], "VALUE": ["UPD", "NewClass"]})


# ── per-engine data factories (fresh objects so mutating tools stay isolated) ──
def d_pandas():
    return pdf.copy()


def d_polars():
    return polars.from_pandas(pdf)


def d_duckdb():
    con = duckdb.connect()
    con.register("src", pdf)
    con.execute("CREATE TABLE triplets AS SELECT * FROM src")
    con.unregister("src")
    return con


# ── normalise any engine output to a comparable pandas frame ──────────────────
def to_pandas(obj):
    if obj is None:
        return None
    cls = type(obj).__name__
    if cls == "DuckDBPyRelation":
        return obj.df()
    if type(obj).__module__.startswith("polars"):
        return obj.to_pandas()
    if isinstance(obj, pandas.Series):
        return obj.rename("VALUE").rename_axis("KEY").reset_index()
    if isinstance(obj, pandas.DataFrame):
        return obj
    return None


def canon(obj, kind):
    """Canonical comparable frame for a raw engine output."""
    if kind == "none":
        return None
    if kind == "dict_count":          # types_dict -> {name: count}
        return _items_frame(obj)
    if kind == "dict_frames":         # triplets_to_tableviews -> {type: frame}
        return _items_frame({k: len(to_pandas(v)) for k, v in obj.items()})
    if kind == "namespace":           # (dict, base) on pandas/duckdb, frame on polars
        if isinstance(obj, tuple):
            mapping = obj[0]
        else:
            frame = to_pandas(obj)
            mapping = dict(zip(frame.iloc[:, 0], frame.iloc[:, 1])) if frame is not None else {}
        return _items_frame(mapping)
    return _frame(to_pandas(obj))     # kind == "frame"


def _items_frame(mapping):
    rows = sorted((str(k), str(v)) for k, v in dict(mapping).items())
    return pandas.DataFrame(rows, columns=["k", "v"])


def _frame(df):
    if df is None:
        return None
    df = df.copy()
    if df.index.name is not None or not isinstance(df.index, pandas.RangeIndex):
        df = df.reset_index()
    df.columns = [str(c) for c in df.columns]
    df = df.astype(str).replace({"nan": "", "None": "", "<NA>": "", "NaN": "", "NaT": ""})
    df = df.reindex(sorted(df.columns), axis=1)
    return df.sort_values(by=list(df.columns)).reset_index(drop=True)


def matches(ref, other, kind):
    try:
        a, b = canon(ref, kind), canon(other, kind)
    except Exception as exc:                       # noqa: BLE001
        return f"ERR:{type(exc).__name__}"
    if a is None and b is None:
        return "n/a"
    if a is None or b is None or list(a.columns) != list(b.columns):
        return "DIFF"
    return "match" if a.equals(b) else "DIFF"


def time_call(fn, mutates):
    """Median ms over RUNS, realising the result to pandas (so lazy duckdb counts)."""
    samples = []
    last = None
    for _ in range(RUNS):
        try:
            with contextlib.redirect_stdout(io.StringIO()):   # mute print_triplets_diff
                start = time.perf_counter()
                out = fn()
                to_pandas(out)                                # realise compute
                samples.append((time.perf_counter() - start) * 1000)
            last = out
        except Exception as exc:                    # noqa: BLE001
            return None, f"ERR:{type(exc).__name__}"
    return statistics.median(samples), last


# ── method registry: name, kind, and a per-engine thunk ───────────────────────
# Each thunk takes a fresh engine data object and performs the call.
def _update_for(data):
    return polars.from_pandas(UPDATE) if type(data).__module__.startswith("polars") else UPDATE


def _subset_for(data):
    return polars.from_pandas(SUBSET) if type(data).__module__.startswith("polars") else SUBSET


def _new_for(data):
    return polars.from_pandas(NEW) if type(data).__module__.startswith("polars") else NEW


def _tv_to_triplets(data):
    # pandas/polars take a tableview; duckdb unpivots a wide table by name
    if isinstance(data, duckdb.DuckDBPyConnection):
        data.execute("CREATE OR REPLACE TABLE _tv AS SELECT * FROM (PIVOT "
                     "(SELECT d.ID,d.KEY,d.VALUE FROM triplets d JOIN "
                     "(SELECT DISTINCT ID FROM triplets WHERE KEY='Type' AND VALUE='ACLineSegment') t "
                     "ON d.ID=t.ID) ON KEY USING FIRST(VALUE) GROUP BY ID)")
        return data.tableview_to_triplets(table_name="_tv")
    return data.tableview_to_triplets(data.type_tableview(TYPE))


METHODS = [
    ("type_tableview", "frame", lambda d: d.type_tableview(TYPE, string_to_number=False)
        if not isinstance(d, duckdb.DuckDBPyConnection) else d.type_tableview(TYPE)),
    ("key_tableview", "frame", lambda d: d.key_tableview(KEY, string_to_number=False)
        if not isinstance(d, duckdb.DuckDBPyConnection) else d.key_tableview(KEY)),
    ("id_tableview", "frame", lambda d: d.id_tableview(ID, string_to_number=False)
        if not isinstance(d, duckdb.DuckDBPyConnection) else d.id_tableview(ID)),
    ("types_dict", "dict_count", lambda d: d.types_dict()),
    ("get_object_data", "frame", lambda d: d.get_object_data(ID)),
    ("get_namespace_map", "namespace", lambda d: d.get_namespace_map()),
    ("triplets_to_tableviews", "dict_frames", lambda d: d.triplets_to_tableviews()),
    ("filter_triplets", "frame", lambda d: d.filter_triplets(KEY="Type", VALUE=TYPE)),
    ("filter_triplets_by_type", "frame", lambda d: d.filter_triplets_by_type(TYPE)),
    ("filter_triplets_by_triplets", "frame", lambda d: d.filter_triplets_by_triplets(_subset_for(d))),
    ("references_to", "frame", lambda d: d.references_to(ID)),
    ("references_from", "frame", lambda d: d.references_from(ID)),
    ("references", "frame", lambda d: d.references(ID)),
    ("references_to_simple", "frame", lambda d: d.references_to_simple(ID)),
    ("references_from_simple", "frame", lambda d: d.references_from_simple(ID)),
    ("references_simple", "frame", lambda d: d.references_simple(ID)),
    ("references_all", "frame", lambda d: d.references_all()),
    ("diff_triplets", "frame", lambda d: d.diff_triplets(_new_for(d))),
    ("diff_triplets_by_instance", "frame", lambda d: d.diff_triplets_by_instance(INSTANCES[0], INSTANCES[1])),
    ("print_triplets_diff", "none", lambda d: d.print_triplets_diff(_new_for(d))),
    ("tableview_to_triplets", "frame", _tv_to_triplets),
    # mutating — result is the resulting triplet set
    ("set_value_at_key", "frame", lambda d: _result(d, d.set_value_at_key(KEY, "X"))),
    ("set_value_at_key_and_id", "frame", lambda d: _result(d, d.set_value_at_key_and_id(KEY, "X", ID))),
    ("update_triplets_from_triplets", "frame", lambda d: _result(d, d.update_triplets_from_triplets(_update_for(d)))),
    ("update_triplets_from_tableview", "frame",
        lambda d: _result(d, d.update_triplets_from_tableview(
            d.type_tableview(TYPE) if not isinstance(d, duckdb.DuckDBPyConnection)
            else d.type_tableview(TYPE).df()))),
    ("remove_triplets_from_triplets", "frame", lambda d: _result(d, d.remove_triplets_from_triplets(_subset_for(d)))),
]

MUTATING = {"set_value_at_key", "set_value_at_key_and_id", "update_triplets_from_triplets",
            "update_triplets_from_tableview", "remove_triplets_from_triplets"}


def _result(data, returned):
    """Resulting triplet set after a mutating call (engines differ on what they return)."""
    if isinstance(data, duckdb.DuckDBPyConnection):
        return data.sql("SELECT * FROM triplets")
    return returned if returned is not None else data


# ── run ───────────────────────────────────────────────────────────────────────
FACTORIES = {"pandas": d_pandas, "polars": d_polars, "duckdb": d_duckdb}
rows = []
print(f"Svedala IGM: {len(pdf):,} rows, {len(FILES)} files — median of {RUNS} runs\n")
for name, kind, thunk in METHODS:
    timings, outputs = {}, {}
    for engine, factory in FACTORIES.items():
        ms, out = time_call(lambda: thunk(factory()), name in MUTATING)
        timings[engine] = ms
        outputs[engine] = out
    ref = outputs["pandas"]
    ref_ok = not isinstance(ref, str)

    def parity(out):
        if isinstance(out, str):    # the engine itself errored
            return out
        if not ref_ok:              # pandas reference errored — nothing to compare to
            return "ref-err"
        return matches(ref, out, kind)

    rows.append({
        "method": name,
        "pandas_ms": timings["pandas"], "polars_ms": timings["polars"], "duckdb_ms": timings["duckdb"],
        "polars_vs_pandas": parity(outputs["polars"]),
        "duckdb_vs_pandas": parity(outputs["duckdb"]),
    })


def fmt(v):
    return "ERR" if v is None else f"{v:7.2f}"


hdr = f"{'method':32} {'pandas':>8} {'polars':>8} {'duckdb':>8}  {'polars=pd':>10} {'duckdb=pd':>10}"
print(hdr)
print("-" * len(hdr))
for r in rows:
    print(f"{r['method']:32} {fmt(r['pandas_ms'])} {fmt(r['polars_ms'])} {fmt(r['duckdb_ms'])}  "
          f"{str(r['polars_vs_pandas']):>10} {str(r['duckdb_vs_pandas']):>10}")
print("\ntimes are milliseconds (compute realised to pandas); pandas is the parity reference")
