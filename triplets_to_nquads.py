"""Quick N-Quads export from triplets DataFrame using rdf_parser.

Converts the [ID, KEY, VALUE, INSTANCE_ID] DataFrame to N-Quads format
where each quad is: <subject> <predicate> <object> <graph> .

Mapping:
- ID -> urn:uuid:{ID} (subject)
- KEY -> mapped to predicate URI
- VALUE -> object (URI or literal)
- INSTANCE_ID -> urn:uuid:{INSTANCE_ID} (named graph)
"""

import sys
from pathlib import Path
import triplets


CIM_NS = "http://iec.ch/TC57/CIM100#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def _make_uri(value: str) -> str:
    """Wrap a value as a URI, adding urn:uuid: prefix if needed."""
    if value.startswith("http://") or value.startswith("https://") or value.startswith("urn:"):
        return f"<{value}>"
    return f"<urn:uuid:{value}>"


def _make_predicate(key: str) -> str:
    """Convert KEY to a predicate URI."""
    if key == "Type":
        return f"<{RDF_TYPE}>"
    if key.startswith("http://") or key.startswith("https://"):
        return f"<{key}>"
    # CIM properties: ClassName.propertyName -> CIM namespace
    return f"<{CIM_NS}{key}>"


def _make_object(key: str, value: str) -> str:
    """Convert VALUE to an object (URI or literal)."""
    if key == "Type":
        # rdf:type values are class references
        if value.startswith("http://") or value.startswith("urn:"):
            return f"<{value}>"
        return f"<{CIM_NS}{value}>"

    # Check if it looks like a URI reference
    if (value.startswith("http://") or value.startswith("https://") or
        value.startswith("urn:") or value.startswith("#")):
        if value.startswith("#"):
            return f"<{CIM_NS}{value[1:]}>"
        return f"<{value}>"

    # Check if it looks like a UUID reference (common in CGMES)
    import re
    if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', value):
        return f"<urn:uuid:{value}>"

    # Literal value - escape for N-Triples
    escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return f'"{escaped}"'


def dataframe_to_nquads(df, output_path: str):
    """Convert triplets DataFrame to N-Quads file.

    Args:
        df: DataFrame with columns [ID, KEY, VALUE, INSTANCE_ID]
        output_path: Path to write the .nq file
    """
    with open(output_path, 'w') as f:
        for _, row in df.iterrows():
            subj = _make_uri(str(row.iloc[0]))
            pred = _make_predicate(str(row.iloc[1]))
            obj = _make_object(str(row.iloc[1]), str(row.iloc[2]))
            graph = _make_uri(str(row.iloc[3]))
            f.write(f"{subj} {pred} {obj} {graph} .\n")


def main():
    if len(sys.argv) < 3:
        print("Usage: python triplets_to_nquads.py <input_xml> [input_xml ...] <output.nq>")
        sys.exit(1)

    output_path = sys.argv[-1]
    input_files = sys.argv[1:-1]

    print(f"Loading {len(input_files)} file(s)...")
    df = triplets.rdf_parser.load_all_to_dataframe(input_files)
    print(f"  {len(df)} triplets loaded")

    print(f"Writing N-Quads to {output_path}...")
    dataframe_to_nquads(df, output_path)

    from pathlib import Path
    size = Path(output_path).stat().st_size
    print(f"  Done: {size / 1024:.1f} KB ({len(df)} quads)")


if __name__ == "__main__":
    main()
