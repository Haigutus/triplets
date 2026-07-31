"""Check the rdfs:domain / schema:domainIncludes convention in RDFS2020 vocabularies.

Convention (entsoe/application-profiles-library#92, PR #99):
  - a property may carry rdfs:domain when the profile owns the property
    (its namespace is one of the owned namespaces or the file's own base),
    or when property and domain class live in the same namespace, so the
    linkage restates the owning vocabulary instead of altering it;
  - a reused external property attached to a foreign class must carry
    schema:domainIncludes instead of rdfs:domain;
  - schema:domainIncludes is always allowed (dcat:startDate/endDate keep it
    even on dcat:Dataset because DCAT declares a conflicting rdfs:domain);
  - every property must be linked to a class by one of the two predicates;
  - a property carries at most one rdfs:domain, rdfs:range and
    cims:inverseRoleName - duplicates mean intersection semantics, i.e.
    conflicting domains (entsoe/application-profiles-library#104) or two
    UML associations merged onto one role (#107 in the same repo).
    schema:domainIncludes may repeat by design.

Exit code 0 when all files comply, 1 otherwise.
"""
import argparse
import pathlib

import pandas
import triplets  # noqa: F401  (registers pandas.read_RDF)

from triplets.rdfs_tools.rdfs_tools import get_namespace_and_name

OWNED_NAMESPACES = [
    "https://cim4.eu/",
    "https://ap-voc.cim4.eu/",
    "https://cim.ucaiug.io/",
    "http://iec.ch/TC57/",
    "http://entsoe.eu/",
]


def check_file(path, owned_namespaces):
    """Return a list of issue dicts for one RDFS file."""
    data = pandas.read_RDF([str(path)])
    base = data[data["KEY"] == "xml_base"]["VALUE"].values
    base = base[0] if len(base) else ""
    owned = owned_namespaces + ([base] if base else [])

    # read_RDF strips declared namespaces from VALUE, so map the local class
    # names back to full URIs via the class definitions in the same file
    classes = data[(data["KEY"] == "type") & (data["VALUE"] == "Class")]["ID"].unique()
    class_uris = {}
    for uri in classes:
        _, name = get_namespace_and_name(uri, base)
        class_uris.setdefault(name, []).append(uri)

    properties = data[(data["KEY"] == "type") & (data["VALUE"] == "Property")]["ID"].unique()
    domains = data[data["KEY"] == "domain"]
    includes = data[data["KEY"] == "domainIncludes"]

    issues = []
    for _, row in domains.iterrows():
        prop_ns, _ = get_namespace_and_name(row["ID"], base)
        if any(prop_ns.startswith(ns) for ns in owned):
            continue
        candidates = class_uris.get(row["VALUE"], [row["VALUE"]] if "://" in row["VALUE"] else [])
        if not candidates:
            issues.append({"property": row["ID"], "issue": f"rdfs:domain target '{row['VALUE']}' not resolvable to a class in this file"})
        elif len(candidates) > 1:
            issues.append({"property": row["ID"], "issue": f"rdfs:domain target '{row['VALUE']}' is ambiguous: {candidates}"})
        elif get_namespace_and_name(candidates[0], base)[0] != prop_ns:
            issues.append({"property": row["ID"], "issue": f"external property with rdfs:domain on foreign class <{candidates[0]}> - use schema:domainIncludes"})

    linked = set(domains["ID"]) | set(includes["ID"])
    for uri in properties:
        if uri not in linked:
            issues.append({"property": uri, "issue": "no rdfs:domain or schema:domainIncludes - class linkage missing"})

    single_valued = {"domain": "rdfs:domain", "range": "rdfs:range", "inverseRoleName": "cims:inverseRoleName"}
    for key, predicate in single_valued.items():
        rows = data[data["KEY"] == key]
        for uri, values in rows.groupby("ID")["VALUE"].apply(list).items():
            if len(values) > 1:
                issues.append({"property": uri, "issue": f"{len(values)}x {predicate} ({', '.join(values)}) - intersection semantics, likely merged associations"})

    return issues


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=pathlib.Path, help="RDFS files or directories (searched for *.rdf)")
    parser.add_argument("--owned-namespace", action="append", default=[], help="additional namespace prefix the profile owns (may repeat)")
    args = parser.parse_args()

    files = sorted(set(path for given in args.paths for path in (given.rglob("*.rdf") if given.is_dir() else [given])))
    owned_namespaces = OWNED_NAMESPACES + args.owned_namespace

    failed = False
    for path in files:
        issues = check_file(path, owned_namespaces)
        print(f"{'FAIL' if issues else 'OK  '} {path}")
        for issue in issues:
            print(f"       {issue['property']}: {issue['issue']}")
        failed = failed or bool(issues)

    raise SystemExit(1 if failed else 0)
