"""Compare SHACL subclass handling: pyshacl ont_graph vs IR fan-out vs Type-index walk.

The two inheritance PRs (#117 fan-out, #121 walk) sit on #120 (pyshacl
``ont_graph``). This script times them on Relicapgrid Svedala IGM and RealGrid,
with a few shapes rewritten from concrete leaf classes to abstract parents
(``cim:Equipment``, ``cim:EquipmentContainer``) — the case inheritance exists
for. Vectorized engine is polars.

Worktrees (created next to this clone):
  triplets-wt-exact   feat/shacl-subclass-iris   (#120, exact Type)
  triplets-wt-fanout  feat/shacl-inheritance     (#117)
  triplets-wt-walk    feat/shacl-inheritance-walk (#121)

Run:
  uv run python examples/bench_shacl_inheritance.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WT_ROOT = REPO.parent
DATA = REPO / "test_data"
SVEDALA = DATA / "relicapgrid/Instance/Grid/IGM_Svedala"
REALGRID = DATA / "TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip"

IMPLS = {
    "exact": WT_ROOT / "triplets-wt-exact",
    "fanout": WT_ROOT / "triplets-wt-fanout",
    "walk": WT_ROOT / "triplets-wt-walk",
}

# Concrete leaf vs the same constraints retargeted at an abstract parent.
# (Published CGMES Simple SHACL expands inherited properties onto each leaf;
#  these are that rewrite.)
SHAPES = {
    "concrete": """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] ;
    sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ; sh:minInclusive 0.0 ] .
""",
    "abstract-target": """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:EquipmentShape a sh:NodeShape ;
    sh:targetClass cim:Equipment ;
    sh:property [ sh:path cim:IdentifiedObject.description ; sh:minCount 1 ] .
""",
    "abstract-class": """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:ContainedShape a sh:NodeShape ;
    sh:targetClass cim:Equipment ;
    sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:class cim:EquipmentContainer ] .
""",
}

DATASETS = ("svedala", "realgrid")
# pyshacl is the same ont_graph on all three branches — run it on walk only.
CELLS = (
    ("walk", "pyshacl"),
    ("exact", "polars"),
    ("fanout", "polars"),
    ("walk", "polars"),
)


def _worker(args):
    impl = Path(args.impl_root).resolve()
    sys.path.insert(0, str(impl))
    sys.path = [p for p in sys.path if Path(p).resolve() != REPO]
    import triplets  # noqa: PLC0415 — after path rewrite
    from triplets.export_schema import schemas  # noqa: PLC0415

    if args.dataset == "svedala":
        files = sorted(str(p) for p in SVEDALA.glob("*.xml"))
        rdf_map = schemas.ENTSOE_CGMES_3_0_0_552_ED1
    else:
        files = [str(REALGRID)]
        rdf_map = schemas.ENTSOE_CGMES_2_4_15_552_ED1
    data = __import__("pandas").read_RDF(files)
    import polars
    frame = polars.from_pandas(data)

    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=SHAPES[args.shape], format="turtle")
    compiled = triplets.validation.compile(graph)
    payload = frame if args.engine == "polars" else data

    def once():
        return triplets.validation.validate(
            payload, compiled, engine=args.engine, rdf_map=rdf_map, lexical=False)

    timed = []
    t0 = time.perf_counter()
    result = once()
    timed.append((time.perf_counter() - t0) * 1000)
    ir_rows = len(compiled.ir)
    for key, bound in compiled.plans.items():
        if str(key).startswith("inherit") and getattr(bound, "ir", None) is not None:
            ir_rows = len(bound.ir)
    rounds = 1 if args.engine == "pyshacl" else 3
    for _ in range(rounds):
        t0 = time.perf_counter()
        result = once()
        timed.append((time.perf_counter() - t0) * 1000)
    warm = timed[1:] if len(timed) > 1 else timed
    print(json.dumps({
        "impl": args.impl, "engine": args.engine, "dataset": args.dataset,
        "shape": args.shape, "ir_rows": int(ir_rows),
        "violations": int(len(result)),
        "first_ms": round(timed[0], 2),
        "warm_ms": round(min(warm), 2),
        "triplets": str(Path(triplets.__file__).resolve().parent.parent),
    }))


def _run_cell(impl, engine, dataset, shape):
    cmd = [sys.executable, str(Path(__file__).resolve()),
           "--worker", "--impl", impl, "--impl-root", str(IMPLS[impl]),
           "--engine", engine, "--dataset", dataset, "--shape", shape]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    if not lines:
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f"no JSON from {impl}/{engine}/{dataset}/{shape}\n{proc.stdout}")
    return json.loads(lines[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--impl")
    parser.add_argument("--impl-root")
    parser.add_argument("--engine")
    parser.add_argument("--dataset")
    parser.add_argument("--shape")
    args = parser.parse_args()
    if args.worker:
        _worker(args)
        return

    missing = [name for name, path in IMPLS.items() if not path.exists()]
    if missing:
        sys.exit(f"worktrees missing: {missing}. "
                 f"git worktree add {WT_ROOT}/triplets-wt-<name> <branch>")
    if not SVEDALA.exists() or not REALGRID.exists():
        sys.exit("need Relicapgrid Svedala IGM and RealGrid zip under test_data/")

    rows = []
    for dataset in DATASETS:
        for shape in SHAPES:
            for impl, engine in CELLS:
                print(f"... {dataset:8s} {shape:16s} {impl:6s} {engine}", flush=True)
                rows.append(_run_cell(impl, engine, dataset, shape))

    print()
    header = f"{'dataset':8s} {'shape':16s} {'impl':6s} {'engine':8s} {'ir':>5s} {'viol':>6s} {'first':>9s} {'warm':>9s}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['dataset']:8s} {row['shape']:16s} {row['impl']:6s} {row['engine']:8s} "
              f"{row['ir_rows']:5d} {row['violations']:6d} {row['first_ms']:8.1f}ms {row['warm_ms']:8.1f}ms")


if __name__ == "__main__":
    main()
