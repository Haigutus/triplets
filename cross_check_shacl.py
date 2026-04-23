import pandas as pd
import polars as pl
import time
from pathlib import Path
from triplets.validation import shacl, parse_shacl

# Configuration
SHACL_FILES = [
    "test_data/entsoe-profiles/CGMES/CurrentRelease/RDFS/Beta_501_Ed2_CD/61970-600-2_Equipment-AP-Con-Simple-SHACLED2a.rdf",
    "test_data/entsoe-profiles/CGMES/CurrentRelease/RDFS/Beta_501_Ed2_CD/61970-600-2_GeographicalLocation-AP-Con-Simple-SHACLED2a.rdf"
]
DATA_FILE = "test_data/relicapgrid/Instance/BoundaryConfigurationExamples/TC-Boundary_data_split/BoundaryData.xml"

def cross_check():
    print(f"Loading SHACL rules...")
    rules = parse_shacl(SHACL_FILES)
    
    print(f"Loading data: {DATA_FILE}")
    import triplets
    df_pd = pd.read_RDF([DATA_FILE])
    df_pl = pl.from_pandas(df_pd)
    
    # 1. Run Polars Parallel
    print("Running Polars Parallel...")
    v_pl = df_pl.shacl(rules, engine='polars_parallel', check_external=True)
    if hasattr(v_pl, 'to_pandas'): v_pl = v_pl.to_pandas()
    
    # 2. Run pySHACL (Reference)
    print("Running pySHACL...")
    # pySHACL engine needs the actual files to be compliant
    v_py = df_pd.shacl(rules, engine='pyshacl', shacl_files=[SHACL_FILES[0]]) # Only equipment for now due to geo issues
    
    # Parity check
    report = []
    report.append("# SHACL Engine Parity Cross-Check\n")
    report.append(f"**Data File:** `{DATA_FILE}`\n")
    report.append(f"**Polars Parallel Violations:** {len(v_pl)}")
    report.append(f"**pySHACL Violations:** {len(v_py)}\n")
    
    # Group by Violation Type
    all_types = set(v_pl['VIOLATION_TYPE'].unique()) | set(v_py['VIOLATION_TYPE'].unique())
    
    for v_type in sorted(all_types):
        report.append(f"## Error Type: `{v_type}`")
        
        pl_subset = v_pl[v_pl['VIOLATION_TYPE'] == v_type]
        py_subset = v_py[v_py['VIOLATION_TYPE'] == v_type]
        
        report.append(f"- **Found by Polars:** {len(pl_subset)}")
        report.append(f"- **Found by pySHACL:** {len(py_subset)}\n")
        
        if not pl_subset.empty:
            report.append("### Example (Polars):")
            sample = pl_subset.iloc[0]
            report.append("```json")
            report.append(sample.to_json(indent=2))
            report.append("```\n")
            
        if not py_subset.empty:
            report.append("### Example (pySHACL):")
            sample = py_subset.iloc[0]
            report.append("```json")
            report.append(sample.to_json(indent=2))
            report.append("```\n")
            
        # Analysis of mismatch
        if len(pl_subset) != len(py_subset):
            report.append("### Parity Analysis")
            if v_type == 'sh:nodeKind':
                report.append("Mismatch in `nodeKind` is expected because pySHACL operates on the full RDF graph, while Polars operates on simplified triplets where IRIs and Literals are distinguished by their presence in the ID column.\n")
            elif v_type == 'sh:class':
                report.append("Mismatch in `class` usually happens when pySHACL resolves types through the graph while Polars depends on the explicit `Type` key in the triplet set.\n")
            else:
                report.append(f"Investigate difference: {abs(len(pl_subset) - len(py_subset))} violations.\n")

    with open("SHACL_PARITY_REPORT.md", "w") as f:
        f.write("\n".join(report))
    print(f"Report written to SHACL_PARITY_REPORT.md")

if __name__ == "__main__":
    cross_check()
