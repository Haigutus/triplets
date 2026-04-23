"""Benchmark different RDF parser implementations.

Variants tested:
1. rdf_parser (original)         - lxml + clean_ID during parse + pandas
2. rdf_parser_polars_clean       - lxml + raw IDs + Polars parallel clean + Arrow->pandas
3. rdf_parser_expat              - Python built-in expat (SAX/C) + clean_ID during parse + pandas
4. rdf_parser_pygixml            - pygixml (Cython/pugixml) + clean_ID during parse + pandas
5. rdf_parser_pugixml            - pugixml (C++ bindings) + clean_ID during parse + pandas

Usage:
    python benchmark_parsers.py [--runs N] [--workers N] [--file PATH]
"""
import os
import sys
import time
import argparse
import gc

# Add project to path
sys.path.insert(0, ".")


def benchmark_single(name, load_func, test_files, runs=3, max_workers=None):
    """Run a single parser benchmark and return timing + row count."""
    times = []
    row_count = 0

    for i in range(runs):
        gc.collect()
        gc.disable()

        start = time.perf_counter()
        result = load_func(test_files, max_workers=max_workers)
        elapsed = time.perf_counter() - start

        gc.enable()

        times.append(elapsed)
        row_count = len(result)

        # Reset file objects for next run
        for f in test_files:
            if hasattr(f, 'seek'):
                f.seek(0)

    return {
        "name": name,
        "times": times,
        "min": min(times),
        "max": max(times),
        "avg": sum(times) / len(times),
        "rows": row_count,
    }


