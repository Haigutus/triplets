"""
Validate all NC (Network Code) instance files against their matching SHACL rules.

Uses the mapping from collect_available_rules to automatically pair each instance
file with the correct SHACL constraints based on dcterms:conformsTo profile URI.
"""
import pathlib
import time
import pandas
import triplets
from triplets.validation import validate_shacl
from triplets.validation.rdflib_shacl_parser import parse_shacl
from triplets.validation.shacl_report import write_shacl_report

base_path = pathlib.Path(__file__).parent.absolute()
profiles_path = base_path / "test_data/entsoe-profiles"
nc_instance_path = base_path / "test_data/relicapgrid/Instance"
report_dir = base_path / "validation_reports"
report_dir.mkdir(exist_ok=True)

# Build profile URI -> SHACL file paths mapping from collect_available_rules
import triplets.shacl.collect_available_rules as car

# Build versionIRI -> base profile URI lookup from RDFS
version_to_base = {}
version_iris = car.rdfs_data.query("KEY == 'versionIRI'")
for _, row in version_iris.iterrows():
    version_to_base[row["VALUE"]] = row["ID"]

profile_to_shacl = {}
resolved = car.mapping[car.mapping["shacl_file"] == "both"]
for _, row in resolved.iterrows():
    profile_uri = row["profile_uri"]
    shacl_path = profiles_path / row["shacl_path"]
    if profile_uri not in profile_to_shacl:
        profile_to_shacl[profile_uri] = []
    profile_to_shacl[profile_uri].append(shacl_path)

# Find all NC instance XML files (under */NetworkCode/cimxml/)
instance_files = sorted(nc_instance_path.glob("*/NetworkCode/cimxml/*.xml"))
instance_files = [f for f in instance_files if "Dataset_version" not in str(f)]

print(f"Found {len(instance_files)} NC instance files")
print(f"Available profile mappings: {len(profile_to_shacl)}")
print()

# Cache parsed SHACL rules per profile URI
rules_cache = {}

engines = ["pandas", "polars"]

results = []

for instance_file in instance_files:
    # Load instance data and detect profile
    try:
        data = pandas.read_RDF([str(instance_file)])
    except Exception as e:
        print(f"SKIP {instance_file.name} - parse error: {e}")
        continue
    conforms_to = data.query("KEY == 'conformsTo'")

    if conforms_to.empty:
        print(f"SKIP {instance_file.name} - no conformsTo found")
        continue

    # Resolve versioned conformsTo URIs to base profile URIs and find matching profiles
    version_lookup = pandas.DataFrame(list(version_to_base.items()), columns=["VALUE", "profile_uri"])
    matched = conforms_to[["VALUE"]].merge(version_lookup, on="VALUE", how="inner")

    if matched.empty:
        print(f"SKIP {instance_file.name} - no SHACL mapping for conformsTo: {list(conforms_to['VALUE'].values)}")
        continue

    profile_uri = matched["profile_uri"].values[0]

    # Parse SHACL rules (cached)
    if profile_uri not in rules_cache:
        shacl_files = profile_to_shacl[profile_uri]
        rules_cache[profile_uri] = parse_shacl([str(f) for f in shacl_files])

    rules = rules_cache[profile_uri]

    print(f"{'=' * 80}")
    print(f"FILE: {instance_file.relative_to(base_path)}")
    print(f"PROFILE: {profile_uri}")
    print(f"RULES: {len(rules)} constraints from {len(profile_to_shacl[profile_uri])} SHACL files")
    print(f"DATA: {len(data)} triples, {data['ID'].nunique()} entities")

    for engine in engines:
        try:
            if engine == "polars":
                import polars as pl
                df = pl.from_pandas(data)
            else:
                df = data

            start = time.time()
            violations = validate_shacl(df, rules, engine=engine, check_external=False)
            elapsed = time.time() - start

            n_violations = len(violations)
            print(f"  {engine:20s}: {n_violations:4d} violations in {elapsed:.3f}s")

            # Write SHACL report for pandas engine
            if engine == "pandas":
                shacl_file_names = [str(f.relative_to(profiles_path)) for f in profile_to_shacl[profile_uri]]
                for fmt, ext in [("xml", ".rdf")]:
                    report_path = report_dir / f"{instance_file.stem}_report{ext}"
                    write_shacl_report(violations, str(report_path), format=fmt,
                                       validated_file=instance_file.name, shacl_files=shacl_file_names)

            results.append({
                "file": instance_file.name,
                "profile": profile_uri,
                "engine": engine,
                "violations": n_violations,
                "time_s": round(elapsed, 3),
                "rules": len(rules),
                "triples": len(data),
            })

        except Exception as e:
            print(f"  {engine:20s}: ERROR - {e}")
            results.append({
                "file": instance_file.name,
                "profile": profile_uri,
                "engine": engine,
                "violations": -1,
                "time_s": -1,
                "rules": len(rules),
                "triples": len(data),
            })

    print()

# Summary
print("=" * 80)
print("SUMMARY")
print("=" * 80)
results_df = pandas.DataFrame(results)
if not results_df.empty:
    summary = results_df.pivot_table(
        index=["file", "profile"],
        columns="engine",
        values=["violations", "time_s"],
        aggfunc="first",
    )
    print(summary.to_string())

    # Check if engines agree
    pivot = results_df.pivot_table(index="file", columns="engine", values="violations", aggfunc="first")
    if len(engines) > 1:
        mismatches = pivot[pivot[engines[0]] != pivot[engines[1]]]
        if mismatches.empty:
            print(f"\nAll engines agree on violation counts.")
        else:
            print(f"\nENGINE MISMATCHES:")
            print(mismatches.to_string())
