"""
SPARQL-based SHACL constraint validation using oxigraph.

This module adds support for SPARQL constraints that were previously skipped.
Uses oxrdflib as an RDFLib-compatible interface to oxigraph for performance.
"""

import pandas as pd
import triplets
from pathlib import Path
from rdflib import Graph, Namespace, Literal, URIRef, BNode
from rdflib.namespace import SH, RDF, RDFS, XSD
from oxrdflib import OxigraphStore
import time
from collections import defaultdict

# Import the base extractor
from test_shacl_parse_and_validate import print_section


class SPARQLConstraintExtractor:
    """Extract SPARQL-based constraints from SHACL shapes."""

    def __init__(self, shacl_file):
        self.graph = Graph()
        self.graph.parse(str(shacl_file), format='xml')
        self.sparql_constraints = []
        self._extract_sparql_constraints()

    def _extract_sparql_constraints(self):
        """Extract all SPARQL constraints from SHACL shapes."""
        # Find all SPARQL constraints
        sparql_constraint_nodes = list(self.graph.subjects(RDF.type, SH.SPARQLConstraint))

        print(f"Found {len(sparql_constraint_nodes)} SPARQL constraints")

        for constraint_node in sparql_constraint_nodes:
            # Extract constraint details
            constraint = {
                'node': str(constraint_node),
                'select': str(self.graph.value(constraint_node, SH.select) or ''),
                'message': str(self.graph.value(constraint_node, SH.message) or 'SPARQL constraint violated'),
                'prefixes': str(self.graph.value(constraint_node, SH.prefixes) or ''),
            }

            # Find which property shape uses this constraint
            for s, p, o in self.graph.triples((None, SH.sparql, constraint_node)):
                property_shape = s
                path = self.graph.value(property_shape, SH.path)
                name = self.graph.value(property_shape, SH.name)
                description = self.graph.value(property_shape, SH.description)

                constraint['property_shape'] = str(property_shape)
                constraint['path'] = str(path) if path else None
                constraint['rule_name'] = str(name) if name else str(property_shape).split('#')[-1]
                constraint['description'] = str(description) if description else ''

                self.sparql_constraints.append(constraint)

    def get_all_sparql_constraints(self):
        """Return all SPARQL constraints."""
        return self.sparql_constraints


class OxigraphSPARQLValidator:
    """SPARQL-based validator using oxigraph for performance."""

    def __init__(self):
        self.store = None
        self.graph = None

    def load_data(self, pandas_df):
        """Load triplet data into oxigraph store."""
        # Create oxigraph-backed graph
        self.store = OxigraphStore()
        self.graph = Graph(store=self.store)

        # Convert pandas DataFrame to RDF triples
        # Format: Subject(ID) - Predicate(KEY) - Object(VALUE)
        print(f"Loading {len(pandas_df)} rows into oxigraph...")
        start = time.time()

        # Define namespaces
        NS = Namespace("http://iec.ch/TC57/CIM100#")
        DATA_NS = Namespace("urn:uuid:")

        for _, row in pandas_df.iterrows():
            # Ensure subject is a proper URI
            id_str = str(row['ID'])
            if id_str.startswith('http://') or id_str.startswith('urn:'):
                subject = URIRef(id_str)
            elif id_str.startswith('#'):
                subject = URIRef(NS + id_str[1:])
            else:
                # Assume it's a UUID, add urn:uuid: prefix
                subject = URIRef(DATA_NS + id_str)

            if row['KEY'] == 'Type':
                # rdf:type
                obj_type = NS[row['VALUE']]
                self.graph.add((subject, RDF.type, obj_type))
            else:
                # Property value
                predicate = NS[row['KEY']]
                # Try to determine if value is a URI reference
                value = str(row['VALUE'])

                # Check if it's a URI or UUID reference
                if value.startswith('http://') or value.startswith('urn:'):
                    obj = URIRef(value)
                elif value.startswith('#'):
                    obj = URIRef(NS + value[1:])
                elif len(value) == 36 and value.count('-') == 4:  # UUID pattern
                    obj = URIRef(DATA_NS + value)
                else:
                    obj = Literal(value)

                self.graph.add((subject, predicate, obj))

        load_time = time.time() - start
        print(f"Loaded {len(self.graph)} triples in {load_time:.3f}s")

        return self

    def validate_sparql_constraint(self, constraint):
        """Execute SPARQL constraint and return violations."""
        if not self.graph:
            raise ValueError("Data not loaded. Call load_data() first.")

        violations = []

        try:
            # Get the SPARQL SELECT query
            sparql_query = constraint['select']

            if not sparql_query or not sparql_query.strip():
                return violations

            # Add CIM namespace prefix if not present
            if 'PREFIX' not in sparql_query.upper():
                sparql_query = """
                PREFIX cim: <http://iec.ch/TC57/CIM100#>
                PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
                PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
                """ + sparql_query

            # Execute query
            results = self.graph.query(sparql_query)

            # Process results - each result is a violation
            for result in results:
                violation = {
                    'rule_name': constraint.get('rule_name', 'SPARQL constraint'),
                    'object_id': str(result[0]) if len(result) > 0 else 'Unknown',
                    'constraint_type': 'sparql',
                    'property': constraint.get('path', ''),
                    'class': '',
                    'expected': 'SPARQL condition',
                    'actual': str(result[1]) if len(result) > 1 else '',
                    'severity': 'Violation',
                    'description': constraint.get('description', ''),
                    'message': constraint.get('message', 'SPARQL constraint violated'),
                    'sparql_query': sparql_query[:200] + '...' if len(sparql_query) > 200 else sparql_query
                }
                violations.append(violation)

        except Exception as e:
            print(f"Error executing SPARQL constraint {constraint.get('rule_name', 'unknown')}: {e}")

        return violations


