"""
Polars-based SHACL constraint validation and testing.

This is a performance-optimized version using Polars instead of Pandas.
Compares performance with the Pandas implementation.
"""

import polars as pl
import pandas as pd
import triplets
from pathlib import Path
from rdflib import Graph, Namespace
from rdflib.namespace import SH, RDF, RDFS, XSD
import time
from collections import defaultdict


# Import the pandas version for comparison
from test_shacl_parse_and_validate import SHACLConstraintExtractor, print_section


def pandas_to_polars(pandas_df):
    """Convert pandas DataFrame to Polars DataFrame."""
    # Convert via dictionary to avoid type conversion issues
    data_dict = pandas_df.to_dict('list')
    return pl.DataFrame(data_dict)


class PolarsValidator:
    """SHACL validators implemented with Polars for performance."""

    @staticmethod
    def validate_min_count(df: pl.DataFrame, property_path: str, min_count: int, target_class: str = None) -> pl.DataFrame:
        """Validate minimum cardinality constraint."""
        # Get all IDs of the target class if specified
        if target_class:
            target_ids = (
                df.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class))
                .select("ID")
            )
        else:
            target_ids = df.select("ID").unique()

        # Group by ID and count occurrences of the property
        counts = (
            df.filter(pl.col("KEY") == property_path)
            .group_by("ID")
            .agg(pl.len().alias("count"))
        )

        # Left join to find IDs with missing property
        violations = (
            target_ids.join(counts, on="ID", how="left")
            .with_columns(pl.col("count").fill_null(0))
            .filter(pl.col("count") < min_count)
            .with_columns([
                pl.lit(property_path).alias("KEY"),
                pl.lit(None, dtype=pl.Utf8).alias("VALUE"),
                pl.lit(f"Property {property_path} has count {{}} but requires minimum {min_count}").alias("MESSAGE")
            ])
        )

        return violations

    @staticmethod
    def validate_max_count(df: pl.DataFrame, property_path: str, max_count: int, target_class: str = None) -> pl.DataFrame:
        """Validate maximum cardinality constraint."""
        data = df.filter(pl.col("KEY") == property_path)

        # Filter by target class if specified
        if target_class:
            target_ids = (
                df.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == target_class))
                .select("ID")
            )
            data = data.join(target_ids, on="ID", how="inner")

        violations = (
            data.group_by("ID")
            .agg([
                pl.len().alias("COUNT"),
                pl.first("VALUE").alias("VALUE")
            ])
            .filter(pl.col("COUNT") > max_count)
            .with_columns([
                pl.lit(property_path).alias("KEY"),
                pl.lit(f"Property {property_path} appears {{COUNT}} times but maximum is {max_count}").alias("MESSAGE")
            ])
        )

        return violations

    @staticmethod
    def validate_datatype(df: pl.DataFrame, property_path: str, datatype: str) -> pl.DataFrame:
        """Validate datatype constraint."""
        data = df.filter(pl.col("KEY") == property_path)

        if datatype in ["xsd:float", "xsd:double", "xsd:decimal"]:
            # Try to cast to float, violations are those that fail
            violations = (
                data.with_columns(
                    pl.col("VALUE").cast(pl.Float64, strict=False).alias("numeric_value")
                )
                .filter(pl.col("numeric_value").is_null() & pl.col("VALUE").is_not_null())
                .drop("numeric_value")
                .with_columns(
                    pl.lit(f"Value '{{VALUE}}' is not a valid {datatype}").alias("MESSAGE")
                )
            )
        elif datatype in ["xsd:integer", "xsd:int", "xsd:long"]:
            violations = (
                data.with_columns(
                    pl.col("VALUE").cast(pl.Int64, strict=False).alias("int_value")
                )
                .filter(pl.col("int_value").is_null() & pl.col("VALUE").is_not_null())
                .drop("int_value")
                .with_columns(
                    pl.lit(f"Value '{{VALUE}}' is not a valid {datatype}").alias("MESSAGE")
                )
            )
        elif datatype == "xsd:boolean":
            violations = (
                data.filter(
                    ~pl.col("VALUE").is_in(["true", "false", "1", "0"])
                )
                .with_columns(
                    pl.lit(f"Value '{{VALUE}}' is not a valid {datatype}").alias("MESSAGE")
                )
            )
        else:
            # String types - no validation needed
            violations = pl.DataFrame()

        return violations

    @staticmethod
    def validate_min_inclusive(df: pl.DataFrame, property_path: str, min_value: float) -> pl.DataFrame:
        """Validate minimum inclusive value constraint."""
        violations = (
            df.filter(pl.col("KEY") == property_path)
            .with_columns(
                pl.col("VALUE").cast(pl.Float64, strict=False).alias("numeric_value")
            )
            .filter(
                pl.col("numeric_value").is_not_null() &
                (pl.col("numeric_value") < min_value)
            )
            .with_columns(
                pl.lit(f"Value {{numeric_value}} is less than minimum {min_value}").alias("MESSAGE")
            )
            .drop("numeric_value")
        )

        return violations

    @staticmethod
    def validate_max_inclusive(df: pl.DataFrame, property_path: str, max_value: float) -> pl.DataFrame:
        """Validate maximum inclusive value constraint."""
        violations = (
            df.filter(pl.col("KEY") == property_path)
            .with_columns(
                pl.col("VALUE").cast(pl.Float64, strict=False).alias("numeric_value")
            )
            .filter(
                pl.col("numeric_value").is_not_null() &
                (pl.col("numeric_value") > max_value)
            )
            .with_columns(
                pl.lit(f"Value {{numeric_value}} is greater than maximum {max_value}").alias("MESSAGE")
            )
            .drop("numeric_value")
        )

        return violations

    @staticmethod
    def validate_min_length(df: pl.DataFrame, property_path: str, min_length: int) -> pl.DataFrame:
        """Validate minimum string length constraint."""
        violations = (
            df.filter(pl.col("KEY") == property_path)
            .filter(pl.col("VALUE").str.len_chars() < min_length)
            .with_columns(
                pl.lit(f"String length {{}} is less than minimum {min_length}").alias("MESSAGE")
            )
        )

        return violations

    @staticmethod
    def validate_max_length(df: pl.DataFrame, property_path: str, max_length: int) -> pl.DataFrame:
        """Validate maximum string length constraint."""
        violations = (
            df.filter(pl.col("KEY") == property_path)
            .filter(pl.col("VALUE").str.len_chars() > max_length)
            .with_columns(
                pl.lit(f"String length {{}} is greater than maximum {max_length}").alias("MESSAGE")
            )
        )

        return violations

    @staticmethod
    def validate_pattern(df: pl.DataFrame, property_path: str, regex: str) -> pl.DataFrame:
        """Validate regex pattern constraint."""
        violations = (
            df.filter(pl.col("KEY") == property_path)
            .filter(~pl.col("VALUE").str.contains(regex))
            .with_columns(
                pl.lit(f"Value does not match pattern: {regex}").alias("MESSAGE")
            )
        )

        return violations

    @staticmethod
    def validate_class(df: pl.DataFrame, property_path: str, target_class: str) -> pl.DataFrame:
        """Validate class/reference constraint."""
        # Get all reference values for this property
        references = df.filter(pl.col("KEY") == property_path)

        # Get all objects with their types
        types = (
            df.filter(pl.col("KEY") == "Type")
            .select([
                pl.col("ID"),
                pl.col("VALUE").alias("ActualType")
            ])
        )

        # Join references with types to check if they match target class
        violations = (
            references.join(types, left_on="VALUE", right_on="ID", how="left")
            .filter(pl.col("ActualType") != target_class)
            .with_columns(
                pl.lit(f"Referenced object has type {{ActualType}} but expected {target_class}").alias("MESSAGE")
            )
        )

        return violations


