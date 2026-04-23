import pandas as pd
import rdflib
from rdflib import Graph, URIRef, Literal, RDF, Namespace
import time
import logging

logger = logging.getLogger(__name__)

def _df_to_rdflib_graph(df):
    """ Converts a triplets DataFrame back to an rdflib Graph for pyshacl."""
    g = Graph()
    cim = Namespace("http://iec.ch/TC57/CIM100#")
    g.bind("cim", cim)
    
    for _, row in df.iterrows():
        s, p, o = row['ID'], row['KEY'], row['VALUE']
        subj = URIRef(s) if str(s).startswith(('http', 'urn:', '_:')) else URIRef(f"urn:uuid:{s}")
        if p == 'Type':
            pred = RDF.type
            obj = URIRef(o) if str(o).startswith('http') else cim[o]
        else:
            pred = cim[p] if not str(p).startswith('http') else URIRef(p)
            obj = URIRef(o) if isinstance(o, str) and o.startswith(('http', 'urn:')) else Literal(o)
        g.add((subj, pred, obj))
    return g

def validate(df, rules, shacl_files=None, **kwargs):
    """
    Validation engine using pySHACL library.
    NOTE: 'rules' is ignored as pySHACL loads shapes directly from 'shacl_files'.
    """
    try:
        from pyshacl import validate as pyshacl_validate
    except ImportError:
        logger.error("pyshacl library not found. Install it with 'pip install pyshacl'")
        return pd.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

    if not shacl_files:
        logger.error("pySHACL engine requires 'shacl_files' parameter.")
        return pd.DataFrame(columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "ERROR_MESSAGE"])

    # 1. Convert Data
    data_graph = _df_to_rdflib_graph(df)

    # 2. Load Shapes
    shacl_graph = Graph()
    for f in shacl_files: shacl_graph.parse(str(f), format='xml')

    # 3. Run pySHACL
    conforms, results_graph, results_text = pyshacl_validate(
        data_graph, shacl_graph=shacl_graph, inference='none', abort_on_first=False, advanced=True
    )

    # 4. Map Results to our schema
    from .shacl_report import SH
    # Map official pySHACL component URIs to our shortened internal names
    COMPONENT_MAP = {
        SH.MinCountConstraintComponent: 'sh:minCount',
        SH.MaxCountConstraintComponent: 'sh:maxCount',
        SH.DatatypeConstraintComponent: 'sh:datatype',
        SH.MinLengthConstraintComponent: 'sh:minLength',
        SH.MaxLengthConstraintComponent: 'sh:maxLength',
        SH.PatternConstraintComponent: 'sh:pattern',
        SH.MinInclusiveConstraintComponent: 'sh:minInclusive',
        SH.MaxInclusiveConstraintComponent: 'sh:maxInclusive',
        SH.ClassConstraintComponent: 'sh:class',
        SH.NodeKindConstraintComponent: 'sh:nodeKind',
        SH.InConstraintComponent: 'sh:in',
        SH.HasValueConstraintComponent: 'sh:hasValue',
        SH.EqualsConstraintComponent: 'sh:equals',
        SH.DisjointConstraintComponent: 'sh:disjoint',
        SH.LessThanConstraintComponent: 'sh:lessThan',
        SH.ClosedConstraintComponent: 'sh:closed',
        SH.OrConstraintComponent: 'sh:or',
        SH.AndConstraintComponent: 'sh:and',
        SH.NotConstraintComponent: 'sh:not',
    }

    violations = []
    for result in results_graph.subjects(RDF.type, SH.ValidationResult):
        component = results_graph.value(result, SH.sourceConstraintComponent)
        shape = results_graph.value(result, SH.sourceShape)
        sev = results_graph.value(result, SH.resultSeverity)
        
        violations.append({
            'ID': str(results_graph.value(result, SH.focusNode)),
            'KEY': str(results_graph.value(result, SH.resultPath)) if results_graph.value(result, SH.resultPath) else None,
            'VALUE': str(results_graph.value(result, SH.value)) if results_graph.value(result, SH.value) else None,
            'VIOLATION_TYPE': COMPONENT_MAP.get(component, f"sh:{str(component).split('#')[-1]}" if component else 'sh:Unknown'),
            'ERROR_MESSAGE': str(results_graph.value(result, SH.resultMessage)) if results_graph.value(result, SH.resultMessage) else None,
            'SEVERITY': str(sev).split('#')[-1] if sev else 'Violation',
            'RULE_NAME': str(shape).split('#')[-1] if shape else 'Unknown',
            'SOURCE_SHAPE': str(shape) if shape else None
        })

    return pd.DataFrame(violations)
