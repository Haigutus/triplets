"""Tests for the pyshacl reference SHACL engine (triplets.validation)."""
import os
import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

import pandas
import triplets
from pathlib import Path

SVEDALA_DIR = Path("test_data/relicapgrid/Instance/Grid/IGM_Svedala")
SVEDALA_EQ = str(SVEDALA_DIR / "20220615T2230Z__Svedala_EQ_1.xml")
SKIP_REASON = "Svedala test data not available"

# Inline shape (written to tmp) — deterministic, no committed shape files / external repo.
INLINE_SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
                  sh:message "ACLineSegment must have a name" ] ;
    sh:property [ sh:path cim:Conductor.length ; sh:datatype xsd:float ;
                  sh:message "Conductor.length must be xsd:float" ] .
"""

# Real CGMES SHACL shapes — external, skip-guarded (not vendored into the repo).
CGMES_SHACL_DIR = Path(os.environ.get(
    "TRIPLETS_CGMES_SHACL",
    "/home/kvilgo/GIT/application-profiles-library/CGMES/CurrentRelease/SHACL"))
CGMES_EQ_SHACL = CGMES_SHACL_DIR / "61970-301_Equipment-AP-Con-Complex-SHACL.ttl"


@pytest.fixture(scope="module")
def svedala_eq():
    if not Path(SVEDALA_EQ).exists():
        pytest.skip(SKIP_REASON)
    return pandas.read_RDF([SVEDALA_EQ])


@pytest.fixture(scope="module")
def shape_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("shapes") / "inline.ttl"
    path.write_text(INLINE_SHAPE)
    return str(path)


def test_typed_data_conforms(svedala_eq, shape_file):
    """With rdf_map, Conductor.length is xsd:float → datatype constraint passes."""
    from triplets.export_schema import schemas
    violations = svedala_eq.shacl.validate(shape_file, rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
    assert isinstance(violations, pandas.DataFrame)
    assert len(violations) == 0


def test_untyped_data_trips_datatype(svedala_eq, shape_file):
    """Without rdf_map, Conductor.length is a plain string → xsd:float violations."""
    violations = svedala_eq.shacl.validate(shape_file)
    assert len(violations) > 0
    assert (violations["VIOLATION_TYPE"] == "sh:datatype").all()


def test_violations_columns(svedala_eq, shape_file):
    from triplets.validation.shacl_report import VIOLATION_COLUMNS
    violations = svedala_eq.shacl.validate(shape_file)
    assert list(violations.columns) == VIOLATION_COLUMNS
    # focusNode stripped to bare UUID (no urn:uuid:)
    assert not violations["ID"].str.startswith("urn:uuid:").any()


def test_scope_excludes_out_of_scope_instances(svedala_eq, shape_file):
    """Scoping to an instance without ACLineSegments yields no violations."""
    instance = str(svedala_eq["INSTANCE_ID"].astype(str).iloc[0])
    in_scope = svedala_eq.shacl.validate(shape_file, scope=[instance])
    assert len(in_scope) > 0  # the EQ instance has the ACLineSegments
    out_scope = svedala_eq.shacl.validate(shape_file, scope=["00000000-0000-0000-0000-000000000000"])
    assert len(out_scope) == 0


@pytest.mark.performance  # pyshacl on the full complex CGMES SHACL takes minutes — opt-in
@pytest.mark.skipif(not CGMES_EQ_SHACL.exists(),
                    reason="external CGMES SHACL shapes not available")
def test_real_cgmes_eq_shapes(svedala_eq):
    """Validate Svedala EQ against the real CGMES Equipment SHACL profile."""
    from triplets.export_schema import schemas
    from triplets.validation.shacl_report import VIOLATION_COLUMNS
    violations = svedala_eq.shacl.validate(str(CGMES_EQ_SHACL), rdf_map=schemas.ENTSOE_CGMES_3_0_0_552_ED1)
    assert list(violations.columns) == VIOLATION_COLUMNS
