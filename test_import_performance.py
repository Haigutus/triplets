"""
Comprehensive RDF DataFrame Construction Performance Comparison

Tests 11 different DataFrame construction methods to find the fastest and most memory-efficient
approach for loading RDF data into pandas DataFrames.

Methods tested:
1. DataFrame() - Current implementation
2. from_records() - pandas.DataFrame.from_records()
3. from_dict_comprehension - Dict with list comprehensions
4. from_dict_prebuilt - Dict built during parsing
5. pyarrow - PyArrow string backend
6. categorical - Categorical dtype for KEY column
7. numpy - NumPy array intermediate
8. polars - Polars with conversion to pandas
9. batched - Batched construction with pre-allocated lists
10. polars_batched - Polars batched with parallel join
11. hybrid - Combined best approaches (PyArrow + categorical)

Metrics tracked:
- Construction time (average and best)
- Memory before/after construction
- Peak memory during construction
- Final DataFrame memory footprint
"""

import pandas
import numpy as np
import time
import datetime
import tracemalloc
import sys
from pathlib import Path
from triplets.rdf_parser import load_RDF_to_list

# Try to import optional dependencies
try:
    import polars as pl
    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False
    print("Warning: polars not available. Polars-based methods will be skipped.")

try:
    import pyarrow
    PYARROW_AVAILABLE = True
except ImportError:
    PYARROW_AVAILABLE = False
    print("Warning: pyarrow not available. PyArrow-based methods will be skipped.")


def get_memory_mb():
    """Get current memory usage in MB using tracemalloc"""
    current, peak = tracemalloc.get_traced_memory()
    return current / 1024**2, peak / 1024**2


def load_RDF_to_list_dict(path_or_fileobject, debug=False):
    """Modified version that builds dict directly instead of list of tuples"""
    # Import here to avoid circular dependency
    from triplets.rdf_parser import clean_ID
    import lxml.etree as ET

    # Define RDF namespace constants (same as in load_RDF_to_list)
    RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    RDF_ID = f"{{{RDF_NS}}}ID"
    RDF_ABOUT = f"{{{RDF_NS}}}about"
    RDF_NODEID = f"{{{RDF_NS}}}nodeID"
    RDF_RESOURCE = f"{{{RDF_NS}}}resource"

    # Initialize dict with empty lists
    data_dict = {
        'ID': [],
        'KEY': [],
        'VALUE': [],
        'INSTANCE_ID': []
    }

    # Determine instance ID
    INSTANCE_ID = str(path_or_fileobject) if isinstance(path_or_fileobject, (str, Path)) else "stream"

    # Parse XML
    if isinstance(path_or_fileobject, (str, Path)):
        tree = ET.parse(str(path_or_fileobject))
    else:
        tree = ET.parse(path_or_fileobject)

    root = tree.getroot()

    # Process elements
    for element in root:
        ID = clean_ID(element.attrib.get(RDF_ABOUT) or element.attrib.get(RDF_ID) or element.attrib.get(RDF_NODEID) or "")

        for item in element:
            KEY = item.tag.split("}")[-1] if "}" in item.tag else item.tag
            VALUE = ""

            if item.text:
                VALUE = item.text.strip()
            else:
                VALUE = clean_ID(item.attrib.get(RDF_RESOURCE) or item.attrib.get(RDF_NODEID) or "")

                if VALUE.startswith("http"):
                    VALUE = VALUE.split("#")[-1]

            data_dict['ID'].append(ID)
            data_dict['KEY'].append(KEY)
            data_dict['VALUE'].append(VALUE)
            data_dict['INSTANCE_ID'].append(INSTANCE_ID)

    return data_dict


# Method 1: Current implementation
def load_RDF_to_dataframe_current(path, debug=False):
    """Current implementation using pandas.DataFrame()"""
    data_list = load_RDF_to_list(path, debug)
    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    return data


