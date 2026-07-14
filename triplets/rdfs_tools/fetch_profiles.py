"""Fetch profile RDFS snapshots from upstream release branches into rdfs/<bundle>/.

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
SOURCES = {
    "ENTSOE_NC_2.4.1":   {"repo": UPSTREAM, "ref": "ncp-v2-4-1", "path": "NCP/RDFS"},
    "ENTSOE_NC_2.4.2":   {"repo": UPSTREAM, "ref": "ncp-v2-4-2", "path": "NCP/RDFS"},
    "ENTSOE_NC_2.5-dev": {"repo": UPSTREAM, "ref": "main", "path": "NCP/CurrentRelease/RDFS"},
}


def fetch(name, spec, rdfs_root=RDFS_ROOT):
    """Materialize rdfs/<name>/ from spec = {repo, ref, path} and return its path."""
    target = rdfs_root / name

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "--depth", "1", "--branch", spec["ref"],
                        "--filter=blob:none", "--sparse", spec["repo"], tmp],
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", tmp, "sparse-checkout", "set", spec["path"]],
                       check=True, capture_output=True, text=True)
        commit = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()

        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)

        for rdf_file in sorted((Path(tmp) / spec["path"]).glob("*.rdf")):
            shutil.copy(rdf_file, target / rdf_file.name)

    source = {**spec, "commit": commit, "fetched": datetime.date.today().isoformat()}
    (target / "SOURCE.json").write_text(json.dumps(source, indent=4) + "\n")

    logger.info(f"Fetched {name}: {spec['ref']}@{commit[:12]} -> {target}")
    return target


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
