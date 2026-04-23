"""Benchmark pandas vs Polars query performance on triplet data."""
import sys
import time
import gc
sys.path.insert(0, ".")

from triplets import rdf_parser
from triplets import polars_queries as plq
import polars as pl
import pandas


def bench(name, fn, runs=5, warmup=1):
    """Benchmark a function, return avg time in ms."""
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(runs):
        gc.collect(); gc.disable()
        t0 = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - t0
        gc.enable()
        times.append(elapsed)
    avg = sum(times) / len(times)
    return avg, result


def main():
    import os
    path = os.path.abspath("test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip")

    print("Loading data...")
    pd_data = rdf_parser.load_all_to_dataframe([path])

    # Convert to Polars (via Arrow for zero-copy)
    import pyarrow as pa
    pl_data = pl.from_pandas(pd_data)

    print(f"Rows: {len(pd_data):,} (pandas), {pl_data.height:,} (polars)")

    # Find a valid reference ID for reference queries
    substations = pd_data[(pd_data["KEY"] == "Type") & (pd_data["VALUE"] == "Substation")]
    ref_id = substations["ID"].iloc[0] if not substations.empty else None
    print(f"Reference ID for tests: {ref_id}")
    print()

    runs = 10

    results = []

    # ── type_tableview ──────────────────────────────────────────────────
    type_name = "ACLineSegment"

    pd_time, pd_res = bench(
        "type_tableview [pandas]",
        lambda: rdf_parser.type_tableview(pd_data, type_name),
        runs=runs,
    )
    pl_time, pl_res = bench(
        "type_tableview [polars]",
        lambda: plq.type_tableview(pl_data, type_name),
        runs=runs,
    )
    pd_rows = len(pd_res) if pd_res is not None else 0
    pl_rows = pl_res.height if pl_res is not None else 0
    results.append(("type_tableview", type_name, pd_time, pl_time, pd_rows, pl_rows))

    # ── key_tableview ───────────────────────────────────────────────────
    key_name = "IdentifiedObject.name"

    pd_time, pd_res = bench(
        "key_tableview [pandas]",
        lambda: rdf_parser.key_tableview(pd_data, key_name),
        runs=runs,
    )
    pl_time, pl_res = bench(
        "key_tableview [polars]",
        lambda: plq.key_tableview(pl_data, key_name),
        runs=runs,
    )
    pd_rows = len(pd_res) if pd_res is not None else 0
    pl_rows = pl_res.height if pl_res is not None else 0
    results.append(("key_tableview", key_name, pd_time, pl_time, pd_rows, pl_rows))

    # ── filter_by_type ──────────────────────────────────────────────────
    pd_time, pd_res = bench(
        "filter_by_type [pandas]",
        lambda: rdf_parser.filter_by_type(pd_data, type_name),
        runs=runs,
    )
    pl_time, pl_res = bench(
        "filter_by_type [polars]",
        lambda: plq.filter_by_type(pl_data, type_name),
        runs=runs,
    )
    results.append(("filter_by_type", type_name, pd_time, pl_time, len(pd_res), pl_res.height))

    # ── references_to ───────────────────────────────────────────────────
    if ref_id:
        pd_time, pd_res = bench(
            "references_to [pandas]",
            lambda: rdf_parser.references_to(pd_data, ref_id, levels=1),
            runs=runs,
        )
        pl_time, pl_res = bench(
            "references_to [polars]",
            lambda: plq.references_to(pl_data, ref_id, levels=1),
            runs=runs,
        )
        results.append(("references_to", ref_id[:12], pd_time, pl_time, len(pd_res), pl_res.height))

    # ── references_from ─────────────────────────────────────────────────
    if ref_id:
        pd_time, pd_res = bench(
            "references_from [pandas]",
            lambda: rdf_parser.references_from(pd_data, ref_id, levels=1),
            runs=runs,
        )
        pl_time, pl_res = bench(
            "references_from [polars]",
            lambda: plq.references_from(pl_data, ref_id, levels=1),
            runs=runs,
        )
        results.append(("references_from", ref_id[:12], pd_time, pl_time, len(pd_res), pl_res.height))

    # ── references_all ──────────────────────────────────────────────────
    pd_time, pd_res = bench(
        "references_all [pandas]",
        lambda: rdf_parser.references_all(pd_data),
        runs=5,
    )
    pl_time, pl_res = bench(
        "references_all [polars]",
        lambda: plq.references_all(pl_data),
        runs=5,
    )
    results.append(("references_all", "", pd_time, pl_time, len(pd_res), pl_res.height))

    # ── id_tableview ────────────────────────────────────────────────────
    if ref_id:
        pd_time, pd_res = bench(
            "id_tableview [pandas]",
            lambda: rdf_parser.id_tableview(pd_data, ref_id),
            runs=runs,
        )
        pl_time, pl_res = bench(
            "id_tableview [polars]",
            lambda: plq.id_tableview(pl_data, ref_id),
            runs=runs,
        )
        pd_rows = len(pd_res) if pd_res is not None else 0
        pl_rows = pl_res.height if pl_res is not None else 0
        results.append(("id_tableview", ref_id[:12], pd_time, pl_time, pd_rows, pl_rows))

    # ── Summary ─────────────────────────────────────────────────────────
    print()
    print("=" * 90)
    print(f"{'Query':<22} {'Arg':<16} {'pandas':>10} {'polars':>10} {'speedup':>8} {'pd rows':>8} {'pl rows':>8}")
    print("-" * 90)

    for name, arg, pd_t, pl_t, pd_r, pl_r in results:
        speedup = pd_t / pl_t if pl_t > 0 else 0
        match = "OK" if pd_r == pl_r else f"DIFF"
        print(f"{name:<22} {arg:<16} {pd_t*1000:>8.1f}ms {pl_t*1000:>8.1f}ms {speedup:>7.2f}x {pd_r:>8} {pl_r:>7} {match}")

    print("=" * 90)


if __name__ == "__main__":
    main()