# Method 2: from_records
def load_RDF_to_dataframe_from_records(path, debug=False):
    """Alternative implementation using pandas.DataFrame.from_records()"""
    data_list = load_RDF_to_list(path, debug)
    data = pandas.DataFrame.from_records(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    return data


# Method 3: from_dict with list comprehension
def load_RDF_to_dataframe_from_dict_comprehension(path, debug=False):
    """Convert list of tuples to dict of lists using comprehensions"""
    data_list = load_RDF_to_list(path, debug)

    data_dict = {
        'ID': [row[0] for row in data_list],
        'KEY': [row[1] for row in data_list],
        'VALUE': [row[2] for row in data_list],
        'INSTANCE_ID': [row[3] for row in data_list]
    }

    data = pandas.DataFrame(data_dict)
    return data


# Method 4: from_dict prebuilt - single pass conversion
def load_RDF_to_dataframe_from_dict_prebuilt(path, debug=False):
    """Convert list to dict in single pass (avoid tuple unpacking overhead)"""
    data_list = load_RDF_to_list(path, debug)

    # Single-pass conversion with pre-sized lists
    n = len(data_list)
    id_list = [None] * n
    key_list = [None] * n
    value_list = [None] * n
    instance_list = [None] * n

    for i, (id_val, key, value, instance) in enumerate(data_list):
        id_list[i] = id_val
        key_list[i] = key
        value_list[i] = value
        instance_list[i] = instance

    data = pandas.DataFrame({
        'ID': id_list,
        'KEY': key_list,
        'VALUE': value_list,
        'INSTANCE_ID': instance_list
    })
    return data


# Method 5: PyArrow backend
def load_RDF_to_dataframe_pyarrow(path, debug=False):
    """Use PyArrow string backend for memory efficiency"""
    if not PYARROW_AVAILABLE:
        return None

    data_list = load_RDF_to_list(path, debug)
    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"],
                           dtype='string[pyarrow]')
    return data


