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
- Parser metadata (NamespaceMap, Distribution) left out unless ``--keep-metadata``
- Only some object types with ``--include``, more left out with ``-ex``
- Unchanged values shown for orientation with ``--context KEY``
- Counts per type only with ``--stat``
- Exit code 0 when equal (nothing printed), 1 when different, 2 on error (as ``diff``)
- Handles large CIM files efficiently

Examples
--------
Basic diff between two CIM files::

    $ cim-diff original.xml modified.xml

Also leave out some model classes::

    $ cim-diff original.xml modified.xml -ex Terminal -ex ConnectivityNode
    $ cim-diff original.xml modified.xml -ex Terminal,ConnectivityNode

Diff everything, parser metadata included::

    $ cim-diff original.xml modified.xml --keep-metadata

Only line segments, with their names, or only the counts::

    $ cim-diff original.xml modified.xml --include ACLineSegment --context IdentifiedObject.name
    $ cim-diff original.xml modified.xml --stat

Diff between ZIP archives::

    $ cim-diff original.zip modified.zip

Output Format
-------------
Counts of removed, added and changed objects per type, then one hunk per
object (sorted by type and ID), keys sorted, ``-`` before ``+``::

    --- original.xml
    +++ modified.xml

    @@ -1,0 +1,0 @@ Removed:
      Terminal 1

    @@ -1,0 +1,0 @@ Added:

    @@ -1,0 +1,0 @@ Changed:
      ACLineSegment 1

    @@ -1,2 +1,2 @@ ACLineSegment c77668c9-6993-4391-8c03-29fc802ad605
    -ACLineSegment.r -> 9.6
    +ACLineSegment.r -> 9.99
     IdentifiedObject.name -> CL15

``-`` lines are only in the original, ``+`` lines only in the modified file,
`` `` lines are unchanged values asked for with ``--context``.

Default Exclusions
------------------
By default, the following object types are excluded from comparison as they
typically contain auto-generated metadata that changes between exports:

- NamespaceMap : XML namespace mappings (contains UUIDs)
- Distribution : File distribution metadata

Use ``--keep-metadata`` to include these in the comparison.

See Also
--------
cim-spreadsheet : Tool for converting between CIM XML and spreadsheet formats
triplets.tools.print_triplets_diff : Core diff function
"""

import argparse
import logging
import sys

from ..parser import parse
from ..tools import print_triplets_diff
from . import add_common_arguments, excluded_types, names

def main():
    """
    CLI entry point for cim-diff tool.

    Parses command-line arguments and executes comparison of two CIM XML files,
    displaying differences in unified diff format.

    Command-Line Usage
    ------------------
    Basic usage::

        cim-diff original.xml modified.xml

    Also leave out some classes::

        cim-diff original.xml modified.xml -ex Terminal -ex ACLineSegment

    Without exclusions::

        cim-diff original.xml modified.xml --keep-metadata

    Parameters
    ----------
    original_file : str (positional)
        Path to original CIM XML file or ZIP archive
    changed_file : str (positional)
        Path to modified CIM XML file or ZIP archive
    -ex, --exclude_objects : str, repeatable
        Object type name (without namespace/prefix) left out of the diff;
        repeat or comma-separate for more.
    --keep-metadata : flag
        Also diff NamespaceMap and Distribution, left out by default.

    Exit Codes
    ----------
    0 : No differences
    1 : Differences (shown on stdout)
    2 : Error (bad arguments, unreadable file)

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
    add_common_arguments(parser, "the diff")
    parser.add_argument('--context', action='extend', type=names, default=[], metavar='KEY',
                        help='Also show the unchanged values of these keys in each changed object, '
                             'e.g. --context IdentifiedObject.name. Repeat or comma-separate for more')
    parser.add_argument('--stat', action='store_true', help='Only the removed / added / changed counts per type')

    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    try:
        original_data = parse([args.original_file])
        changed_data = parse([args.changed_file])
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(2)

    differences = print_triplets_diff(original_data, changed_data, exclude_objects=excluded_types(args),
                                      include_objects=args.include, context_keys=args.context, stat=args.stat)
    sys.exit(1 if differences else 0)

if __name__ == "__main__":
    main()
