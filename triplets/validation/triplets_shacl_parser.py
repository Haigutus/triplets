import pandas
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

class SHACLParser:
    """Comprehensive SHACL parser from triplet DataFrames."""
    
    def __init__(self, df):
        self.df = df
        self.constraints = defaultdict(list)
        self.stats = {'total_shapes': 0, 'total_properties': 0, 'extracted': 0}
        self._parse()

    def _get_local_name(self, value):
        if not value or not isinstance(value, str): return value
        return value.split('#')[-1].split('/')[-1]

    def _get_rdf_list(self, list_head_id):
        """Recursively resolves an RDF list (first/rest) into a list of IDs."""
        items = []
        current = list_head_id
        while current and current != 'http://www.w3.org/1999/02/22-rdf-syntax-ns#nil' and current != 'nil':
            # Get first and rest for current node
            node_data = self.df[self.df['ID'] == current]
            first = node_data[node_data['KEY'].str.endswith('first')]['VALUE'].tolist()
            rest = node_data[node_data['KEY'].str.endswith('rest')]['VALUE'].tolist()
            
            if first: items.append(first[0])
            current = rest[0] if rest else None
        return items

    def _parse(self):
        # 1. Find all NodeShapes
        node_shapes = self.df[(self.df['KEY'].str.lower() == 'type') & (self.df['VALUE'].str.contains('NodeShape', na=False))]['ID'].unique()
        self.stats['total_shapes'] = len(node_shapes)
        
        for shape_id in node_shapes:
            # 2. Find target class
            target_class_rows = self.df[(self.df['ID'] == shape_id) & (self.df['KEY'].str.contains('targetClass', na=False, case=False))]
            if target_class_rows.empty: continue
            target_class = self._get_local_name(target_class_rows.iloc[0]['VALUE'])
            
            # 3. Find property shapes
            prop_shape_ids = self.df[(self.df['ID'] == shape_id) & (self.df['KEY'].str.contains('property', na=False, case=False)) & (self.df['KEY'].str.lower() != 'type')]['VALUE'].unique()
            self.stats['total_properties'] += len(prop_shape_ids)
            
            for prop_id in prop_shape_ids:
                constraint = self._parse_any_shape(prop_id, target_class)
                if constraint:
                    self.constraints[target_class].append(constraint)
                    self.stats['extracted'] += 1

    def _parse_any_shape(self, shape_id, class_name=None):
        """Parses a shape (NodeShape or PropertyShape) and extracts all its constraints."""
        shape_data = self.df[self.df['ID'] == shape_id]
        if shape_data.empty: return None
        
        # Map keys to local names and collect all values
        p_dict = defaultdict(list)
        for _, row in shape_data.iterrows():
            key = self._get_local_name(row['KEY'])
            p_dict[key].append(row['VALUE'])
        
        # Helper to get first value or None
        def get_first(k):
            vals = p_dict.get(k)
            return vals[0] if vals else None

        path = get_first('path')
        property_name = self._get_local_name(path) if path else None
        
        constraint = {'id': shape_id}
        if property_name:
            constraint['property'] = property_name if '.' in property_name else f"{class_name}.{property_name}"
        if class_name:
            constraint['class'] = class_name

        # Standard Mappings
        mapping = {
            'name': ('rule_name', str), 'description': ('description', str), 'message': ('message', str),
            'severity': ('severity', self._get_local_name), 'minCount': ('min_count', int), 'maxCount': ('max_count', int),
            'datatype': ('datatype', lambda v: f"xsd:{self._get_local_name(v)}"), 'class': ('target_class', self._get_local_name),
            'minInclusive': ('min_inclusive', float), 'maxInclusive': ('max_inclusive', float), 'pattern': ('pattern', str),
            'minLength': ('min_length', int), 'maxLength': ('max_length', int), 'node': ('sh_node', str)
        }
        
        for sh_term, (target_key, transform) in mapping.items():
            val = get_first(sh_term)
            if val is not None:
                try: constraint[target_key] = transform(val)
                except: pass

        # SPARQL
        sparql_id = get_first('sparql')
        if sparql_id:
            sparql_data = self.df[self.df['ID'] == sparql_id]
            select_rows = sparql_data[sparql_data['KEY'].str.contains('select', na=False, case=False)]
            if not select_rows.empty:
                constraint['sparql_query'] = select_rows.iloc[0]['VALUE']

        # Logical Operators (Recursion)
        for op in ['or', 'and']:
            if op in p_dict:
                item_ids = p_dict[op]
                resolved_items = []
                for item_id in item_ids:
                    if not item_id: continue
                    list_items = self._get_rdf_list(item_id)
                    if list_items:
                        resolved_items.extend([self._parse_any_shape(i, class_name) for i in list_items])
                    else:
                        item_shape = self._parse_any_shape(item_id, class_name)
                        if item_shape: resolved_items.append(item_shape)
                if resolved_items: constraint[op] = resolved_items

        not_id = get_first('not')
        if not_id:
            constraint['not'] = self._parse_any_shape(not_id, class_name)

        return constraint

def parse_shacl(df_or_files):
    import triplets # Ensure read_RDF is registered on pandas
    df = pandas.read_RDF(df_or_files) if not isinstance(df_or_files, pandas.DataFrame) else df_or_files
    parser = SHACLParser(df)
    all_rules = []
    for class_rules in parser.constraints.values(): all_rules.extend(class_rules)
    return all_rules

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent.parent))
    
    shacl_file = "test_data/entsoe-profiles/CGMES/CurrentRelease/RDFS/Beta_501_Ed2_CD/61970-600-2_Equipment-AP-Con-Simple-SHACLED2a.rdf"
    if Path(shacl_file).exists():
        print(f"Testing Complex SHACL Parser with: {shacl_file}")
        rules = parse_shacl([shacl_file])
        print(f"Successfully extracted {len(rules)} rules.")
        
        # Debug: check keys of first 10 rules
        for i, r in enumerate(rules[:10]):
            print(f"Rule {i} keys: {list(r.keys())}")
            
        # Find a complex rule to show (e.g. one with an 'or' or 'and' if it exists)
        complex_rules = [r for r in rules if 'or' in r or 'and' in r or 'not' in r or 'sparql_query' in r]
        print(f"Found {len(complex_rules)} complex rules.")
        if complex_rules:
            import json
            print("\nSample Complex Rule:")
            print(json.dumps(complex_rules[0], indent=2))
