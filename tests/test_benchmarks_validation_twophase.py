"""Two-phase validation vs one-pass report collection on RealGrid.

Issue: https://github.com/Haigutus/triplets/issues/122

Hypothesis: cheaper to decide whether a constraint fails, then collect
report bindings only for the hits. These benches measure that split on
RealGrid (CGMES 2.4, ~1.14M rows / 7561 ACLineSegment) and are written to
show both wins and losses.

Three SPARQL strategies
  one_phase        authored SELECT (filter + report vars in one query)
  ask_then_select  ASK(filter); if true, the same SELECT  — naive two-phase
  ids_then_values  skinny SELECT DISTINCT ?this; fat SELECT with VALUES
                   bound only to those IDs — restricted collection

Expected direction (RealGrid ACLineSegment; oxigraph-sized engines):
  clean_fat     0 hits, fat OPTIONAL joins. ASK is false → skip SELECT.
                Two-phase should win by a lot (the fat query never runs).
  rare_fat      1 hit (the single 6-char name). ask_then_select still runs
                the fat SELECT over the whole class → loss. ids_then_values
                only wins if the fat projection dominates the filter scan;
                if the scan is the cost, two queries lose to one.
  mass_fat      7561 hits (none have IdentifiedObject.description). both
                two-phase strategies still pay the fat scan, plus a round
                trip → loss (ids_then_values worst: skinny + VALUES fat)
  mass_skinny   same 7561 hits, SELECT only ?this. two-phase is an extra
                ASK/skinny with no projection to save → loss or tie

IR (pandas compiled engine, one constraint):
  one_phase              validate() as today
  exists_then_validate   cheap pandas predicate; skip validate() when empty

  clean (name minCount 1)     → exists_then_validate should win (skip)
  rare (name minLength 7)     → 1 hit, still runs full validate → loss
  mass (description minCount) → 7561 hits, extra exists scan → loss

Deselected by default (`-m "not performance"`). Needs the RealGrid LFS zip
and a non-rdflib SPARQL engine (oxigraph or qlever) for the SPARQL group.

  pytest tests/test_benchmarks_validation_twophase.py -m performance -v
"""
import pandas
import pytest

import triplets
from triplets.export.nquads_utils import make_subject
from triplets.validation.shacl_report import VIOLATION_COLUMNS

from _parity import REALGRID_ZIP, REALGRID_SKIP_REASON

pytest.importorskip("rdflib")

pytestmark = pytest.mark.performance

PREFIX = """PREFIX cim: <http://iec.ch/TC57/CIM100#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
"""

# Report-data joins mixed into the filter. On a clean constraint these run
# for every ACLineSegment even though the FILTER never matches.
FAT = """
  OPTIONAL { ?this cim:IdentifiedObject.name ?name }
  OPTIONAL { ?this cim:ACLineSegment.r ?r }
  OPTIONAL { ?this cim:ACLineSegment.x ?x }
  OPTIONAL { ?this cim:ACLineSegment.bch ?bch }
  OPTIONAL { ?this cim:Equipment.EquipmentContainer ?container .
             ?container cim:IdentifiedObject.name ?containerName }
  OPTIONAL { ?term cim:Terminal.ConductingEquipment ?this .
             ?term cim:IdentifiedObject.name ?termName }
"""
FAT_VARS = "?this ?name ?r ?x ?bch ?containerName ?termName"