# Method 6: Categorical KEY column
def load_RDF_to_dataframe_categorical(path, debug=False):
    """Use categorical dtype for KEY column (low cardinality)"""
    data_list = load_RDF_to_list(path, debug)
    data = pandas.DataFrame(data_list, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    data['KEY'] = data['KEY'].astype('category')
    return data


# Method 7: NumPy array intermediate
def load_RDF_to_dataframe_numpy(path, debug=False):
    """Convert to NumPy array first, then to DataFrame"""
    data_list = load_RDF_to_list(path, debug)
    arr = np.array(data_list, dtype=object)
    data = pandas.DataFrame(arr, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    return data


# Method 8: Polars with conversion
def load_RDF_to_dataframe_polars(path, debug=False):
    """Build in Polars first, then convert to pandas"""
    if not POLARS_AVAILABLE:
        return None

    data_list = load_RDF_to_list(path, debug)
    data_polars = pl.DataFrame(data_list, schema=['ID', 'KEY', 'VALUE', 'INSTANCE_ID'],
                               orient='row')
    data = data_polars.to_pandas()
    return data


# Method 9: Batched construction
def load_RDF_to_dataframe_batched(path, debug=False):
    """Build lists in batches to reduce reallocation overhead"""
    data_list = load_RDF_to_list(path, debug)

    batch_size = 100000
    batches = []

    for i in range(0, len(data_list), batch_size):
        batch = data_list[i:i+batch_size]
        batch_df = pandas.DataFrame(batch, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
        batches.append(batch_df)

    if len(batches) == 1:
        data = batches[0]
    else:
        data = pandas.concat(batches, ignore_index=True)

    return data


# Method 10: Polars batched with parallel join
def load_RDF_to_dataframe_polars_batched(path, debug=False):
    """Build in Polars batches, then join and convert"""
    if not POLARS_AVAILABLE:
        return None

    data_list = load_RDF_to_list(path, debug)

    batch_size = 100000
    batches = []

    for i in range(0, len(data_list), batch_size):
        batch = data_list[i:i+batch_size]
        batch_pl = pl.DataFrame(batch, schema=['ID', 'KEY', 'VALUE', 'INSTANCE_ID'],
                               orient='row')
        batches.append(batch_pl)

    if len(batches) == 1:
        data_polars = batches[0]
    else:
        data_polars = pl.concat(batches)

    data = data_polars.to_pandas()
    return data


# Method 11: Hybrid approach (best of all)
def load_RDF_to_dataframe_hybrid(path, debug=False):
    """Combine from_dict + PyArrow + categorical for best performance"""
    data_list = load_RDF_to_list(path, debug)

    if not PYARROW_AVAILABLE:
        # Fallback to from_dict with categorical if PyArrow not available
        data_dict = {
            'ID': [row[0] for row in data_list],
            'KEY': [row[1] for row in data_list],
            'VALUE': [row[2] for row in data_list],
            'INSTANCE_ID': [row[3] for row in data_list]
        }
        data = pandas.DataFrame(data_dict)
        data['KEY'] = data['KEY'].astype('category')
        data['INSTANCE_ID'] = data['INSTANCE_ID'].astype('category')
        return data

    # Build dict using list comprehensions
    data_dict = {
        'ID': [row[0] for row in data_list],
        'KEY': [row[1] for row in data_list],
        'VALUE': [row[2] for row in data_list],
        'INSTANCE_ID': [row[3] for row in data_list]
    }
    data = pandas.DataFrame(data_dict)

    # Apply optimized dtypes
    data = data.astype({
        'ID': 'string[pyarrow]',
        'KEY': 'category',
        'VALUE': 'string[pyarrow]',
        'INSTANCE_ID': 'category'
    })

    return data


# Registry of all methods
METHODS = {
    'current': {
        'name': 'DataFrame()',
        'func': load_RDF_to_dataframe_current,
        'description': 'Current implementation',
        'available': True
    },
    'from_records': {
        'name': 'from_records()',
        'func': load_RDF_to_dataframe_from_records,
        'description': 'pandas.DataFrame.from_records()',
        'available': True
    },
    'from_dict_comp': {
        'name': 'from_dict_comp',
        'func': load_RDF_to_dataframe_from_dict_comprehension,
        'description': 'Dict with list comprehensions',
        'available': True
    },
    'from_dict_prebuilt': {
        'name': 'from_dict_prebuilt',
        'func': load_RDF_to_dataframe_from_dict_prebuilt,
        'description': 'Dict built during parsing',
        'available': True
    },
    'pyarrow': {
        'name': 'PyArrow',
        'func': load_RDF_to_dataframe_pyarrow,
        'description': 'PyArrow string backend',
        'available': PYARROW_AVAILABLE
    },
    'categorical': {
        'name': 'Categorical',
        'func': load_RDF_to_dataframe_categorical,
        'description': 'Categorical KEY column',
        'available': True
    },
    'numpy': {
        'name': 'NumPy',
        'func': load_RDF_to_dataframe_numpy,
        'description': 'NumPy array intermediate',
        'available': True
    },
    'polars': {
        'name': 'Polars',
        'func': load_RDF_to_dataframe_polars,
        'description': 'Polars with conversion',
        'available': POLARS_AVAILABLE
    },
    'batched': {
        'name': 'Batched',
        'func': load_RDF_to_dataframe_batched,
        'description': 'Batched construction',
        'available': True
    },
    'polars_batched': {
        'name': 'Polars Batched',
        'func': load_RDF_to_dataframe_polars_batched,
        'description': 'Polars batched with parallel join',
        'available': POLARS_AVAILABLE
    },
    'hybrid': {
        'name': 'Hybrid',
        'func': load_RDF_to_dataframe_hybrid,
        'description': 'Best combined approach',
        'available': True  # Has fallback if PyArrow unavailable
    }
}


def benchmark_method(method_key, file_path, iterations=3, reference_df=None):
    """Benchmark a single method"""
    method = METHODS[method_key]

    if not method['available']:
        return None

    print(f"\n  Testing {method['name']}...")

    times = []
    mem_before_list = []
    mem_after_list = []
    peak_mem_list = []
    df_size_list = []

    result_df = None

    for i in range(iterations):
        # Start memory tracking
        tracemalloc.start()
        mem_before, _ = get_memory_mb()

        # Benchmark
        start = time.perf_counter()
        try:
            df = method['func'](str(file_path), debug=False)
        except Exception as e:
            print(f"    ERROR: {e}")
            tracemalloc.stop()
            return None
        end = time.perf_counter()

        # Get memory stats
        mem_after, peak_mem = get_memory_mb()
        tracemalloc.stop()

        # Get DataFrame memory footprint
        df_size = df.memory_usage(deep=True).sum() / 1024**2  # MB

        elapsed = end - start
        times.append(elapsed)
        mem_before_list.append(mem_before)
        mem_after_list.append(mem_after)
        peak_mem_list.append(peak_mem)
        df_size_list.append(df_size)

        if i == 0:
            result_df = df

        if i == 0:
            print(f"    Iteration 1: {elapsed:.4f}s, {len(df)} rows, {df_size:.2f} MB")

    # Verify correctness against reference
    if reference_df is not None and result_df is not None:
        try:
            # Compare shapes
            if result_df.shape != reference_df.shape:
                print(f"    WARNING: Shape mismatch! {result_df.shape} vs {reference_df.shape}")
            # Compare values (convert to same dtype for comparison)
            elif not result_df.astype(str).equals(reference_df.astype(str)):
                print(f"    WARNING: Values differ from reference!")
        except Exception as e:
            print(f"    WARNING: Could not verify correctness: {e}")

    avg_time = sum(times) / len(times)
    min_time = min(times)
    avg_df_size = sum(df_size_list) / len(df_size_list)
    avg_peak_mem = sum(peak_mem_list) / len(peak_mem_list)

    print(f"    Average: {avg_time:.4f}s")
    print(f"    Best: {min_time:.4f}s")
    print(f"    DataFrame size: {avg_df_size:.2f} MB")
    print(f"    Peak memory: {avg_peak_mem:.2f} MB")

    return {
        'method_key': method_key,
        'method_name': method['name'],
        'description': method['description'],
        'times': times,
        'avg_time': avg_time,
        'min_time': min_time,
        'df_size': avg_df_size,
        'peak_mem': avg_peak_mem,
        'result_df': result_df
    }


def benchmark_file(file_path, iterations=3):
    """Benchmark a single file with all available methods"""
    print(f"\n{'='*100}")
    print(f"Testing: {file_path.name}")
    print(f"{'='*100}")

    # Get file size
    file_size_mb = file_path.stat().st_size / (1024**2) if file_path.exists() else 0
    print(f"File size: {file_size_mb:.2f} MB")

    print(f"\nRunning {iterations} iterations per method...")

    results = []
    reference_df = None

    # Test all methods
    for method_key in METHODS.keys():
        result = benchmark_method(method_key, file_path, iterations, reference_df)

        if result is not None:
            results.append(result)

            # Use first successful method as reference
            if reference_df is None:
                reference_df = result['result_df']

    if not results:
        print("\nNo methods could be tested!")
        return None

    # Calculate speedups relative to current method
    current_result = next((r for r in results if r['method_key'] == 'current'), None)
    if current_result:
        current_time = current_result['avg_time']
        current_size = current_result['df_size']

        for result in results:
            result['speedup'] = current_time / result['avg_time']
            result['time_saved'] = current_time - result['avg_time']
            result['time_saved_pct'] = (result['time_saved'] / current_time) * 100
            result['mem_saved'] = current_size - result['df_size']
            result['mem_saved_pct'] = (result['mem_saved'] / current_size) * 100

    # Print results table
    print(f"\n{'-'*100}")
    print("RESULTS:")
    print(f"  Rows loaded: {len(reference_df)}")
    print(f"\n{'Method':<20} {'Avg Time':<12} {'Best Time':<12} {'Speedup':<10} {'DF Size':<12} {'Mem Saved'}")
    print('-'*100)

    for result in results:
        speedup_str = f"{result.get('speedup', 1.0):.2f}x"
        time_str = f"{result['avg_time']:.4f}s"
        best_str = f"{result['min_time']:.4f}s"
        size_str = f"{result['df_size']:.1f} MB"

        if 'mem_saved_pct' in result:
            mem_str = f"{result['mem_saved']:.1f} MB ({result['mem_saved_pct']:.1f}%)"
        else:
            mem_str = "baseline"

        print(f"{result['method_name']:<20} {time_str:<12} {best_str:<12} {speedup_str:<10} {size_str:<12} {mem_str}")

    return {
        'file': file_path.name,
        'file_size_mb': file_size_mb,
        'rows': len(reference_df),
        'results': results
    }


def run_benchmark_suite():
    """Run benchmarks on multiple test files"""
    print("="*100)
    print("COMPREHENSIVE RDF DATAFRAME CONSTRUCTION PERFORMANCE COMPARISON")
    print("="*100)
    print("\nMethods being tested:")
    for i, (key, method) in enumerate(METHODS.items(), 1):
        status = "✓" if method['available'] else "✗"
        print(f"  {i:2d}. {status} {method['name']:<20} - {method['description']}")

    # Find test files
    test_files = []

    # Small file
    small_file = Path("test_data/relicapgrid/Instance/BoundaryConfigurationExamples/TC-Boundary_data_split/CommonData.xml")
    if small_file.exists():
        test_files.append(('small', small_file))

    # Medium file
    medium_file = Path("test_data/relicapgrid/Instance/Grid/IGM_Belgovia/20220615T2230Z__Belgovia_EQ_1.xml")
    if medium_file.exists():
        test_files.append(('medium', medium_file))

    # Large file
    large_file = Path("test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2/CGMES_v2.4.15_RealGridTestConfiguration_EQ_V2.xml")
    if large_file.exists():
        test_files.append(('large', large_file))

    if not test_files:
        print("\nNo test files found!")
        print("Please ensure test data exists in:")
        print("  - test_data/relicapgrid/")
        print("  - test_data/TestConfigurations_packageCASv2.0/")
        return

    print(f"\nFound {len(test_files)} test files")

    all_results = []

    for category, file_path in test_files:
        try:
            result = benchmark_file(file_path, iterations=3)
            if result:
                result['category'] = category
                all_results.append(result)
        except Exception as e:
            print(f"\nError testing {file_path.name}: {e}")
            import traceback
            traceback.print_exc()

    # Generate summary report
    print("\n" + "="*100)
    print("SUMMARY REPORT")
    print("="*100)

    # Find best methods across all tests
    method_scores = {}

    for file_result in all_results:
        for method_result in file_result['results']:
            key = method_result['method_key']
            if key not in method_scores:
                method_scores[key] = {
                    'name': method_result['method_name'],
                    'speedups': [],
                    'mem_saved': [],
                    'times': []
                }

            if 'speedup' in method_result:
                method_scores[key]['speedups'].append(method_result['speedup'])
                method_scores[key]['mem_saved'].append(method_result['mem_saved_pct'])
                method_scores[key]['times'].append(method_result['avg_time'])

    # Calculate overall metrics
    print("\nOVERALL PERFORMANCE:")
    print(f"{'Method':<20} {'Avg Speedup':<15} {'Avg Memory Saved':<20} {'Total Time'}")
    print('-'*100)

    for key, scores in sorted(method_scores.items(), key=lambda x: sum(x[1]['speedups'])/len(x[1]['speedups']), reverse=True):
        avg_speedup = sum(scores['speedups']) / len(scores['speedups']) if scores['speedups'] else 1.0
        avg_mem = sum(scores['mem_saved']) / len(scores['mem_saved']) if scores['mem_saved'] else 0.0
        total_time = sum(scores['times'])

        speedup_str = f"{avg_speedup:.2f}x"
        mem_str = f"{avg_mem:.1f}%"
        time_str = f"{total_time:.4f}s"

        print(f"{scores['name']:<20} {speedup_str:<15} {mem_str:<20} {time_str}")

    # Recommendations
    print("\n" + "="*100)
    print("RECOMMENDATIONS")
    print("="*100)

    # Best for speed
    best_speed_key = max(method_scores.items(),
                        key=lambda x: sum(x[1]['speedups'])/len(x[1]['speedups']) if x[1]['speedups'] else 0)
    best_speed_speedup = sum(best_speed_key[1]['speedups']) / len(best_speed_key[1]['speedups'])

    print(f"\n✓ FASTEST METHOD: {best_speed_key[1]['name']}")
    print(f"  Average speedup: {best_speed_speedup:.2f}x")

    # Best for memory
    best_mem_key = max(method_scores.items(),
                      key=lambda x: sum(x[1]['mem_saved'])/len(x[1]['mem_saved']) if x[1]['mem_saved'] else 0)
    best_mem_saved = sum(best_mem_key[1]['mem_saved']) / len(best_mem_key[1]['mem_saved'])

    print(f"\n✓ MOST MEMORY EFFICIENT: {best_mem_key[1]['name']}")
    print(f"  Average memory saved: {best_mem_saved:.1f}%")

    # Best balanced (speed * mem_efficiency)
    best_balanced_key = max(method_scores.items(),
                           key=lambda x: (sum(x[1]['speedups'])/len(x[1]['speedups']) if x[1]['speedups'] else 0) *
                                        (1 + sum(x[1]['mem_saved'])/len(x[1]['mem_saved'])/100 if x[1]['mem_saved'] else 1))

    print(f"\n✓ BEST BALANCED METHOD: {best_balanced_key[1]['name']}")
    balanced_speedup = sum(best_balanced_key[1]['speedups']) / len(best_balanced_key[1]['speedups'])
    balanced_mem = sum(best_balanced_key[1]['mem_saved']) / len(best_balanced_key[1]['mem_saved'])
    print(f"  Average speedup: {balanced_speedup:.2f}x")
    print(f"  Average memory saved: {balanced_mem:.1f}%")

    # Production impact analysis
    print("\n" + "="*100)
    print("PRODUCTION IMPACT ANALYSIS")
    print("="*100)

    # Calculate for large file (most representative)
    large_result = next((r for r in all_results if r['category'] == 'large'), None)
    if large_result:
        current_method = next((r for r in large_result['results'] if r['method_key'] == 'current'), None)
        best_method = next((r for r in large_result['results'] if r['method_key'] == best_balanced_key[0]), None)

        if current_method and best_method:
            time_saved_per_file = current_method['avg_time'] - best_method['avg_time']

            print(f"\nFor large files (~{large_result['rows']:,} rows):")
            print(f"  Current time: {current_method['avg_time']:.4f}s")
            print(f"  Optimized time: {best_method['avg_time']:.4f}s")
            print(f"  Time saved per file: {time_saved_per_file:.4f}s")

            # Extrapolate to typical workloads
            files_per_day = 500
            daily_savings = time_saved_per_file * files_per_day
            annual_savings = daily_savings * 365

            print(f"\n  If processing {files_per_day} files/day:")
            print(f"    Daily time savings: {daily_savings:.1f}s ({daily_savings/60:.1f} minutes)")
            print(f"    Annual time savings: {annual_savings:.1f}s ({annual_savings/3600:.1f} hours)")

            print(f"\n  Memory reduction: {best_method['mem_saved']:.1f} MB ({best_method['mem_saved_pct']:.1f}%)")
            print(f"    Allows {100/(100-best_method['mem_saved_pct']):.1f}x more files in memory")

    return all_results


if __name__ == "__main__":
    results = run_benchmark_suite()
