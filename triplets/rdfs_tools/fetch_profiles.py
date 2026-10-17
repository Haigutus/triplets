"""Fetch profile RDFS snapshots from upstream release branches into rdfs/<bundle>/.

The PROF descriptors (``prof_path``) go to rdfs/<bundle>/PROF/: they hold the profile
IRI instances declare in ``dcterms:conformsTo``, which the RDFS does not carry. A spec
with only ``prof_path`` refreshes PROF/ alone (CGMES: the RDFS is a legacy snapshot) and
keeps its SOURCE.json there.

Each snapshot is committed to this repo together with a SOURCE.json recording the
upstream repo, ref, commit and fetch date, so schema generation stays reproducible
offline while the provenance of every bundle remains visible.

Usage: python -m triplets.rdfs_tools.fetch_profiles [bundle ...]   (default: all)
"""
import argparse
import datetime
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parents[2]
RDFS_ROOT = REPO_ROOT / "rdfs"

UPSTREAM = "https://github.com/entsoe/application-profiles-library.git"
CGMES_UPSTREAM = "https://github.com/entsoe/CGMES-AP.git"
SOURCES = {
    "ENTSOE_CGMES_3.0.0": {"repo": CGMES_UPSTREAM, "ref": "main", "prof_path": "CGMES/PROF"},
    "ENTSOE_NC_2.4.1": {"repo": UPSTREAM, "ref": "ncp-v2-4-1", "path": "NCP/RDFS", "prof_path": "NCP/PROF"},
    # DatasetMetadata class linkage via schema:domainIncludes (application-profiles-library#99, fixes #92)
    "ENTSOE_NC_2.5.0": {"repo": UPSTREAM, "ref": "ncp-v2-5-0", "path": "NCP/RDFS", "prof_path": "NCP/PROF"},
}


def fetch(name, spec, rdfs_root=RDFS_ROOT):
    """Materialize rdfs/<name>/ and/or rdfs/<name>/PROF/ from spec = {repo, ref[, path][, prof_path]}
    and return the directory holding SOURCE.json."""
    target = rdfs_root / name

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "--depth", "1", "--branch", spec["ref"],
                        "--filter=blob:none", "--sparse", spec["repo"], tmp],
                       check=True, capture_output=True, text=True)
        paths = {}
        if "path" in spec:
            paths[spec["path"]] = target
        if "prof_path" in spec:
            paths[spec["prof_path"]] = target / "PROF"
        subprocess.run(["git", "-C", tmp, "sparse-checkout", "set", *paths],
                       check=True, capture_output=True, text=True)
        commit = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()

        for upstream_path, directory in paths.items():   # target before target/PROF
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True)
            for rdf_file in sorted((Path(tmp) / upstream_path).glob("*.rdf")):
                shutil.copy(rdf_file, directory / rdf_file.name)

    source_dir = next(iter(paths.values()))
    source = {**spec, "commit": commit, "fetched": datetime.date.today().isoformat()}
    (source_dir / "SOURCE.json").write_text(json.dumps(source, indent=4) + "\n")

    logger.info(f"Fetched {name}: {spec['ref']}@{commit[:12]} -> {source_dir}")
    return source_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundles", nargs="*", choices=[[], *SOURCES], default=[],
                        help="bundle names to fetch (default: all)")
    args = parser.parse_args(argv)

    for name in args.bundles or SOURCES:
        fetch(name, SOURCES[name])


if __name__ == "__main__":
    import sys
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    main()