# RealGrid: every ACLineSegment has IdentifiedObject.name; none have
# IdentifiedObject.description; exactly one ACLineSegment name has length 6.
SPARQL_CASES = {
    "clean_fat": {
        "filter": ("?this rdf:type cim:ACLineSegment . "
                   "FILTER NOT EXISTS { ?this cim:IdentifiedObject.name ?n }"),
        "fat": True,
        "expect": 0,
        "tradeoff": "two-phase should win (skip fat SELECT)",
    },
    "rare_fat": {
        "filter": ("?this rdf:type cim:ACLineSegment . "
                   "?this cim:IdentifiedObject.name ?nm . "
                   "FILTER (STRLEN(?nm) = 6)"),
        "fat": True,
        "expect": 1,
        "tradeoff": "ask_then_select should lose; ids_then_values wins only if fat projection dominates",
    },
    "mass_fat": {
        "filter": ("?this rdf:type cim:ACLineSegment . "
                   "FILTER NOT EXISTS { ?this cim:IdentifiedObject.description ?d }"),
        "fat": True,
        "expect": 7561,
        "tradeoff": "two-phase should lose (still pays fat SELECT)",
    },
    "mass_skinny": {
        "filter": ("?this rdf:type cim:ACLineSegment . "
                   "FILTER NOT EXISTS { ?this cim:IdentifiedObject.description ?d }"),
        "fat": False,
        "expect": 7561,
        "tradeoff": "two-phase should lose (no projection to save)",
    },
}

IR_CASES = {
    "clean": {
        "body": "sh:path cim:IdentifiedObject.name ; sh:minCount 1",
        "exists": lambda data: _missing_count(data, "ACLineSegment",
                                              "IdentifiedObject.name") > 0,
        "expect": 0,
        "tradeoff": "exists_then_validate should win (skip validate)",
    },
    "rare": {
        "body": "sh:path cim:IdentifiedObject.name ; sh:minLength 7",
        "exists": lambda data: _min_length_hits(data, "ACLineSegment",
                                                "IdentifiedObject.name", 7) > 0,
        "expect": 1,
        "tradeoff": "exists_then_validate should lose (1 hit still validates)",
    },
    "mass": {
        "body": "sh:path cim:IdentifiedObject.description ; sh:minCount 1",
        "exists": lambda data: _missing_count(data, "ACLineSegment",
                                              "IdentifiedObject.description") > 0,
        "expect": 7561,
        "tradeoff": "exists_then_validate should lose (extra exists scan)",
    },
}


def _empty():
    return pandas.DataFrame(columns=VIOLATION_COLUMNS)


def _class_ids(data, target):
    return data.loc[(data["KEY"] == "Type") & (data["VALUE"] == target), "ID"].unique()


def _missing_count(data, target, path):
    focus = pandas.Index(_class_ids(data, target))
    having = pandas.Index(data.loc[data["KEY"] == path, "ID"].unique())
    return int(focus.difference(having).size)


def _min_length_hits(data, target, path, minimum):
    focus = pandas.Index(_class_ids(data, target))
    rows = data.loc[data["KEY"] == path]
    rows = rows[rows["ID"].isin(focus)]
    return int((rows["VALUE"].astype(str).str.len() < minimum).sum())


def _select(filter_pattern, fat):
    projection = FAT_VARS if fat else "?this"
    extra = FAT if fat else ""
    return f"SELECT DISTINCT {projection} WHERE {{ {filter_pattern} {extra} }}"


def _iri(term):
    text = str(term)
    if text.startswith("<"):
        return text
    return make_subject(text)


def _sparql(data, engine, text):
    return triplets.sparql.query(data, PREFIX + text, engine=engine, data_unchanged=True)


def _n_focus(result):
    """Violating focus-node count (OPTIONAL joins must not inflate this)."""
    if result is None or result is False:
        return 0
    if result is True:
        return 1
    if "this" in result.columns:
        return int(result["this"].nunique())
    return len(result)


def run_sparql(data, engine, case, approach):
    spec = SPARQL_CASES[case]
    filt, fat = spec["filter"], spec["fat"]
    select = _select(filt, fat)
    if approach == "one_phase":
        return _n_focus(_sparql(data, engine, select))
    if approach == "ask_then_select":
        if not _sparql(data, engine, f"ASK {{ {filt} }}"):
            return 0
        return _n_focus(_sparql(data, engine, select))
    if approach == "ids_then_values":
        hits = _sparql(data, engine, f"SELECT DISTINCT ?this WHERE {{ {filt} }}")
        n = _n_focus(hits)
        if n == 0 or not fat:
            return n
        values = " ".join(_iri(term) for term in hits["this"].unique())
        fat_select = (f"SELECT DISTINCT {FAT_VARS} WHERE {{ "
                      f"VALUES ?this {{ {values} }} {FAT} }}")
        collected = _sparql(data, engine, fat_select)
        assert _n_focus(collected) == n
        return n
    raise ValueError(approach)


