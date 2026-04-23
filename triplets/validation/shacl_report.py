import rdflib
from rdflib import Graph, Literal, RDF, URIRef, Namespace, BNode
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Namespaces
SH = Namespace("http://www.w3.org/ns/shacl#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")

# Mapping from our internal VIOLATION_TYPE to official SHACL ConstraintComponent URIs
CONSTRAINT_COMPONENTS = {
    'sh:minCount': SH.MinCountConstraintComponent,
    'sh:maxCount': SH.MaxCountConstraintComponent,
    'sh:datatype': SH.DatatypeConstraintComponent,
    'sh:minLength': SH.MinLengthConstraintComponent,
    'sh:maxLength': SH.MaxLengthConstraintComponent,
    'sh:pattern': SH.PatternConstraintComponent,
    'sh:minInclusive': SH.MinInclusiveConstraintComponent,
    'sh:maxInclusive': SH.MaxInclusiveConstraintComponent,
    'sh:class': SH.ClassConstraintComponent,
    'sh:nodeKind': SH.NodeKindConstraintComponent,
    'sh:equals': SH.EqualsConstraintComponent,
    'sh:disjoint': SH.DisjointConstraintComponent,
    'sh:lessThan': SH.LessThanConstraintComponent,
    'sh:closed': SH.ClosedConstraintComponent,
    'sh:hasValue': SH.HasValueConstraintComponent,
    'sh:in': SH.InConstraintComponent,
    'sh:or': SH.OrConstraintComponent,
    'sh:and': SH.AndConstraintComponent,
    'sh:not': SH.NotConstraintComponent,
}

# Mapping for Severities
SEVERITIES = {
    'Violation': SH.Violation,
    'Warning': SH.Warning,
    'Info': SH.Info,
}

RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
DCTERMS = Namespace("http://purl.org/dc/terms/")

def generate_shacl_report(violations_data, validated_file=None, shacl_files=None):
    """
    Converts violations data to a standard SHACL Validation Report (RDF/XML).

    :param violations_data: pandas.DataFrame, polars.DataFrame or list of dicts
    :param validated_file: name of the file that was validated
    :param shacl_files: list of SHACL file names used for validation
    :return: rdflib.Graph containing the SHACL report
    """
    # Normalize to list of dicts for uniform processing
    if hasattr(violations_data, 'to_pandas'):
        df = violations_data.to_pandas()
        rows = [row.to_dict() for _, row in df.iterrows()]
    elif isinstance(violations_data, pd.DataFrame):
        rows = [row.to_dict() for _, row in violations_data.iterrows()]
    else:
        rows = violations_data

    g = Graph()
    g.bind("sh", SH)
    g.bind("xsd", XSD)
    g.bind("rdfs", RDFS)
    g.bind("dcterms", DCTERMS)

    report = BNode()
    g.add((report, RDF.type, SH.ValidationReport))

    conforms = len(rows) == 0
    g.add((report, SH.conforms, Literal(conforms, datatype=XSD.boolean)))

    # Add validation datetime
    from datetime import datetime, timezone
    PROV = Namespace("http://www.w3.org/ns/prov#")
    g.bind("prov", PROV)
    g.add((report, PROV.generatedAtTime, Literal(datetime.now(timezone.utc).isoformat(), datatype=XSD.dateTime)))

    # Add tool name and version
    import triplets as _triplets
    g.add((report, PROV.wasGeneratedBy, Literal(f"triplets {_triplets.__version__}")))

    # Add validated file name
    if validated_file:
        g.add((report, DCTERMS.source, Literal(validated_file)))

    # Add SHACL files used
    if shacl_files:
        for sf in shacl_files:
            g.add((report, DCTERMS.conformsTo, Literal(sf)))

    for row in rows:
        result = BNode()
        g.add((report, SH.result, result))
        g.add((result, RDF.type, SH.ValidationResult))

        # focusNode (ID or object_id)
        focus_node = row.get('ID') or row.get('object_id')
        if focus_node:
            if isinstance(focus_node, str) and (focus_node.startswith('http') or focus_node.startswith('urn:')):
                g.add((result, SH.focusNode, URIRef(focus_node)))
            else:
                g.add((result, SH.focusNode, Literal(focus_node)))

        # resultPath (KEY or property)
        path = row.get('KEY') or row.get('property')
        if path:
            if isinstance(path, str) and (path.startswith('http') or path.startswith('urn:')):
                g.add((result, SH.resultPath, URIRef(path)))
            else:
                g.add((result, SH.resultPath, Literal(path)))

        # value (VALUE or actual)
        val = row.get('VALUE')
        if val is None: val = row.get('actual')
        
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            if isinstance(val, str) and (val.startswith('http') or val.startswith('urn:')):
                g.add((result, SH.value, URIRef(val)))
            else:
                g.add((result, SH.value, Literal(val)))

        # resultSeverity (SEVERITY or severity)
        sev_str = row.get('SEVERITY') or row.get('severity', 'Violation')
        g.add((result, SH.resultSeverity, SEVERITIES.get(sev_str, SH.Violation)))

        # resultMessages (SHACL allows multiple result messages)
        
        # 1. Technical Error Message
        err_msg = row.get('ERROR_MESSAGE')
        if err_msg:
            g.add((result, SH.resultMessage, Literal(f"Technical Error: {err_msg}")))
            
        # 2. Original Rule Message
        rule_msg = row.get('MESSAGE') or row.get('message')
        if rule_msg:
            g.add((result, SH.resultMessage, Literal(rule_msg)))
            
        # 3. Rule Description
        desc = row.get('DESCRIPTION') or row.get('description')
        if desc:
            g.add((result, SH.resultMessage, Literal(f"Description: {desc}")))

        # sourceConstraintComponent (VIOLATION_TYPE or constraint_type)
        v_type = row.get('VIOLATION_TYPE') or row.get('constraint_type')
        if v_type and not v_type.startswith('sh:'):
            from .pandas_shacl import INTERNAL_TO_SHACL
            v_type = INTERNAL_TO_SHACL.get(v_type, v_type)
            
        if v_type in CONSTRAINT_COMPONENTS:
            g.add((result, SH.sourceConstraintComponent, CONSTRAINT_COMPONENTS[v_type]))

        # sourceShape (SOURCE_SHAPE or id)
        source_shape = row.get('SOURCE_SHAPE') or row.get('id')
        if source_shape:
            if isinstance(source_shape, str) and (source_shape.startswith('http') or source_shape.startswith('urn:')):
                g.add((result, SH.sourceShape, URIRef(source_shape)))
            else:
                g.add((result, SH.sourceShape, Literal(source_shape)))

    return g

def write_shacl_report(violations_data, output_path, format='xml', validated_file=None, shacl_files=None):
    """Helper to generate and write the report to a file."""
    g = generate_shacl_report(violations_data, validated_file=validated_file, shacl_files=shacl_files)
    g.serialize(destination=output_path, format=format)
    return output_path