def compare_dataframes(reference, test, name):
    """Compare two DataFrames for correctness.

    Ignores INSTANCE_ID (random per run) and metadata rows (Distribution, NamespaceMap).
    Compares only the actual RDF data content.
    """
    import pandas as pd

    if hasattr(reference, 'to_pandas'):
        reference = reference.to_pandas()
    if hasattr(test, 'to_pandas'):
        test = test.to_pandas()

    # Filter out metadata rows (Distribution, NamespaceMap) and their children
    def filter_data_rows(df):
        # Find IDs of metadata objects
        meta_ids = set(df[(df["KEY"] == "Type") & (df["VALUE"].isin(["Distribution", "NamespaceMap"]))]["ID"].values)
        return df[~df["ID"].isin(meta_ids)].copy()

    ref_data = filter_data_rows(reference)
    test_data = filter_data_rows(test)

    # Sort by content columns only (ignore INSTANCE_ID)
    ref_sorted = ref_data[["ID", "KEY", "VALUE"]].sort_values(["ID", "KEY", "VALUE"]).reset_index(drop=True)
    test_sorted = test_data[["ID", "KEY", "VALUE"]].sort_values(["ID", "KEY", "VALUE"]).reset_index(drop=True)

    # Compare shapes
    if ref_sorted.shape != test_sorted.shape:
        print(f"  WARNING {name}: Data shape mismatch ref={ref_sorted.shape} vs test={test_sorted.shape}")
        return False

    # Compare content
    for col in ["ID", "KEY", "VALUE"]:
        # Handle None/NaN comparison
        ref_col = ref_sorted[col].fillna("__NULL__")
        test_col = test_sorted[col].fillna("__NULL__")
        mismatches = (ref_col != test_col).sum()
        if mismatches > 0:
            print(f"  WARNING {name}: {mismatches} mismatches in {col}")
            mask = ref_col != test_col
            print(f"    REF: {ref_sorted.loc[mask, col].head(5).tolist()}")
            print(f"    TST: {test_sorted.loc[mask, col].head(5).tolist()}")
            return False

    print(f"  OK {name}: Output matches reference ({ref_sorted.shape[0]} data rows)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Benchmark RDF parser variants")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark runs per variant")
    parser.add_argument("--workers", type=int, default=None, help="Thread pool workers (None=sequential)")
    parser.add_argument("--file", type=str, default=None, help="Path to XML or ZIP file to parse")
    parser.add_argument("--verify", action="store_true", help="Verify output correctness against original")
    args = parser.parse_args()

    # Default test file
    if args.file:
        test_files = [os.path.abspath(args.file)]
    else:
        test_files = [
            os.path.abspath("test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip")
        ]

    print(f"Benchmark: {args.runs} runs, workers={args.workers}")
    print(f"Files: {test_files}")
    print()

    # Import all parsers
    parsers = {}

    try:
        from triplets import rdf_parser
        parsers["1_lxml_original"] = rdf_parser.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser (original): {e}")

    try:
        from triplets import rdf_parser_polars_clean
        parsers["2_lxml_polars_clean"] = rdf_parser_polars_clean.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser_polars_clean: {e}")

    try:
        from triplets import rdf_parser_polars_clean as rpc
        parsers["2b_lxml_polars_native"] = lambda files, max_workers=None: rpc.load_all_to_polars(files, max_workers=max_workers)
    except Exception as e:
        print(f"Skip rdf_parser_polars_clean (native): {e}")

    try:
        from triplets import rdf_parser_lxml_tuned
        parsers["3_lxml_tuned"] = rdf_parser_lxml_tuned.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser_lxml_tuned: {e}")

    try:
        from triplets import rdf_parser_pygixml
        parsers["4_pygixml"] = rdf_parser_pygixml.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser_pygixml: {e}")

    try:
        from triplets import rdf_parser_pygixml_tuned
        parsers["5_pygixml_tuned"] = rdf_parser_pygixml_tuned.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser_pygixml_tuned: {e}")

    try:
        from triplets import rdf_parser_lxml_arrow
        parsers["3b_lxml_cython_arrow"] = rdf_parser_lxml_arrow.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser_lxml_arrow: {e}")

    try:
        from triplets import rdf_parser_cython
        parsers["5b_cython_pugixml"] = rdf_parser_cython.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser_cython: {e}")

    try:
        from triplets import rdf_parser_cython_arrow
        parsers["5c_cython_arrow"] = rdf_parser_cython_arrow.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser_cython_arrow: {e}")

    try:
        from triplets import rdf_parser_arrow
        parsers["6_rust_arrow"] = rdf_parser_arrow.load_all_to_dataframe
    except Exception as e:
        print(f"Skip rdf_parser_arrow (Rust): {e}")

    try:
        from triplets import rdf_parser_arrow as rpa
        parsers["6b_rust_polars"] = lambda files, max_workers=None: rpa.load_all_to_polars(files, max_workers=max_workers)
    except Exception as e:
        print(f"Skip rdf_parser_arrow polars (Rust): {e}")

    if not parsers:
        print("No parsers available!")
        return

    # Run benchmarks
    results = []
    reference_df = None

    for name, load_func in sorted(parsers.items()):
        print(f"Running {name}...")
        try:
            result = benchmark_single(name, load_func, test_files, runs=args.runs, max_workers=args.workers)
            results.append(result)
            print(f"  {result['avg']:.4f}s avg ({result['min']:.4f}s - {result['max']:.4f}s) | {result['rows']} rows")

            # Verify correctness
            if args.verify:
                test_df = load_func(test_files, max_workers=args.workers)
                if reference_df is None:
                    reference_df = test_df
                    print(f"  (reference)")
                else:
                    compare_dataframes(reference_df, test_df, name)

        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback
            traceback.print_exc()

    # Summary table
    if results:
        print("\n" + "=" * 70)
        print(f"{'Parser':<30} {'Avg (s)':>10} {'Min (s)':>10} {'Max (s)':>10} {'Rows':>10}")
        print("-" * 70)

        baseline = results[0]["avg"] if results else 1
        for r in results:
            speedup = baseline / r["avg"] if r["avg"] > 0 else 0
            print(f"{r['name']:<30} {r['avg']:>10.4f} {r['min']:>10.4f} {r['max']:>10.4f} {r['rows']:>10}  {speedup:.2f}x")

        print("=" * 70)


if __name__ == "__main__":
    main()