def run_ir(data, compiled, case, approach):
    spec = IR_CASES[case]
    if approach == "one_phase":
        return triplets.validation.validate(data, compiled, engine="pandas")
    if approach == "exists_then_validate":
        if not spec["exists"](data):
            return _empty()
        return triplets.validation.validate(data, compiled, engine="pandas")
    raise ValueError(approach)


@pytest.fixture(scope="module")
def realgrid():
    from pathlib import Path
    if not Path(REALGRID_ZIP).exists():
        pytest.skip(REALGRID_SKIP_REASON)
    return pandas.read_RDF([REALGRID_ZIP])


@pytest.fixture(scope="module")
def sparql_ready(realgrid):
    """Ingest RealGrid into the auto SPARQL engine once; skip rdflib."""
    engine, _ = triplets.sparql.get_engine("auto")
    if engine == "rdflib":
        pytest.skip("RealGrid SPARQL two-phase benches need oxigraph or qlever")
    triplets.sparql.query(realgrid, PREFIX + "ASK { ?s rdf:type cim:ACLineSegment }",
                          engine=engine)
    n = int(realgrid.loc[(realgrid["KEY"] == "Type")
                         & (realgrid["VALUE"] == "ACLineSegment"), "ID"].nunique())
    if n != 7561:
        pytest.skip(f"RealGrid ACLineSegment count changed ({n}, expected 7561)")
    return realgrid, engine


@pytest.fixture(scope="module")
def ir_compiled():
    pytest.importorskip("pyshacl")
    import rdflib
    compiled = {}
    for name, spec in IR_CASES.items():
        turtle = f"""
        @prefix sh:  <http://www.w3.org/ns/shacl#> .
        @prefix cim: <http://iec.ch/TC57/CIM100#> .
        cim:Shape a sh:NodeShape ; sh:targetClass cim:ACLineSegment ;
            sh:property [ {spec["body"]} ] .
        """
        graph = rdflib.Graph()
        graph.parse(data=turtle, format="turtle")
        compiled[name] = triplets.validation.compile(graph)
    return compiled


@pytest.mark.benchmark(group="twophase-sparql")
@pytest.mark.parametrize("approach", ["one_phase", "ask_then_select", "ids_then_values"])
@pytest.mark.parametrize("case", list(SPARQL_CASES))
def test_sparql_twophase(benchmark, sparql_ready, case, approach):
    data, engine = sparql_ready
    spec = SPARQL_CASES[case]
    benchmark.extra_info.update({
        "case": case, "approach": approach, "engine": engine,
        "expect": spec["expect"], "tradeoff": spec["tradeoff"],
    })
    n = benchmark(lambda: run_sparql(data, engine, case, approach))
    assert n == spec["expect"], f"{case}/{approach} hit {n}, expected {spec['expect']}"


@pytest.mark.benchmark(group="twophase-ir")
@pytest.mark.parametrize("approach", ["one_phase", "exists_then_validate"])
@pytest.mark.parametrize("case", list(IR_CASES))
def test_ir_twophase(benchmark, realgrid, ir_compiled, case, approach):
    spec = IR_CASES[case]
    compiled = ir_compiled[case]
    benchmark.extra_info.update({
        "case": case, "approach": approach,
        "expect": spec["expect"], "tradeoff": spec["tradeoff"],
    })
    violations = benchmark(lambda: run_ir(realgrid, compiled, case, approach))
    n = len(violations)
    assert n == spec["expect"], f"{case}/{approach} hit {n}, expected {spec['expect']}"
