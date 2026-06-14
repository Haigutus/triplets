# Import modules explicitly for package namespace
from . import export_schema
from . import rdf_parser
from . import cgmes_tools
from . import rdfs_tools
from . import cli
from . import tools
from . import export
from . import sparql  # noqa: F401  (df.sparql.* namespace; rdflib lazy-loaded per call)
from . import validation  # noqa: F401  (df.shacl.* namespace; pyshacl lazy-loaded per call)
from . import _accessor  # registers df.triplets.* / df.sparql.* / df.shacl.* namespaces  # noqa: F401

__all__ = [
    'cgmes_tools',
    'rdf_parser',
    'export_schema',
    'rdfs_tools',
    'cli',
    'sparql',
    'validation',
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
import logging
pd.read_RDF = partial(parse, return_type="pandas")
pd.read_rdf = partial(parse, return_type="pandas")
logging.getLogger(__name__).debug("Registered pandas.read_rdf (and read_RDF)")

try:
    import polars as pl
    pl.read_rdf = partial(parse, return_type="polars")
    pl.read_RDF = partial(parse, return_type="polars")
    logging.getLogger(__name__).debug("Registered polars.read_rdf (polars available)")
except ImportError:
    logging.getLogger(__name__).debug("polars not installed, skipping read_rdf registration")
    pass

# Register read_rdf on DuckDB connections (if duckdb is installed)
try:
    import duckdb as _duckdb
    import logging as _logging

    _duckdb_logger = _logging.getLogger(__name__)

    def _duckdb_read_rdf(self, paths, table_name="triplets", **kwargs):
        """Parse RDF/XML files and load into DuckDB table via Arrow (zero-copy)."""
        arrow_table = parse(paths, return_type="arrow", **kwargs)
        self.register("_arrow_import", arrow_table)
        self.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM _arrow_import")
        self.unregister("_arrow_import")
        row_count = self.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        _duckdb_logger.info(f"Loaded {row_count} rows into {table_name}")
        return row_count

    _duckdb.DuckDBPyConnection.read_rdf = _duckdb_read_rdf
    _duckdb_logger.debug("Registered DuckDBPyConnection.read_rdf (via Arrow)")
except ImportError:
    logging.getLogger(__name__).debug("duckdb not installed, skipping read_rdf registration")
    pass

