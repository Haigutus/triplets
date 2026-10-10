"""
CLI tools for the triplets package.
"""

__all__ = ['cim_spreadsheet', 'cim_diff', 'METADATA_TYPES', 'add_common_arguments', 'excluded_types']

# classes the parser adds to every file (namespaces, source file path): not model content
METADATA_TYPES = ("NamespaceMap", "Distribution")


def names(value):
    """Comma-separated option value -> list, so a flag takes one argument and never swallows positionals."""
    return [name for name in value.split(',') if name]


def add_common_arguments(parser, purpose):
    """Options shared by every CLI: ``--version``, ``--include`` / ``-ex`` / ``--keep-metadata``."""
    from .. import __version__
    parser.add_argument('--version', action='version', version=f'%(prog)s (triplets {__version__})')
    parser.add_argument('--include', action='extend', type=names, default=[], metavar='TYPE',
                        help=f'Only these object types (rdf:type name without namespace) in {purpose}. '
                             f'Repeat or comma-separate for more')
    parser.add_argument('-ex', '--exclude_objects', action='extend', type=names, default=[], metavar='TYPE',
                        help=f'Object type left out of {purpose}, after --include. '
                             f'Repeat or comma-separate for more: -ex Terminal -ex Breaker,Switch')
    parser.add_argument('--keep-metadata', action='store_true',
                        help=f'Keep the parser metadata ({", ".join(METADATA_TYPES)}), left out by default')


def excluded_types(args):
    """Types to leave out: the ``-ex`` names, plus the metadata unless ``--keep-metadata``."""
    return args.exclude_objects + ([] if args.keep_metadata else list(METADATA_TYPES))
