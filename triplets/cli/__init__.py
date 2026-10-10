"""
CLI tools for the triplets package.
"""

__all__ = ['cim_spreadsheet', 'cim_diff', 'METADATA_TYPES', 'add_exclusion_arguments', 'excluded_types']

# classes the parser adds to every file (namespaces, source file path): not model content
METADATA_TYPES = ("NamespaceMap", "Distribution")


def add_exclusion_arguments(parser, purpose):
    """``-ex TYPE`` and ``--keep-metadata``, the same in every CLI; combine with :func:`excluded_types`."""
    parser.add_argument('-ex', '--exclude_objects', action='extend', type=lambda names: names.split(','),
                        default=[], metavar='TYPE',
                        help=f'Object type (rdf:type name without namespace) left out of {purpose}. '
                             f'Repeat or comma-separate for more: -ex Terminal -ex Breaker,Switch')
    parser.add_argument('--keep-metadata', action='store_true',
                        help=f'Keep the parser metadata ({", ".join(METADATA_TYPES)}), left out by default')


def excluded_types(args):
    """Types to leave out: the ``-ex`` names, plus the metadata unless ``--keep-metadata``."""
    return args.exclude_objects + ([] if args.keep_metadata else list(METADATA_TYPES))
