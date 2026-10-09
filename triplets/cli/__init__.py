"""
CLI tools for the triplets package.
"""

__all__ = ['cim_spreadsheet', 'cim_diff', 'DEFAULT_EXCLUSIONS', 'add_exclusion_arguments', 'exclusions']

# classes the parser adds to every file (namespaces, source file path): not model content
DEFAULT_EXCLUSIONS = ("NamespaceMap", "Distribution")


def add_exclusion_arguments(parser, purpose):
    """``--exclude_objects`` / ``--no-default-exclusions``, the same in every CLI."""
    defaults = ", ".join(DEFAULT_EXCLUSIONS)
    parser.add_argument('-ex', '--exclude_objects', nargs='+',
                        help=f'Names of rdf:Description rdf:type-s without namespace or prefix to be excluded '
                             f'from {purpose} (default: {defaults})')
    parser.add_argument('--no-default-exclusions', action='store_true',
                        help=f'Disable default exclusions ({defaults})')


def exclusions(args):
    """The class names to leave out: the defaults unless disabled, plus ``--exclude_objects``."""
    return [*(() if args.no_default_exclusions else DEFAULT_EXCLUSIONS), *(args.exclude_objects or ())]
