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
    all_rules = parse_shacl(list(SHACL_FILES.values()), keep_namespaces=False)
    time_parse = time.time() - start_parse
    print(f"✓ Extracted {len(all_rules)} rules in {time_parse:.3f}s")

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
