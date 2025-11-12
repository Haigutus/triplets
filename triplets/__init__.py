# Import modules explicitly for package namespace
from . import cgmes_tools         # exposes cgmes_tools.*
from . import rdf_parser          # exposes rdf_parser.*
from . import export_schema       # exposes export_schema.*
from . import rdfs_tools

__all__ = [
    'cgmes_tools',
    'rdf_parser',
    'export_schema',
    'rdfs_tools'
]

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
