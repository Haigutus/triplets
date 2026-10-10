"""
CIM Diff Tool
=============

Command-line tool for comparing CIM XML (Common Information Model) files and
displaying differences in unified diff format.

This tool compares CIM files at the semantic level (object-by-object based on
ID, KEY, VALUE triplets) rather than as plain XML text. This makes it much more
useful for identifying actual data changes while ignoring irrelevant formatting
or ordering differences.

Installation
------------
Install the triplets package::

    pip install triplets

Or install from source::

    git clone https://github.com/Haigutus/triplets.git
    cd triplets
    pip install -e .

Using uv (recommended)::

    uv pip install -e .

Usage
-----
After installation, the tool can be invoked in three ways:

1. **As a command-line tool** (recommended)::

    cim-diff original.xml modified.xml

2. **As a Python module**::

    python -m triplets.cli.cim_diff original.xml modified.xml

3. **Programmatically** in Python code::

    from triplets import parse
    from triplets.tools import print_triplets_diff

    # Load both files
    original = parse(['original.xml'])
    modified = parse(['modified.xml'])

    # Print diff
    print_triplets_diff(original, modified, exclude_objects=['NamespaceMap', 'Distribution'])

Features
--------
- Semantic comparison at triplet level (ID, KEY, VALUE)
- Unified diff format output for easy reading
- Support for ZIP archives (single or nested)
- Default exclusions for metadata objects (NamespaceMap, Distribution)
- Custom exclusion lists for filtering out specific object types
- Handles large CIM files efficiently

Examples
--------
Basic diff between two CIM files::

    $ cim-diff original.xml modified.xml

Diff only some types left out (replaces the defaults)::

    $ cim-diff original.xml modified.xml -ex Terminal ConnectivityNode

Diff everything, parser metadata included::

    $ cim-diff original.xml modified.xml -ex

Diff between ZIP archives::

    $ cim-diff original.zip modified.zip

Exclusions replace the defaults; keep them by listing them::

    $ cim-diff original.xml modified.xml -ex NamespaceMap Distribution CustomType

Output Format
-------------
The tool outputs differences in unified diff format::

    --- Objects in original_file
    +++ Objects in modified_file
    - ObjectID ObjectType.attribute oldValue
    + ObjectID ObjectType.attribute newValue

Lines starting with '-' indicate values removed (in original but not in modified).
Lines starting with '+' indicate values added (in modified but not in original).

Default Exclusions
------------------
By default, the following object types are excluded from comparison as they
typically contain auto-generated metadata that changes between exports:

- NamespaceMap : XML namespace mappings (contains UUIDs)
- Distribution : File distribution metadata

Use ``-ex`` with no names to include these in the comparison.

See Also
--------
cim-spreadsheet : Tool for converting between CIM XML and spreadsheet formats
triplets.tools.print_triplets_diff : Core diff function
"""

import argparse

from ..parser import parse
from ..tools import print_triplets_diff
from . import add_exclusion_argument

def main():
    """
    CLI entry point for cim-diff tool.

    Parses command-line arguments and executes comparison of two CIM XML files,
    displaying differences in unified diff format.

    Command-Line Usage
    ------------------
    Basic usage::

        cim-diff original.xml modified.xml

    With other exclusions (replace the defaults)::

        cim-diff original.xml modified.xml -ex Terminal ACLineSegment

    Without exclusions::

        cim-diff original.xml modified.xml -ex

    Parameters
    ----------
    original_file : str (positional)
        Path to original CIM XML file or ZIP archive
    changed_file : str (positional)
        Path to modified CIM XML file or ZIP archive
    -ex, --exclude_objects : list of str, optional
        Object type names (without namespace/prefix) to exclude from diff,
        default NamespaceMap Distribution. ``-ex`` with no names excludes nothing.

    Exit Codes
    ----------
    0 : Successful comparison (differences shown on stdout)

    Notes
    -----
    The diff is performed on the semantic triplet level (ID, KEY, VALUE),
    not on raw XML text. This means:

    - XML formatting differences are ignored
    - Element ordering differences are ignored
    - Only actual data value changes are shown
    - Object type filtering happens before comparison

    See Also
    --------
    triplets.parser.parse : Loads CIM XML into triplet format
    triplets.tools.print_triplets_diff : Prints differences between triplet DataFrames
    """
    parser = argparse.ArgumentParser(
        description="""Create diff in Unified format for XML RDF CIM files.
        Diff is per object (ID KEY VALUE) not per XML line in file.
        The input can be xml, zip(xml), zip(zip(xml))""",
        epilog="""Copyright (c) Kristjan Vilgo 2026; Licence: MIT"""
    )
    parser.add_argument('original_file', type=str, help='Original file path')
    parser.add_argument('changed_file', type=str, help='Changed file path')
    add_exclusion_argument(parser, "the diff")

    args = parser.parse_args()

    # Load and compare files
    original_data = parse([args.original_file])
    changed_data = parse([args.changed_file])

    print_triplets_diff(original_data, changed_data, exclude_objects=args.exclude_objects)

if __name__ == "__main__":
    main()
