"""
Parse SHACL constraints and automatically run validators on test data.

This script:
1. Parses SHACL constraint definitions from ENTSOE application profiles
2. Extracts constraint rules (cardinality, datatype, etc.)
3. Automatically applies validators based on SHACL shapes
4. Tests with progressively larger CIM data files
"""

import pandas
import triplets
from pathlib import Path
from rdflib import Graph, Namespace
from rdflib.namespace import SH, RDF, RDFS, XSD
import time
from collections import defaultdict

# Define paths
TEST_DATA_DIR = Path("test_data/relicapgrid/Instance")
SHACL_DIR = Path("test_data/entsoe-profiles/CGMES/CurrentRelease/RDFS/Beta_501_Ed2_CD")

# Test files (ordered by size/complexity)
TEST_FILES = {
    "small": TEST_DATA_DIR / "BoundaryConfigurationExamples/TC-Boundary_data_split/CommonData.xml",
    "boundary": TEST_DATA_DIR / "BoundaryConfigurationExamples/TC-Boundary_data_split/BoundaryData.xml",
    "medium": TEST_DATA_DIR / "BoundaryConfigurationExamples/TC-Boundary-EQ/20241223T0642Z_ENTSO-E_EQ_BD_1.xml",
}

# SHACL files
SHACL_FILES = {
    "equipment": SHACL_DIR / "61970-600-2_Equipment-AP-Con-Simple-SHACLED2a.rdf",
    "geographical": SHACL_DIR / "61970-600-2_GeographicalLocation-AP-Con-Simple-SHACLED2a.rdf",
}


def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'=' * 80}")
    print(f"{title}")
    print('=' * 80)