def apply_validators_polars(df: pl.DataFrame, constraints: list, test_name: str):
    """Apply Polars validators based on SHACL constraints."""
    print_section(f"Applying Polars Validators: {test_name}")

    all_violations = []
    validator_stats = defaultdict(int)
    validator = PolarsValidator()

    for constraint in constraints:
        prop = constraint['property']
        rule_name = constraint.get('rule_name', prop)
        constraint_class = constraint.get('class', '')
        description = constraint.get('description', '')
        severity = constraint.get('severity', 'Violation')

        # Apply cardinality constraints
        if 'min_count' in constraint:
            violations = validator.validate_min_count(df, prop, constraint['min_count'], target_class=constraint_class)
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'min_count',
                        'property': prop,
                        'class': constraint_class,
                        'expected': constraint['min_count'],
                        'actual': 0,
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['min_count'] += 1

        if 'max_count' in constraint:
            violations = validator.validate_max_count(df, prop, constraint['max_count'], target_class=constraint_class)
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'max_count',
                        'property': prop,
                        'class': constraint_class,
                        'expected': constraint['max_count'],
                        'actual': row.get('COUNT', '>1'),
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['max_count'] += 1

        # Apply datatype constraints
        if 'datatype' in constraint:
            violations = validator.validate_datatype(df, prop, constraint['datatype'])
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'datatype',
                        'property': prop,
                        'class': constraint_class,
                        'expected': constraint['datatype'],
                        'actual': row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['datatype'] += 1

        # Apply numeric range constraints
        if 'min_inclusive' in constraint:
            violations = validator.validate_min_inclusive(df, prop, constraint['min_inclusive'])
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'min_inclusive',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'>= {constraint["min_inclusive"]}',
                        'actual': row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['min_inclusive'] += 1

        if 'max_inclusive' in constraint:
            violations = validator.validate_max_inclusive(df, prop, constraint['max_inclusive'])
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'max_inclusive',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'<= {constraint["max_inclusive"]}',
                        'actual': row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['max_inclusive'] += 1

        # Apply string constraints
        if 'min_length' in constraint:
            violations = validator.validate_min_length(df, prop, constraint['min_length'])
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'min_length',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'length >= {constraint["min_length"]}',
                        'actual': row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['min_length'] += 1

        if 'max_length' in constraint:
            violations = validator.validate_max_length(df, prop, constraint['max_length'])
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'max_length',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'length <= {constraint["max_length"]}',
                        'actual': row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['max_length'] += 1

        if 'pattern' in constraint:
            violations = validator.validate_pattern(df, prop, constraint['pattern'])
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'pattern',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'matches /{constraint["pattern"]}/',
                        'actual': row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['pattern'] += 1

        # Apply class/reference constraints
        if 'target_class' in constraint:
            violations = validator.validate_class(df, prop, constraint['target_class'])
            if len(violations) > 0:
                for row in violations.iter_rows(named=True):
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': row['ID'],
                        'constraint_type': 'class',
                        'property': prop,
                        'class': constraint_class,
                        'expected': constraint['target_class'],
                        'actual': row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': row.get('MESSAGE', '')
                    })
            validator_stats['class'] += 1

    return all_violations, validator_stats


