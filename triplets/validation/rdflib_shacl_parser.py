import rdflib
from rdflib import Graph, RDF, Namespace
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

# Namespaces
SH = Namespace("http://www.w3.org/ns/shacl#")

class SHACLParser:
    """Robust SHACL parser using rdflib for full RDF support."""
    
    def __init__(self, files, keep_namespaces=False):
        self.graph = Graph()
        self.files = files if isinstance(files, list) else [files]
        self.keep_namespaces = keep_namespaces
        self.constraints = defaultdict(list)
        self.stats = {'total_shapes': 0, 'total_properties': 0, 'extracted': 0}
        self._load_and_parse()

    def _get_name(self, value):
        if value is None: return None
        val_str = str(value)
        if self.keep_namespaces: return val_str
        return val_str.split('#')[-1].split('/')[-1]

    def _load_and_parse(self):
        for file_path in self.files:
            try:
                self.graph.parse(str(file_path), format='xml')
            except Exception as e:
                logger.error(f"Error parsing {file_path}: {e}")

        # Find all NodeShapes
        node_shapes = list(self.graph.subjects(RDF.type, SH.NodeShape))
        self.stats['total_shapes'] = len(node_shapes)

        for shape in node_shapes:
            target_class_uri = self.graph.value(shape, SH.targetClass)
            if not target_class_uri: continue
            target_class = self._get_name(target_class_uri)
            
            # Find property shapes linked to this NodeShape
            properties = list(self.graph.objects(shape, SH.property))
            self.stats['total_properties'] += len(properties)
            
            for prop in properties:
                constraint = self._parse_shape(prop, target_class)
                if constraint:
                    self.constraints[target_class].append(constraint)
                    self.stats['extracted'] += 1

    def _parse_shape(self, shape_uri, class_name=None):
        """Recursively parses a SHACL shape (PropertyShape or nested)."""
        path_uri = self.graph.value(shape_uri, SH.path)
        property_name = self._get_name(path_uri) if path_uri else None
        
        constraint = {'id': str(shape_uri)}
        if property_name:
            if self.keep_namespaces:
                constraint['property'] = property_name
            else:
                constraint['property'] = property_name if '.' in property_name else f"{class_name}.{property_name}"
        if class_name:
            constraint['class'] = class_name

        # Data-driven mapping for properties
        mapping = [
            (SH.name, 'rule_name', str),
            (SH.description, 'description', str),
            (SH.message, 'message', str),
            (SH.severity, 'severity', self._get_name),
            (SH.minCount, 'min_count', int),
            (SH.maxCount, 'max_count', int),
            (SH.datatype, 'datatype', lambda v: f"xsd:{self._get_name(v)}" if not self.keep_namespaces else str(v)),
            (SH['class'], 'target_class', self._get_name),
            (SH.minInclusive, 'min_inclusive', float),
            (SH.maxInclusive, 'max_inclusive', float),
            (SH.pattern, 'pattern', str),
            (SH.minLength, 'min_length', int),
            (SH.maxLength, 'max_length', int),
            (SH.node, 'sh_node', str),
        ]

        for term, key, transform in mapping:
            val = self.graph.value(shape_uri, term)
            if val is not None:
                try: constraint[key] = transform(val)
                except: pass

        # SPARQL
        sparql = self.graph.value(shape_uri, SH.sparql)
        if sparql:
            select = self.graph.value(sparql, SH.select)
            if select: constraint['sparql_query'] = str(select)

        # Logical Operators (Recursion via RDF Lists)
        for op_term, op_key in [(SH['or'], 'or'), (SH['and'], 'and')]:
            list_head = self.graph.value(shape_uri, op_term)
            if list_head:
                from rdflib.collection import Collection
                items = list(Collection(self.graph, list_head))
                constraint[op_key] = [self._parse_shape(item, class_name) for item in items]

        # SH:NOT
        not_shape = self.graph.value(shape_uri, SH['not'])
        if not_shape:
            constraint['not'] = self._parse_shape(not_shape, class_name)

        return constraint

def parse_shacl(files, keep_namespaces=False):
    """
    Parse SHACL constraints from a list of files using rdflib.
    Returns a list of constraint dictionaries.
    
    :param files: List of SHACL file paths
    :param keep_namespaces: Whether to keep full URIs instead of local names
    """
    parser = SHACLParser(files, keep_namespaces=keep_namespaces)
    all_rules = []
    for class_rules in parser.constraints.values():
        all_rules.extend(class_rules)
    return all_rules

if __name__ == "__main__":
    import sys
    from pathlib import Path
    import json
    
    # Simple test with an actual SHACL file
    shacl_file = "../../test_data/entsoe-profiles/CGMES/CurrentRelease/RDFS/Beta_501_Ed2_CD/61970-600-2_Equipment-AP-Con-Simple-SHACLED2a.rdf"
    if Path(shacl_file).exists():
        print(f"Testing SHACL Parser with: {shacl_file}")
        rules = parse_shacl([shacl_file])
        print(f"Successfully extracted {len(rules)} rules.")
        
        print("\n--- Namespace test (keep_namespaces=True) ---")
        rules_ns = parse_shacl([shacl_file], keep_namespaces=False)
        if rules_ns:
            # Find a rule with a class or datatype to show full URI
            for r in rules_ns:
                if 'target_class' in r:
                    print(f"Sample rule with full URIs:")
                    print(json.dumps(r, indent=2))
                    break
        
        # Check for complex rules
        complex_rules = [r for r in rules if 'or' in r or 'and' in r or 'not' in r or 'sparql_query' in r]
        print(f"Found {len(complex_rules)} complex rules.")
        
        if complex_rules:
            print("\nSample Complex Rule (with logical operator):")
            # Find one with 'or' specifically if possible
            or_rules = [r for r in complex_rules if 'or' in r]
            sample = or_rules[0] if or_rules else complex_rules[0]
            print(json.dumps(sample, indent=2))
    else:
        print(f"Test file not found: {shacl_file}")