class SHACLConstraintExtractor:
    """Extract and organize SHACL constraints from RDF graphs."""

    def __init__(self, shacl_file):
        self.graph = Graph()
        self.graph.parse(str(shacl_file), format='xml')
        self.constraints = defaultdict(list)
        self.skipped_constraints = defaultdict(list)
        self.stats = {
            'total_shapes': 0,
            'total_properties': 0,
            'extracted': 0,
            'skipped': 0,
            'skip_reasons': defaultdict(int)
        }
        self._extract_constraints()

    def _extract_constraints(self):
        """Extract all constraints from SHACL shapes."""
        shapes = list(self.graph.subjects(RDF.type, SH.NodeShape))
        self.stats['total_shapes'] = len(shapes)

        for shape in shapes:
            target_class = self.graph.value(shape, SH.targetClass)
            if not target_class:
                continue

            class_name = str(target_class).split('#')[-1]

            # Get all property constraints for this shape
            properties = list(self.graph.objects(shape, SH.property))
            self.stats['total_properties'] += len(properties)

            for prop in properties:
                result = self._extract_property_constraint(prop, class_name)
                if result:
                    if 'skipped' in result:
                        self.skipped_constraints[class_name].append(result)
                        self.stats['skipped'] += 1
                        self.stats['skip_reasons'][result['reason']] += 1
                    else:
                        self.constraints[class_name].append(result)
                        self.stats['extracted'] += 1

    def _extract_property_constraint(self, prop, class_name):
        """Extract constraint details from a property shape."""
        path = self.graph.value(prop, SH.path)
        if not path:
            return None

        property_name = str(path).split('#')[-1]
        full_property = f"{class_name}.{property_name}"

        # If the property is from a parent class, use just the property name
        if '.' in property_name:
            full_property = property_name

        # Check for unsupported constraint types
        sparql_constraint = self.graph.value(prop, SH.sparql)
        if sparql_constraint:
            return {
                'property': full_property,
                'class': class_name,
                'skipped': True,
                'reason': 'SPARQL constraint (not supported)',
                'description': str(self.graph.value(prop, SH.description) or 'No description')
            }

        # Check for sh:node (nested shapes)
        node_constraint = self.graph.value(prop, SH.node)
        if node_constraint:
            return {
                'property': full_property,
                'class': class_name,
                'skipped': True,
                'reason': 'Nested shape (sh:node)',
                'description': str(self.graph.value(prop, SH.description) or 'No description')
            }

        # Check for logical constraints (sh:or, sh:and, sh:not)
        or_constraint = self.graph.value(prop, SH['or'])
        and_constraint = self.graph.value(prop, SH['and'])
        not_constraint = self.graph.value(prop, SH['not'])
        if or_constraint or and_constraint or not_constraint:
            logic_type = 'sh:or' if or_constraint else ('sh:and' if and_constraint else 'sh:not')
            return {
                'property': full_property,
                'class': class_name,
                'skipped': True,
                'reason': f'Logical constraint ({logic_type})',
                'description': str(self.graph.value(prop, SH.description) or 'No description')
            }

        constraint = {
            'property': full_property,
            'class': class_name,
        }

        # Extract metadata
        rule_name = self.graph.value(prop, SH.name)
        if rule_name:
            constraint['rule_name'] = str(rule_name)

        description = self.graph.value(prop, SH.description)
        if description:
            constraint['description'] = str(description)

        message = self.graph.value(prop, SH.message)
        if message:
            constraint['message'] = str(message)

        severity = self.graph.value(prop, SH.severity)
        if severity:
            constraint['severity'] = str(severity).split('#')[-1]

        # Extract constraint values
        min_count = self.graph.value(prop, SH.minCount)
        if min_count:
            constraint['min_count'] = int(min_count)

        max_count = self.graph.value(prop, SH.maxCount)
        if max_count:
            constraint['max_count'] = int(max_count)

        datatype = self.graph.value(prop, SH.datatype)
        if datatype:
            dt_name = str(datatype).split('#')[-1]
            constraint['datatype'] = f"xsd:{dt_name}"

        sh_class = self.graph.value(prop, SH['class'])
        if sh_class:
            constraint['target_class'] = str(sh_class).split('#')[-1]

        min_inclusive = self.graph.value(prop, SH.minInclusive)
        if min_inclusive:
            constraint['min_inclusive'] = float(min_inclusive)

        max_inclusive = self.graph.value(prop, SH.maxInclusive)
        if max_inclusive:
            constraint['max_inclusive'] = float(max_inclusive)

        pattern = self.graph.value(prop, SH.pattern)
        if pattern:
            constraint['pattern'] = str(pattern)

        min_length = self.graph.value(prop, SH.minLength)
        if min_length:
            constraint['min_length'] = int(min_length)

        max_length = self.graph.value(prop, SH.maxLength)
        if max_length:
            constraint['max_length'] = int(max_length)

        return constraint

    def get_constraints_for_class(self, class_name):
        """Get all constraints for a specific class."""
        return self.constraints.get(class_name, [])

    def get_all_classes(self):
        """Get all classes with constraints."""
        return sorted(self.constraints.keys())

    def print_summary(self):
        """Print summary of extracted constraints."""
        print(f"Extracted constraints for {len(self.constraints)} classes")
        for class_name in sorted(self.constraints.keys())[:10]:
            constraints = self.constraints[class_name]
            print(f"  {class_name}: {len(constraints)} constraints")

        # Show statistics
        print(f"\nConstraint Statistics:")
        print(f"  Total shapes: {self.stats['total_shapes']}")
        print(f"  Total property constraints: {self.stats['total_properties']}")
        print(f"  Extracted (supported): {self.stats['extracted']}")
        print(f"  Skipped (unsupported): {self.stats['skipped']}")

        if self.stats['skipped'] > 0:
            print(f"\n  Skip reasons:")
            for reason, count in sorted(self.stats['skip_reasons'].items(), key=lambda x: -x[1]):
                print(f"    - {reason}: {count}")


def load_shacl_constraints(shacl_files):
    """Load and parse SHACL constraint files."""
    print_section("Loading SHACL Constraints")

    extractors = {}
    for name, file_path in shacl_files.items():
        if not file_path.exists():
            print(f"✗ File not found: {file_path}")
            continue

        print(f"\nLoading {name}: {file_path.name}")
        start_time = time.time()
        extractor = SHACLConstraintExtractor(file_path)
        load_time = time.time() - start_time

        print(f"  ✓ Loaded in {load_time:.3f}s")
        extractor.print_summary()
        extractors[name] = extractor

    return extractors


