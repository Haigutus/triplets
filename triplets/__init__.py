# Import modules explicitly for package namespace
from . import export_schema
from . import rdf_parser
from . import cgmes_tools
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

# Expose the new parser API at top level
from .parser import parse, read_rdf as read_rdf_func  # noqa: F401

# Register pd.read_RDF / pd.read_rdf → parser.parse directly
import pandas as pd
pd.read_RDF = parse
pd.read_rdf = parse

