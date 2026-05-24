"""Generate PROF descriptors for CGMES profiles based on existing RDFS and SHACL files."""
import pandas
import triplets
import rdflib
import pathlib
import uuid
from lxml import etree

base_path = pathlib.Path(__file__).parent.absolute()
profiles_path = base_path / "test_data/entsoe-profiles"
cgmes_rdfs_dir = profiles_path / "CGMES/CurrentRelease/RDFS"
cgmes_shacl_dir = profiles_path / "CGMES/CurrentRelease/SHACL/TTL"
output_dir = profiles_path / "CGMES/CurrentRelease/PROF"
output_dir.mkdir(exist_ok=True)

# Load RDFS vocabulary files to get profile metadata
rdfs_files = list(cgmes_rdfs_dir.glob("*-Voc-RDFS2020.rdf"))
rdfs_data = pandas.read_RDF([str(f) for f in rdfs_files])

# Parse SHACL files to get versionIRIs
owl = rdflib.Namespace("http://www.w3.org/2002/07/owl#")
shacl_files = list(cgmes_shacl_dir.glob("*.ttl"))
shacl_map = []
for path in shacl_files:
    g = rdflib.Graph()
    g.parse(path, format="turtle")
    results = list(g.triples((None, owl["versionIRI"], None)))
    if results:
        shacl_map.append({"name": path.name, "shacl_uri": str(results[0][2]), "path": path})

# Build profile info from RDFS
profiles = {}
for vid in rdfs_data[rdfs_data["KEY"] == "versionIRI"]["ID"].unique():
    rows = rdfs_data[rdfs_data["ID"] == vid]
    ver = rows[rows["KEY"] == "versionIRI"]["VALUE"].values
    kw = rows[rows["KEY"] == "keyword"]["VALUE"].values
    title = rows[rows["KEY"] == "title"]["VALUE"].values
    desc = rows[rows["KEY"] == "description"]["VALUE"].values
    inst = rows["INSTANCE_ID"].values[0]
    dist = rdfs_data[(rdfs_data["INSTANCE_ID"] == inst) & (rdfs_data["KEY"] == "label")]
    fname = pathlib.Path(dist["VALUE"].values[0]).name if len(dist) else ""

    if len(ver) and len(kw):
        profile_name = fname.replace("61970-600-2_", "").replace("-AP-Voc-RDFS2020.rdf", "").replace("-AP-Voc-RDFS2019.rdf", "")
        profiles[kw[0]] = {
            "profile_uri": vid,
            "versionIRI": ver[0],
            "keyword": kw[0],
            "title": title[0] if len(title) else profile_name,
            "description": desc[0] if len(desc) else "",
            "rdfs_file": fname,
            "profile_name": profile_name,
        }

# Match SHACL files to profiles by name
for kw, prof in profiles.items():
    name = prof["profile_name"]
    matched = [s for s in shacl_map if name.split("-")[0] in s["name"]]
    prof["shacl_files"] = matched

# Generate PROF RDF/XML for each profile
NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "dcat": "http://www.w3.org/ns/dcat#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "prof": "http://www.w3.org/ns/dx/prof/",
    "role": "http://www.w3.org/ns/dx/prof/role/",
    "dcterms": "http://purl.org/dc/terms/",
}