def test_sparql_validation():
    """Test SPARQL validation with real data and constraints."""
    print_section("SPARQL-Based SHACL Validation with Oxigraph")

    # Load SHACL file with SPARQL constraints
    shacl_file = Path("test_data/entsoe-profiles/CGMES/CurrentRelease/SHACL/RDF/61970-301_Equipment-AP-Con-Complex-SHACL.rdf")

    if not shacl_file.exists():
        print(f"✗ SHACL file not found: {shacl_file}")
        return

    print(f"\nLoading SHACL file: {shacl_file.name}")
    extractor = SPARQLConstraintExtractor(shacl_file)

    sparql_constraints = extractor.get_all_sparql_constraints()
    print(f"Found {len(sparql_constraints)} SPARQL constraints")

    if len(sparql_constraints) == 0:
        print("No SPARQL constraints to test")
        return

    # Show sample constraints
    print("\nSample SPARQL constraints:")
    for i, constraint in enumerate(sparql_constraints[:3], 1):
        print(f"\n{i}. {constraint['rule_name']}")
        print(f"   Description: {constraint['description'][:80]}...")
        print(f"   Query (first 100 chars): {constraint['select'][:100]}...")

    # Load test data
    print_section("Loading Test Data")
    test_file = Path("test_data/relicapgrid/Instance/Grid/IGM_Belgovia/20220615T2230Z__Belgovia_EQ_1.xml")

    if not test_file.exists():
        print(f"✗ Test file not found: {test_file}")
        return

    print(f"Loading: {test_file.name}")
    data = pd.read_RDF([str(test_file)])
    print(f"Loaded {len(data)} rows, {len(data['ID'].unique())} entities")

    # Load data into oxigraph
    print_section("Initializing Oxigraph Store")
    validator = OxigraphSPARQLValidator()
    validator.load_data(data)

    # Run SPARQL validations
    print_section("Running SPARQL Validations")

    all_violations = []
    validation_stats = defaultdict(int)

    for i, constraint in enumerate(sparql_constraints, 1):
        print(f"\n[{i}/{len(sparql_constraints)}] Validating: {constraint['rule_name']}")

        start = time.time()
        violations = validator.validate_sparql_constraint(constraint)
        duration = time.time() - start

        validation_stats['total_constraints'] += 1
        if len(violations) > 0:
            validation_stats['constraints_with_violations'] += 1
            validation_stats['total_violations'] += len(violations)
            print(f"  ✗ Found {len(violations)} violations ({duration:.3f}s)")
            all_violations.extend(violations)
        else:
            validation_stats['constraints_passed'] += 1
            print(f"  ✓ No violations ({duration:.3f}s)")

    # Summary
    print_section("Validation Summary")
    print(f"\nTotal SPARQL constraints checked: {validation_stats['total_constraints']}")
    print(f"Constraints passed: {validation_stats['constraints_passed']}")
    print(f"Constraints with violations: {validation_stats['constraints_with_violations']}")
    print(f"Total violations found: {validation_stats['total_violations']}")

    if all_violations:
        print(f"\nTop 10 violations:")
        for i, violation in enumerate(all_violations[:10], 1):
            print(f"\n{i}. Rule: {violation['rule_name']}")
            print(f"   Object: {violation['object_id']}")
            print(f"   Message: {violation['message']}")
            if violation.get('description'):
                print(f"   Description: {violation['description'][:80]}...")

        # Export to CSV
        violations_df = pd.DataFrame(all_violations)
        csv_file = "violations_sparql.csv"
        violations_df.to_csv(csv_file, index=False)
        print(f"\n✓ Exported all SPARQL violations to: {csv_file}")

    return all_violations, validation_stats


def compare_with_without_sparql():
    """Compare validation coverage with and without SPARQL constraints."""
    print_section("Validation Coverage Comparison")

    from test_shacl_parse_and_validate import SHACLConstraintExtractor

    shacl_file = Path("test_data/entsoe-profiles/CGMES/CurrentRelease/SHACL/RDF/61970-301_Equipment-AP-Con-Complex-SHACL.rdf")

    # Extract all constraints (with skipping)
    print("\n1. Standard SHACL constraints (non-SPARQL)")
    extractor = SHACLConstraintExtractor(shacl_file)

    total_constraints = extractor.stats['total_properties']
    extracted = extractor.stats['extracted']
    skipped = extractor.stats['skipped']
    sparql_skipped = extractor.stats['skip_reasons'].get('SPARQL constraint (not supported)', 0)

    print(f"   Total property constraints: {total_constraints}")
    print(f"   Extracted (supported): {extracted}")
    print(f"   Skipped (unsupported): {skipped}")
    print(f"   Of which SPARQL: {sparql_skipped}")

    # Extract SPARQL constraints
    print("\n2. SPARQL-based constraints")
    sparql_extractor = SPARQLConstraintExtractor(shacl_file)
    sparql_count = len(sparql_extractor.get_all_sparql_constraints())

    print(f"   SPARQL constraints found: {sparql_count}")

    # Calculate coverage
    print("\n3. Coverage Analysis")
    total_validation_rules = extracted + sparql_count
    print(f"   With SPARQL support: {total_validation_rules} constraints ({100*total_validation_rules/total_constraints:.1f}%)")
    print(f"   Without SPARQL support: {extracted} constraints ({100*extracted/total_constraints:.1f}%)")
    print(f"   Coverage improvement: +{sparql_count} constraints (+{100*sparql_count/total_constraints:.1f}%)")


if __name__ == "__main__":
    # First show coverage comparison
    compare_with_without_sparql()

    # Then run SPARQL validation test
    print("\n" + "="*80 + "\n")
    test_sparql_validation()
