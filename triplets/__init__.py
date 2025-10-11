# Define public API
__all__ = ['cgmes_tools', 'rdf_parser']

# Import modules explicitly for package namespace
from . import cgmes_tools
from . import rdf_parser

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
