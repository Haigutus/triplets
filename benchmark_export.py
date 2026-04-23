"""Benchmark different CIM XML export implementations.

Variants tested:
1. Original (lxml ElementMaker + itertuples)
2. lxml optimized (pre-grouped data, numpy array access, tag cache)
3. String builder (no XML library, direct string concat)
4. Polars-based (Polars for data access + string building)
5. Cython (C++ string building from numpy arrays)

Usage:
    python benchmark_export.py [--runs N] [--workers N] [--file PATH]
"""
import os
import sys
import time
import argparse
import gc
import json

sys.path.insert(0, ".")


def load_test_data(test_file):
    """Load test data and return DataFrame + rdf_map."""
    import pandas
    import triplets

    print(f"Loading test data from {test_file}...")
    start = time.perf_counter()
    data = pandas.read_RDF([test_file])
    elapsed = time.perf_counter() - start
    print(f"  Loaded {len(data)} rows in {elapsed:.2f}s")
    print(f"  Instances: {data['INSTANCE_ID'].nunique()}")

    return data


def benchmark_export(name, generate_func, instances, rdf_map, runs=3):
    """Run a single export benchmark across all instances."""
    times = []
    total_bytes = 0

    for r in range(runs):
        gc.collect()
        gc.disable()

        start = time.perf_counter()
        results = []
        for _, instance_data in instances:
            result = generate_func(
                instance_data,
                rdf_map=rdf_map,
                class_KEY="Type",
                export_undefined=True,
            )
            if result:
                results.append(result)
        elapsed = time.perf_counter() - start

        gc.enable()
        times.append(elapsed)
        total_bytes = sum(len(r["file"]) for r in results)

    return {
        "name": name,
        "times": times,
        "min": min(times),
        "max": max(times),
        "avg": sum(times) / len(times),
        "files": len(results),
        "total_bytes": total_bytes,
    }


def benchmark_export_parallel(name, generate_func, data, rdf_map, runs=3, max_workers=4):
    """Run a parallel export benchmark using ThreadPoolExecutor.

    Uses threads instead of processes to avoid DataFrame pickling overhead.
    """
    from concurrent.futures import ThreadPoolExecutor
    times = []
    total_bytes = 0

    instances = list(data.groupby("INSTANCE_ID"))

    for r in range(runs):
        gc.collect()
        gc.disable()

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(generate_func, inst_data, rdf_map, None, "Type", True, None, False)
                for _, inst_data in instances
            ]
            results = [f.result() for f in futures if f.result() is not None]
        elapsed = time.perf_counter() - start

        gc.enable()
        times.append(elapsed)
        total_bytes = sum(len(r["file"]) for r in results)

    return {
        "name": name,
        "times": times,
        "min": min(times),
        "max": max(times),
        "avg": sum(times) / len(times),
        "files": len(results),
        "total_bytes": total_bytes,
    }


