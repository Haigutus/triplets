# Import modules explicitly for package namespace
from triplets import cgmes_tools
from triplets import rdf_parser
from triplets import export_schema
from triplets import rdfs_tools

__all__ = [
    'cgmes_tools',
    'rdf_parser',
    'export_schema',
    'rdfs_tools'
]

from triplets._version import get_versions
__version__ = get_versions()['version']
del get_versions