def benchmark_comparison():
    """Compare Pandas vs Polars performance on SHACL validation."""
    print_section("PANDAS vs POLARS PERFORMANCE COMPARISON")

    # Test files of different sizes
    test_files = {
        "small": Path("test_data/relicapgrid/Instance/BoundaryConfigurationExamples/TC-Boundary_data_split/CommonData.xml"),
        "medium": Path("test_data/relicapgrid/Instance/Grid/IGM_Belgovia/20220615T2230Z__Belgovia_EQ_1.xml"),
        "large": Path("test_data/relicapgrid/Instance/Grid/IGM_Svedala/20220615T2230Z__Svedala_EQ_1.xml"),
    }

    # Load SHACL constraints once
    shacl_file = Path("test_data/entsoe-profiles/CGMES/CurrentRelease/RDFS/Beta_501_Ed2_CD/61970-600-2_Equipment-AP-Con-Simple-SHACLED2a.rdf")
    extractor = SHACLConstraintExtractor(shacl_file)
    all_constraints = []
    for class_constraints in extractor.constraints.values():
        all_constraints.extend(class_constraints)

    print(f"\nLoaded {len(all_constraints)} SHACL constraints")
    print(f"\nTesting {len(test_files)} files of different sizes...\n")

    results = []

    for size_name, file_path in test_files.items():
        if not file_path.exists():
            print(f"⚠ Skipping {size_name}: file not found")
            continue

        print(f"\n{'=' * 80}")
        print(f"Testing: {size_name.upper()} - {file_path.name}")
        print('=' * 80)

        # Load data once
        print("\nLoading data...")
        pandas_df = pd.read_RDF([str(file_path)])
        polars_df = pandas_to_polars(pandas_df)

        print(f"  Rows: {len(pandas_df):,}")
        print(f"  Entities: {len(pandas_df['ID'].unique()):,}")

        # Benchmark Pandas
        print("\n[Pandas] Running validation...")
        start = time.time()
        from test_shacl_parse_and_validate import apply_validators
        pandas_violations, pandas_stats = apply_validators(pandas_df, all_constraints, file_path.name)
        pandas_time = time.time() - start

        print(f"  Time: {pandas_time:.3f}s")
        print(f"  Violations: {len(pandas_violations)}")

        # Benchmark Polars
        print("\n[Polars] Running validation...")
        start = time.time()
        polars_violations, polars_stats = apply_validators_polars(polars_df, all_constraints, file_path.name)
        polars_time = time.time() - start

        print(f"  Time: {polars_time:.3f}s")
        print(f"  Violations: {len(polars_violations)}")

        # Calculate speedup
        speedup = pandas_time / polars_time if polars_time > 0 else 0
        print(f"\n  ⚡ Speedup: {speedup:.2f}x")
        print(f"  Time saved: {(pandas_time - polars_time):.3f}s ({(1 - polars_time/pandas_time)*100:.1f}%)")

        results.append({
            'size': size_name,
            'file': file_path.name,
            'rows': len(pandas_df),
            'entities': len(pandas_df['ID'].unique()),
            'pandas_time': pandas_time,
            'polars_time': polars_time,
            'speedup': speedup,
            'pandas_violations': len(pandas_violations),
            'polars_violations': len(polars_violations)
        })

    # Summary
    print(f"\n\n{'=' * 80}")
    print("PERFORMANCE SUMMARY")
    print('=' * 80)
    print(f"\n{'Size':<10} | {'Rows':>10} | {'Pandas':>10} | {'Polars':>10} | {'Speedup':>10}")
    print('-' * 80)
    for r in results:
        print(f"{r['size']:<10} | {r['rows']:>10,} | {r['pandas_time']:>9.3f}s | {r['polars_time']:>9.3f}s | {r['speedup']:>9.2f}x")

    avg_speedup = sum(r['speedup'] for r in results) / len(results) if results else 0
    print(f"\n{'Average speedup:':<45} {avg_speedup:>9.2f}x")

    return results


if __name__ == "__main__":
    benchmark_comparison()