for kw, prof in sorted(profiles.items()):
    root = etree.Element("{%s}RDF" % NS["rdf"], nsmap={
        "rdf": NS["rdf"], "owl": NS["owl"], "dcat": NS["dcat"],
        "rdfs": NS["rdfs"], "prof": NS["prof"], "role": NS["role"],
        "dcterms": NS["dcterms"],
    })
    root.set("{http://www.w3.org/XML/1998/namespace}base", "http://www.w3.org/ns/dx/prof")

    # Profile description
    profile = etree.SubElement(root, "{%s}Description" % NS["rdf"])
    profile.set("{%s}about" % NS["rdf"], prof["profile_uri"])
    etree.SubElement(profile, "{%s}type" % NS["rdf"]).set("{%s}resource" % NS["rdf"], "http://www.w3.org/ns/dx/prof/Profile")
    etree.SubElement(profile, "{%s}title" % NS["dcterms"]).text = prof["title"]
    etree.SubElement(profile, "{%s}description" % NS["dcterms"]).text = prof["description"] or f"CGMES {prof['title']} profile."
    etree.SubElement(profile, "{%s}keyword" % NS["dcat"]).text = kw
    etree.SubElement(profile, "{%s}hasToken" % NS["prof"]).text = kw
    etree.SubElement(profile, "{%s}creator" % NS["dcterms"]).text = "ENTSO-E"
    etree.SubElement(profile, "{%s}publisher" % NS["dcterms"]).text = "ENTSO-E"
    etree.SubElement(profile, "{%s}rightsHolder" % NS["dcterms"]).text = "ENTSO-E"
    etree.SubElement(profile, "{%s}license" % NS["dcterms"]).text = "https://www.apache.org/licenses/LICENSE-2.0"
    etree.SubElement(profile, "{%s}language" % NS["dcterms"]).text = "en-GB"
    etree.SubElement(profile, "{%s}identifier" % NS["dcterms"]).text = str(uuid.uuid4())
    etree.SubElement(profile, "{%s}versionIRI" % NS["owl"]).set("{%s}resource" % NS["rdf"], prof["versionIRI"])
    etree.SubElement(profile, "{%s}versionInfo" % NS["owl"]).text = "3.0.0"

    # Cross-profile dependencies via prof:isProfileOf
    PROFILE_DEPENDENCIES = {
        "SSH": ["EQ"],
        "TP": ["EQ"],
        "SV": ["EQ", "SSH", "TP"],
        "DY": ["EQ", "SSH", "SV", "TP"],
        "OP": ["EQ"],
        "SC": ["EQ"],
        "GL": ["EQ"],
        "DL": ["EQ", "TP"],
        "EQBD": ["EQ"],
    }
    for dep_kw in PROFILE_DEPENDENCIES.get(kw, []):
        if dep_kw in profiles:
            dep = etree.SubElement(profile, "{%s}isProfileOf" % NS["prof"])
            dep.set("{%s}resource" % NS["rdf"], profiles[dep_kw]["profile_uri"])

    # Vocabulary resource
    voc_uri = prof["versionIRI"].rstrip("/") + "/vocabulary"
    res_ref = etree.SubElement(profile, "{%s}hasResource" % NS["prof"])
    res_ref.set("{%s}resource" % NS["rdf"], voc_uri)

    voc = etree.SubElement(root, "{%s}Description" % NS["rdf"])
    voc.set("{%s}about" % NS["rdf"], voc_uri)
    etree.SubElement(voc, "{%s}type" % NS["rdf"]).set("{%s}resource" % NS["rdf"], "http://www.w3.org/ns/dx/prof/ResourceDescriptor")
    etree.SubElement(voc, "{%s}hasRole" % NS["prof"]).set("{%s}resource" % NS["rdf"], "http://www.w3.org/ns/dx/prof/role/vocabulary")
    etree.SubElement(voc, "{%s}hasArtifact" % NS["prof"]).set("{%s}resource" % NS["rdf"], prof["rdfs_file"])
    etree.SubElement(voc, "{%s}format" % NS["dcterms"]).set("{%s}resource" % NS["rdf"], "https://www.iana.org/assignments/media-types/application/rdf+xml")
    etree.SubElement(voc, "{%s}title" % NS["dcterms"]).text = f"{prof['title']}"

    # SHACL resources — assign role based on file type
    for shacl in prof["shacl_files"]:
        con_uri = shacl["shacl_uri"]
        name = shacl["name"]
        is_cross_profile = "CrossProfile" in name or "SolvedMAS" in name or "InverseAssociation" in name
        role = "validation" if is_cross_profile else "constraints"

        res_ref = etree.SubElement(profile, "{%s}hasResource" % NS["prof"])
        res_ref.set("{%s}resource" % NS["rdf"], con_uri)

        con = etree.SubElement(root, "{%s}Description" % NS["rdf"])
        con.set("{%s}about" % NS["rdf"], con_uri)
        etree.SubElement(con, "{%s}type" % NS["rdf"]).set("{%s}resource" % NS["rdf"], "http://www.w3.org/ns/dx/prof/ResourceDescriptor")
        etree.SubElement(con, "{%s}hasRole" % NS["prof"]).set("{%s}resource" % NS["rdf"], f"http://www.w3.org/ns/dx/prof/role/{role}")
        etree.SubElement(con, "{%s}hasArtifact" % NS["prof"]).set("{%s}resource" % NS["rdf"], name)
        etree.SubElement(con, "{%s}format" % NS["dcterms"]).set("{%s}resource" % NS["rdf"], "https://www.iana.org/assignments/media-types/text/turtle")
        etree.SubElement(con, "{%s}conformsTo" % NS["dcterms"]).text = "http://www.w3.org/ns/shacl"
        etree.SubElement(con, "{%s}label" % NS["rdfs"]).text = name.replace(".ttl", "").replace("_", " ")

    # Write file
    tree = etree.ElementTree(root)
    filename = f"CGMES-{kw}-PROF.rdf"
    filepath = output_dir / filename
    etree.indent(tree, space="\t")
    tree.write(str(filepath), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    print(f"{kw:6s} | {filename:30s} | voc: 1, constraints: {len(prof['shacl_files']):2d}")

print(f"\nGenerated {len(profiles)} PROF files in {output_dir}")
