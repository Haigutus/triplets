# Import modules explicitly for package namespace
from . import export_schema
from . import rdf_parser
from . import cgmes_tools
from . import rdfs_tools
from . import cli
from . import tools
from . import export

__all__ = [
    'cgmes_tools',
    'rdf_parser',
    'export_schema',
    'rdfs_tools',
    'cli',
]

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions

# Expose the new parser API at top level
from .parser import parse, read_rdf as read_rdf_func  # noqa: F401

# Register read_rdf on pandas and polars (monkey-patch, standard approach)
# There is no official plugin API for top-level read functions in either library.
# This is the same pattern used by pandas-gbq (pd.read_gbq) and similar.
# polars uses functools.partial so return_type defaults to "polars" automatically.
from functools import partial
import pandas as pd
pd.read_RDF = partial(parse, return_type="pandas")
pd.read_rdf = partial(parse, return_type="pandas")

try:
    import polars as pl
    pl.read_rdf = partial(parse, return_type="polars")
    pl.read_RDF = partial(parse, return_type="polars")
except ImportError:
    pass

