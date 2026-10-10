"""
CLI tools for the triplets package.
"""

__all__ = ['cim_spreadsheet', 'cim_diff', 'DEFAULT_EXCLUSIONS', 'add_exclusion_argument']

# classes the parser adds to every file (namespaces, source file path): not model content
DEFAULT_EXCLUSIONS = ("NamespaceMap", "Distribution")


def add_exclusion_argument(parser, purpose):
    """``-ex`` / ``--exclude_objects``, the same in every CLI: replaces the defaults; ``-ex`` alone keeps everything."""
    defaults = " ".join(DEFAULT_EXCLUSIONS)
    parser.add_argument('-ex', '--exclude_objects', nargs='*', default=list(DEFAULT_EXCLUSIONS), metavar='TYPE',
                        help=f'Object types (rdf:type name without namespace) left out of {purpose}. '
                             f'Default: {defaults}. Names given replace the default; '
                             f'-ex with no names keeps everything.')
