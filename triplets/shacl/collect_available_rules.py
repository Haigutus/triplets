import rdflib
import pathlib
import pandas
import triplets

base_path = pathlib.Path(__file__).parent.parent.parent.absolute()

profiles_path = pathlib.Path(
    __import__("sys").argv[1]
    if len(__import__("sys").argv) > 1
    else base_path / "test_data/entsoe-profiles"
)

shacl_paths = (
    list((profiles_path / "NCP/CurrentRelease/SHACL").glob("*.ttl"))
    +
    list((profiles_path / "CGMES/CurrentRelease/SHACL/TTL").glob("*.ttl"))
)

rdfs_paths = (
    list((profiles_path / "NCP/CurrentRelease/PROF").glob("*.rdf"))
    +
    list((profiles_path / "CGMES/CurrentRelease/PROF").glob("*.rdf"))
    +
    list((profiles_path / "CGMES/CurrentRelease/RDFS").glob("*.rdf"))
)

owl = rdflib.Namespace("http://www.w3.org/2002/07/owl#")
KEY = owl["versionIRI"]

map = []

for path in shacl_paths:

    graph = rdflib.Graph()
    graph.parse(path, format="turtle")
    results = list(graph.triples((None, KEY, None)))

    map.append({
        "name": path.stem,
        "url": str(results[0][2]),
        "shacl_path": str(path.relative_to(profiles_path)),
    })

rdfs_data = pandas.read_RDF(rdfs_paths)

constraints = rdfs_data.query("KEY == 'hasRole' and VALUE == 'http://www.w3.org/ns/dx/prof/role/constraints'")

# Build full PROF resource table with all roles
has_role = rdfs_data.query("KEY == 'hasRole'")
has_artifact = rdfs_data.query("KEY == 'hasArtifact'")
has_label = rdfs_data[rdfs_data["KEY"].isin(["label", "title"])]
has_resource = rdfs_data.query("KEY == 'hasResource'")

# Merge role, artifact, and label for each resource
prof_resources = has_role[["ID", "VALUE"]].rename(columns={"VALUE": "role"})
prof_resources = prof_resources.merge(has_artifact[["ID", "VALUE"]].rename(columns={"VALUE": "artifact_url"}), on="ID", how="left")
prof_resources = prof_resources.merge(has_label[["ID", "VALUE"]].rename(columns={"VALUE": "label"}), on="ID", how="left")
prof_resources["role"] = prof_resources["role"].str.split("/").str[-1]
prof_resources = prof_resources.rename(columns={"ID": "resource_uri"})

# Map profile URI -> resource via hasResource
profile_resources = has_resource.merge(prof_resources, left_on="VALUE", right_on="resource_uri", how="inner")
profile_resources = profile_resources[["ID", "resource_uri", "role", "artifact_url", "label"]].rename(columns={"ID": "profile_uri"})

# Filter to constraints only for backward compatibility
profile_constraints = profile_resources[profile_resources["role"] == "constraints"][["profile_uri", "resource_uri"]].rename(columns={"resource_uri": "constraint_uri"})

# Build SHACL DataFrame from parsed SHACL files
shacl_df = pandas.DataFrame(map).rename(columns={"url": "constraint_uri"})

# 1. Profile URI to SHACL URI mapping table
mapping = profile_constraints.merge(shacl_df, on="constraint_uri", how="outer", indicator="shacl_file")
mapping.to_csv("shacl_rules.csv", index=False)

# 2. RDFS profiles with unresolved SHACL rules (constraint in RDFS but no SHACL file)
rdfs_only = mapping[mapping["shacl_file"] == "left_only"]

# 3. SHACL rules without accompanying RDFS
shacl_only = mapping[mapping["shacl_file"] == "right_only"]


if __name__ == "__main__":

    print("=" * 80)
    print("0. PROF RESOURCE TABLE (all roles)")
    print("=" * 80)
    print(profile_resources.to_string(index=False))

    print()
    print("=" * 80)
    print("1. PROFILE URI TO SHACL URI MAPPING")
    print("=" * 80)
    print(mapping.to_string(index=False))

    print()
    print("=" * 80)
    print("2. RDFS CONSTRAINTS WITHOUT MATCHING SHACL FILE")
    print("=" * 80)
    if rdfs_only.empty:
        print("All RDFS constraint resources have matching SHACL files.")
    else:
        print(rdfs_only[["profile_uri", "constraint_uri"]].to_string(index=False))

    print()
    print("=" * 80)
    print("3. SHACL RULES WITHOUT ACCOMPANYING RDFS")
    print("=" * 80)
    if shacl_only.empty:
        print("All SHACL rules have accompanying RDFS entries.")
    else:
        print(shacl_only[["name", "constraint_uri"]].to_string(index=False))