def apply_validators(data, constraints, test_name):
    """Apply validators based on SHACL constraints and collect violations."""
    print_section(f"Applying Validators: {test_name}")

    all_violations = []
    validator_stats = defaultdict(int)

    for constraint in constraints:
        prop = constraint['property']
        rule_name = constraint.get('rule_name', prop)
        constraint_class = constraint.get('class', '')
        description = constraint.get('description', '')
        severity = constraint.get('severity', 'Violation')

        # Apply cardinality constraints
        if 'min_count' in constraint:
            violations = data._min_count(prop, min_count=constraint['min_count'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'min_count',
                        'property': prop,
                        'class': constraint_class,
                        'expected': constraint['min_count'],
                        'actual': 0,  # min_count violations mean property is missing
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['min_count'] += 1

        if 'max_count' in constraint:
            violations = data._max_count(prop, max_count=constraint['max_count'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'max_count',
                        'property': prop,
                        'class': constraint_class,
                        'expected': constraint['max_count'],
                        'actual': violation_row.get('COUNT', '>1'),
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['max_count'] += 1

        # Apply datatype constraints
        if 'datatype' in constraint:
            violations = data._datatype(prop, datatype=constraint['datatype'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'datatype',
                        'property': prop,
                        'class': constraint_class,
                        'expected': constraint['datatype'],
                        'actual': violation_row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['datatype'] += 1

        # Apply numeric range constraints
        if 'min_inclusive' in constraint:
            violations = data._min_inclusive(prop, min_value=constraint['min_inclusive'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'min_inclusive',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'>= {constraint["min_inclusive"]}',
                        'actual': violation_row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['min_inclusive'] += 1

        if 'max_inclusive' in constraint:
            violations = data._max_inclusive(prop, max_value=constraint['max_inclusive'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'max_inclusive',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'<= {constraint["max_inclusive"]}',
                        'actual': violation_row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['max_inclusive'] += 1

        # Apply string constraints
        if 'min_length' in constraint:
            violations = data._min_length(prop, min_length=constraint['min_length'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'min_length',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'length >= {constraint["min_length"]}',
                        'actual': violation_row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['min_length'] += 1

        if 'max_length' in constraint:
            violations = data._max_length(prop, max_length=constraint['max_length'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'max_length',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'length <= {constraint["max_length"]}',
                        'actual': violation_row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['max_length'] += 1

        if 'pattern' in constraint:
            violations = data._pattern(prop, regex=constraint['pattern'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'pattern',
                        'property': prop,
                        'class': constraint_class,
                        'expected': f'matches /{constraint["pattern"]}/',
                        'actual': violation_row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['pattern'] += 1

        # Apply class/reference constraints
        if 'target_class' in constraint:
            violations = data._class(prop, target_class=constraint['target_class'])
            if len(violations) > 0:
                for _, violation_row in violations.iterrows():
                    all_violations.append({
                        'rule_name': rule_name,
                        'object_id': violation_row['ID'],
                        'constraint_type': 'class',
                        'property': prop,
                        'class': constraint_class,
                        'expected': constraint['target_class'],
                        'actual': violation_row.get('VALUE', ''),
                        'severity': severity,
                        'description': description,
                        'message': constraint.get('message', ''),
                        'ERROR_MESSAGE': violation_row.get('ERROR_MESSAGE', '')

                    })
            validator_stats['class'] += 1

    return all_violations, validator_stats


def generate_report(data, extractors, test_name):
    """Generate comprehensive validation report."""
    print_section(f"Validation Report: {test_name}")

    total_entities = len(data['ID'].unique()) if 'ID' in data.columns else len(data)
    print(f"\nTotal entities: {total_entities}")
    print(f"Total rows: {len(data)}")

    # Collect all constraints from all extractors
    all_constraints = []
    total_skipped = 0
    skip_reasons = defaultdict(int)

    for extractor in extractors.values():
        for class_constraints in extractor.constraints.values():
            all_constraints.extend(class_constraints)
        total_skipped += extractor.stats['skipped']
        for reason, count in extractor.stats['skip_reasons'].items():
            skip_reasons[reason] += count

    print(f"\nApplying {len(all_constraints)} supported SHACL constraints...")
    if total_skipped > 0:
        print(f"⚠ Skipping {total_skipped} unsupported constraints")

    # Apply validators
    start_time = time.time()
    violations, stats = apply_validators(data, all_constraints, test_name)
    validation_time = time.time() - start_time

    print(f"\nValidation completed in {validation_time:.3f}s")
    print(f"\nValidator usage statistics:")
    for validator_type, count in sorted(stats.items()):
        print(f"  {validator_type:20s}: {count} constraints checked")

    # Show skipped constraints
    if total_skipped > 0:
        print(f"\n\nSkipped constraints ({total_skipped} total):")
        for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            print(f"  {reason:40s}: {count} constraints")

    print(f"\n\nViolations found: {len(violations)}")
    if violations:
        # Group violations by rule for summary
        from pandas import DataFrame
        violations_df = DataFrame(violations)

        print("\nViolation Summary by Rule:")
        summary = violations_df.groupby(['rule_name', 'constraint_type']).size().reset_index(name='count')
        summary = summary.sort_values('count', ascending=False).head(10)
        for _, row in summary.iterrows():
            print(f"  {row['rule_name'][:60]:60s} ({row['constraint_type']:12s}): {row['count']:3d} violations")

        print(f"\nDetailed Violations (first 20):")
        print("=" * 120)
        for i, violation in enumerate(violations[:20], 1):
            print(f"\n{i}. Rule: {violation['rule_name']}")
            print(f"   Object ID: {violation['object_id']}")
            print(f"   Constraint: {violation['constraint_type']} - Expected: {violation['expected']}, Actual: {str(violation['actual'])[:50]}")
            if violation.get('description'):
                print(f"   Description: {violation['description'][:80]}")

        # Export violations to CSV
        csv_filename = f"violations_{test_name.replace('.xml', '')}.csv"
        violations_df.to_csv(csv_filename, index=False)
        print(f"\n✓ Exported all violations to: {csv_filename}")
        
        # Generate and save SHACL standard report
        from triplets.validation import write_shacl_report
        shacl_report_path = f"shacl_report_{test_name.replace('.xml', '')}.xml"
        try:
            write_shacl_report(violations_df, shacl_report_path)
            print(f"✓ Exported standard SHACL report to: {shacl_report_path}")
        except Exception as e:
            print(f"⚠ Failed to export SHACL report: {e}")
    else:
        print("✓ No violations found - data is fully compliant!")

    return violations, stats


def test_file(file_path, extractors, all_rules):
    """Load and validate a single test file."""
    print_section(f"Testing: {file_path.name}")

    # Load data
    print(f"\nLoading {file_path.name}...")
    start_time = time.time()
    data = pandas.read_RDF([str(file_path)])
    load_time = time.time() - start_time

    print(f"✓ Loaded in {load_time:.3f}s")
    print(f"  Shape: {data.shape[0]} rows x {data.shape[1]} columns")

    if 'KEY' in data.columns:
        type_rows = data[data['KEY'] == 'Type']
        if not type_rows.empty:
            unique_types = type_rows['VALUE'].unique()
            print(f"  Entity types: {len(unique_types)}")
            print(f"  Sample types: {', '.join(sorted(unique_types)[:5])}")

    # Generate validation report (original way)
    start_orig = time.time()
    violations, stats = generate_report(data, extractors, file_path.name)
    orig_time = time.time() - start_orig

    # Test the new unified validation engine (Pandas)
    from triplets.validation import shacl
    # 1. Unified Pandas Engine
    print(f"\n[Unified Pandas] Running validation with {len(all_rules)} rules (check_external=True)...")
    start_pd = time.time()
    pd_violations = data.shacl(all_rules, engine='pandas', check_external=True)
    pd_time = time.time() - start_pd
    print(f"✓ [Unified Pandas] completed in {pd_time:.3f}s, Violations: {len(pd_violations)}")

    # 2. Unified Polars Engine (Sequential)
    import polars as pl
    pl_data = pl.DataFrame(data.to_dict('list'))
    print(f"\n[Unified Polars] Running validation with {len(all_rules)} rules (check_external=True)...")
    start_pl = time.time()
    pl_violations = pl_data.shacl(all_rules, engine='polars', check_external=True)
    pl_time = time.time() - start_pl
    print(f"✓ [Unified Polars] completed in {pl_time:.3f}s, Violations: {len(pl_violations)}")

    # 3. Polars Parallel Engine (Lazy + Parallel)
    print(f"\n[Polars Parallel] Running validation with {len(all_rules)} rules (check_external=True)...")
    start_plp = time.time()
    plp_violations = pl_data.shacl(all_rules, engine='polars_parallel', check_external=True)
    plp_time = time.time() - start_plp
    print(f"✓ [Polars Parallel] completed in {plp_time:.3f}s, Violations: {len(plp_violations)}")

    # 4. pySHACL Reference Engine
    print(f"\n[pySHACL Reference] Running validation (advanced=True)...")
    start_py = time.time()
    try:
        # Use only equipment SHACL for now as geo seems to have compliance issues
        py_violations = data.shacl(all_rules, engine='pyshacl', shacl_files=[SHACL_FILES['equipment']])
        py_time = time.time() - start_py
        print(f"✓ [pySHACL] completed in {py_time:.3f}s, Violations: {len(py_violations)}")
    except Exception as e:
        print(f"⚠ pySHACL failed: {e}")
        py_violations = []
        py_time = 0
    
    # Check for regressions
    print("\n" + "="*40)
    print("REGRESSION AND SPEEDUP SUMMARY")
    print("="*40)
    print(f"Original Engine  : {len(violations):>5} violations | {orig_time:.3f}s")
    print(f"Unified Pandas   : {len(pd_violations):>5} violations | {pd_time:.3f}s (Speedup: {orig_time/pd_time:.2f}x)")
    print(f"Unified Polars   : {len(pl_violations):>5} violations | {pl_time:.3f}s (Speedup: {orig_time/pl_time:.2f}x)")
    print(f"Polars Parallel  : {len(plp_violations):>5} violations | {plp_time:.3f}s (Speedup: {orig_time/plp_time:.2f}x)")
    if py_time > 0:
        print(f"pySHACL (Ref)    : {len(py_violations):>5} violations | {py_time:.3f}s")
        if len(py_violations) > 0:
            print("\n  Sample of pySHACL violations:")
            for i, row in py_violations.head(10).iterrows():
                print(f"    - ID: {row['ID']}, Rule: {row.get('RULE_NAME', 'N/A')}, Type: {row.get('VIOLATION_TYPE', 'N/A')}, Message: {row.get('ERROR_MESSAGE', 'N/A')}")
    
    if len(pd_violations) > 0:
        print(f"  [Unified Pandas] Columns: {list(pd_violations.columns)}")
    if len(pl_violations) > 0:
        print(f"  [Unified Polars] Columns: {pl_violations.columns}")
    
    if len(violations) != len(pd_violations):
        print(f"⚠ WARNING: Mismatch in Pandas violation count! Original={len(violations)}, Pandas={len(pd_violations)}")
    if len(violations) != len(pl_violations):
        print(f"⚠ WARNING: Mismatch in Polars violation count!")

    return {
        'file': file_path.name,
        'rows': len(data),
        'load_time': load_time,
        'violations': len(violations),
        'stats': stats
    }


def main():
    """Main test execution."""
    print_section("SHACL Constraint Parsing and Validation")
    print("\nThis script:")
    print("  1. Parses SHACL constraint definitions from ENTSOE profiles")
    print("  2. Extracts validation rules (cardinality, datatype, etc.)")
    print("  3. Automatically applies validators to test data")
    print("  4. Tests with progressively larger data files")

    # 1. Load SHACL constraints using new parser
    print_section("SHACL Rule Extraction")
    from triplets.validation import parse_shacl
    start_parse = time.time()
    # Explicitly load both files to ensure all rules are present
    all_rules = parse_shacl(list(SHACL_FILES.values()), keep_namespaces=False)
    time_parse = time.time() - start_parse
    print(f"✓ Extracted {len(all_rules)} rules in {time_parse:.3f}s")
    
    # Debug: Print first 5 complex rules
    complex_rules = [r for r in all_rules if 'or' in r or 'and' in r]
    print(f"  Found {len(complex_rules)} complex rules (or/and)")
    if complex_rules:
        import json
        print(f"  Example complex rule:\n{json.dumps(complex_rules[0], indent=2)}")

    # 2. Still load original extractors for the 'Original Engine' baseline comparison
    extractors = load_shacl_constraints(SHACL_FILES)

    if not all_rules:
        print("\n✗ No SHACL constraints loaded. Exiting.")
        return

    # 3. Test with progressively larger files
    results = []
    for name, file_path in TEST_FILES.items():
        if not file_path.exists():
            print(f"\n✗ File not found: {file_path}")
            continue

        result = test_file(file_path, extractors, all_rules)
        results.append(result)

    # 4. Final summary
    print_section("Test Summary")
    print("\nResults for all test files:")
    print(f"{'File':<50s} | {'Rows':>8s} | {'Load':>8s} | {'Violations':>10s}")
    print("-" * 80)
    for result in results:
        print(f"{result['file']:<50s} | {result['rows']:>8d} | {result['load_time']:>7.3f}s | {result['violations']:>10d}")

    print("\n✓ SHACL constraint parsing and validation completed")
    print("✓ Validators successfully applied based on SHACL shapes")
    print("✓ Automatic validation workflow functional")


if __name__ == "__main__":
    main()
