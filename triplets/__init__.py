# Import modules explicitly for package namespace
from . import export_schema
from . import rdf_parser
from . import cgmes_tools
from . import rdfs_tools
from . import shacl
from . import validation

__all__ = [
    'cgmes_tools',
    'rdf_parser',
    'export_schema',
    'rdfs_tools',
    'shacl',
    'validation'
]

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