def compare_outputs(reference, test, name):
    """Compare XML output sizes and structure (basic check)."""
    if len(reference) != len(test):
        print(f"  WARNING {name}: file count mismatch ref={len(reference)} vs test={len(test)}")
        return False

    # Sort by filename for stable comparison
    ref_sorted = sorted(reference, key=lambda x: x["filename"])
    test_sorted = sorted(test, key=lambda x: x["filename"])

    total_ref = 0
    total_test = 0
    for r, t in zip(ref_sorted, test_sorted):
        total_ref += len(r["file"])
        total_test += len(t["file"])
        if r["filename"] != t["filename"]:
            print(f"  WARNING {name}: filename mismatch {r['filename']} vs {t['filename']}")
            return False

    ratio = total_test / total_ref if total_ref > 0 else 0
    print(f"  OK {name}: {len(reference)} files, size ratio={ratio:.3f} ({total_ref:,} vs {total_test:,} bytes)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Benchmark CIM XML export variants")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark runs")
    parser.add_argument("--workers", type=int, default=4, help="Workers for parallel variants")
    parser.add_argument("--file", type=str, default=None, help="Path to XML or ZIP file")
    parser.add_argument("--verify", action="store_true", help="Verify output correctness")
    args = parser.parse_args()

    # Default test file
    if args.file:
        test_file = os.path.abspath(args.file)
    else:
        test_file = os.path.abspath(
            "test_data/TestConfigurations_packageCASv2.0/RealGrid/"
            "CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"
        )

    # Load data
    data = load_test_data(test_file)

    # Load rdf_map
    from triplets.export_schema import schemas
    rdf_map_path = str(schemas.ENTSOE_CGMES_2_4_15_552_ED2)
    with open(rdf_map_path) as f:
        rdf_map = json.load(f)

    instances = list(data.groupby("INSTANCE_ID"))
    print(f"\nBenchmark: {args.runs} runs, {len(instances)} instances")
    print()

    # Collect exporters
    exporters = {}

    # 1. Original
    try:
        from triplets.rdf_parser import generate_xml as orig_generate_xml
        exporters["1_original_lxml"] = orig_generate_xml
    except Exception as e:
        print(f"Skip original: {e}")

    # 2. lxml optimized
    try:
        from triplets.cimxml_export_lxml_optimized import generate_xml as opt_generate_xml
        exporters["2_lxml_optimized"] = opt_generate_xml
    except Exception as e:
        print(f"Skip lxml_optimized: {e}")

    # 3. String builder
    try:
        from triplets.cimxml_export_string import generate_xml as str_generate_xml
        exporters["3_string_builder"] = str_generate_xml
    except Exception as e:
        print(f"Skip string_builder: {e}")

    # 4. Polars
    try:
        from triplets.cimxml_export_polars import generate_xml as pl_generate_xml
        exporters["4_polars_string"] = pl_generate_xml
    except Exception as e:
        print(f"Skip polars: {e}")

    # 5. Cython raw string
    try:
        from triplets.cimxml_export_cython_wrapper import generate_xml as cy_generate_xml
        exporters["5_cython_string"] = cy_generate_xml
    except Exception as e:
        print(f"Skip cython string: {e}")

    # 6. Cython pugixml DOM (numpy arrays)
    try:
        from triplets.cimxml_export_pugixml_wrapper import generate_xml as pugi_generate_xml
        exporters["6_cython_pugixml"] = pugi_generate_xml
    except Exception as e:
        print(f"Skip cython pugixml: {e}")

    # 7. Arrow → pugixml (zero-copy Arrow reads)
    try:
        from triplets.cimxml_export_arrow_pugixml_wrapper import generate_xml as arrow_pugi_generate_xml
        exporters["7_arrow_pugixml"] = arrow_pugi_generate_xml
    except Exception as e:
        print(f"Skip arrow pugixml: {e}")

    if not exporters:
        print("No exporters available!")
        return

    # Run sequential benchmarks
    results = []
    reference_outputs = None

    print("=== Sequential (single-threaded) ===\n")

    for name, gen_func in sorted(exporters.items()):
        print(f"Running {name}...")
        try:
            result = benchmark_export(name, gen_func, instances, rdf_map, runs=args.runs)
            results.append(result)
            mb = result["total_bytes"] / (1024 * 1024)
            print(f"  {result['avg']:.4f}s avg ({result['min']:.4f}s - {result['max']:.4f}s) | "
                  f"{result['files']} files, {mb:.1f} MB")

            if args.verify:
                test_outputs = []
                for _, inst_data in instances:
                    r = gen_func(inst_data, rdf_map=rdf_map, class_KEY="Type", export_undefined=True)
                    if r:
                        test_outputs.append(r)
                if reference_outputs is None:
                    reference_outputs = test_outputs
                    print(f"  (reference)")
                else:
                    compare_outputs(reference_outputs, test_outputs, name)

        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback
            traceback.print_exc()

    # Run parallel benchmarks
    print("\n=== Parallel (max_workers={}) ===\n".format(args.workers))

    parallel_results = []
    for name, gen_func in sorted(exporters.items()):
        pname = f"{name}_parallel"
        print(f"Running {pname}...")
        try:
            result = benchmark_export_parallel(
                pname, gen_func, data, rdf_map,
                runs=args.runs, max_workers=args.workers
            )
            parallel_results.append(result)
            mb = result["total_bytes"] / (1024 * 1024)
            print(f"  {result['avg']:.4f}s avg ({result['min']:.4f}s - {result['max']:.4f}s) | "
                  f"{result['files']} files, {mb:.1f} MB")
        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback
            traceback.print_exc()

    # Summary tables
    all_results = results + parallel_results

    if all_results:
        print("\n" + "=" * 85)
        print(f"{'Exporter':<35} {'Avg (s)':>10} {'Min (s)':>10} {'Max (s)':>10} {'MB':>8} {'Speedup':>8}")
        print("-" * 85)

        baseline = results[0]["avg"] if results else 1
        for r in all_results:
            speedup = baseline / r["avg"] if r["avg"] > 0 else 0
            mb = r["total_bytes"] / (1024 * 1024)
            print(f"{r['name']:<35} {r['avg']:>10.4f} {r['min']:>10.4f} {r['max']:>10.4f} {mb:>8.1f} {speedup:>7.2f}x")

        print("=" * 85)


if __name__ == "__main__":
    main()
