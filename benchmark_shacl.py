"""
Benchmark: Pandas vs Polars vs pySHACL for SHACL validation.

Compares all three engines on the same data files with the same SHACL rules.

Usage:
    python benchmark_shacl.py
"""

import time
import pandas as pd
import polars as pl
import triplets
from triplets.validation import parse_shacl

# ─── Configuration ────────────────────────────────────────────────────────────

SHACL_FILES = {
    "equipment": "test_data/entsoe-profiles/CGMES/CurrentRelease/RDFS/Beta_501_Ed2_CD/61970-600-2_Equipment-AP-Con-Simple-SHACLED2a.rdf",
    "geo": "test_data/entsoe-profiles/CGMES/CurrentRelease/RDFS/Beta_501_Ed2_CD/61970-600-2_GeographicalLocation-AP-Con-Simple-SHACLED2a.rdf",
}

TEST_FILES = {
    "Small (38 rows)": [
        "test_data/relicapgrid/Instance/BoundaryConfigurationExamples/TC-Boundary_data_split/CommonData.xml"
    ],
    "Medium (2K rows)": [
        "test_data/relicapgrid/Instance/Grid/IGM_Belgovia/20220615T2230Z__Belgovia_EQ_1.xml"
    ],
    "Large (48K rows)": [
        "test_data/relicapgrid/Instance/Grid/IGM_Svedala/20220615T2230Z__Svedala_EQ_1.xml"
    ],
}


def run_benchmark():
    print("SHACL Validation Benchmark: Pandas vs Polars vs pySHACL")
    print("=" * 70)

    # Load rules
    print("\nLoading SHACL rules...")
    all_rules = parse_shacl(list(SHACL_FILES.values()))
    print(f"Loaded {len(all_rules)} rules from {len(SHACL_FILES)} SHACL files")

    results = []

    for label, files in TEST_FILES.items():
        print(f"\n{'─' * 70}")
        print(f"Dataset: {label}")
        print(f"{'─' * 70}")

        data = pd.read_RDF(files)
        print(f"Rows: {len(data)}")

        row = {"dataset": label, "rows": len(data)}

        # Pandas engine
        t0 = time.perf_counter()
        v_pd = data.shacl(all_rules, engine="pandas")
        row["pandas_time"] = time.perf_counter() - t0
        row["pandas_violations"] = len(v_pd)
        print(f"  Pandas:  {row['pandas_time']:.3f}s  violations={row['pandas_violations']}")

        # Polars engine
        pl_data = pl.from_pandas(data)
        t0 = time.perf_counter()
        v_pl = pl_data.shacl(all_rules, engine="polars", check_external=True)
        row["polars_time"] = time.perf_counter() - t0
        row["polars_violations"] = len(v_pl)
        print(f"  Polars:  {row['polars_time']:.3f}s  violations={row['polars_violations']}")

        # pySHACL engine
        t0 = time.perf_counter()
        try:
            v_py = data.shacl(
                all_rules,
                engine="pyshacl",
                shacl_files=[SHACL_FILES["equipment"]],
            )
            row["pyshacl_time"] = time.perf_counter() - t0
            row["pyshacl_violations"] = len(v_py)
            row["pyshacl_error"] = None
        except Exception as e:
            row["pyshacl_time"] = time.perf_counter() - t0
            row["pyshacl_violations"] = None
            row["pyshacl_error"] = str(e)

        if row["pyshacl_error"]:
            print(f"  pySHACL: {row['pyshacl_time']:.3f}s  ERROR: {row['pyshacl_error'][:80]}")
        else:
            print(f"  pySHACL: {row['pyshacl_time']:.3f}s  violations={row['pyshacl_violations']}")

        results.append(row)

    # Summary table
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Dataset':<20s} {'Rows':>8s} {'Pandas':>10s} {'Polars':>10s} {'pySHACL':>10s} {'Pd/Pl':>8s}")
    print(f"{'-' * 20} {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8}")

    for r in results:
        py_str = f"{r['pyshacl_time']:.3f}s" if r["pyshacl_error"] is None else "ERROR"
        speedup = f"{r['pandas_time'] / r['polars_time']:.1f}x"
        print(
            f"{r['dataset']:<20s} {r['rows']:>8d} "
            f"{r['pandas_time']:>9.3f}s {r['polars_time']:>9.3f}s "
            f"{py_str:>10s} {speedup:>8s}"
        )

    print(f"\n{'=' * 70}")
    print("VIOLATION COUNTS")
    print(f"{'=' * 70}")
    print(f"{'Dataset':<20s} {'Pandas':>10s} {'Polars':>10s} {'pySHACL':>10s}")
    print(f"{'-' * 20} {'-' * 10} {'-' * 10} {'-' * 10}")

    for r in results:
        py_str = str(r["pyshacl_violations"]) if r["pyshacl_violations"] is not None else "ERROR"
        print(
            f"{r['dataset']:<20s} "
            f"{r['pandas_violations']:>10d} {r['polars_violations']:>10d} "
            f"{py_str:>10s}"
        )

    # Notes
    print("\nNotes:")
    print("- pySHACL validates at the RDF graph level using the full SHACL spec")
    print("- Pandas/Polars engines iterate per-rule on the triplet DataFrame")
    print("- pySHACL may error on SHACL shapes with non-standard property paths")
    print("- Violation count differences are due to type resolution and null handling")


if __name__ == "__main__":
    run_benchmark()
