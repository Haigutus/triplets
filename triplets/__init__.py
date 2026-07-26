# Import modules explicitly for package namespace
from . import export_schema
from . import rdf_parser
from . import cgmes_tools
from . import rdfs_tools
from . import cli
from . import tools
from . import export
from . import sparql
from . import validation
from . import _accessor  # registers df.triplets / df.sparql / df.shacl namespaces  # noqa: F401
from ._caches import clear_caches, cache_scope  # noqa: F401 — engine-state lifecycle

__all__ = [
    'cgmes_tools',
    'rdf_parser',
    'export_schema',
    'rdfs_tools',
    'cli',
    'sparql',
    'validation',
    'clear_caches',
    'cache_scope',
]

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions

# Expose the new parser API at top level
from .parser import parse, read_rdf as read_rdf_func, read_nquads  # noqa: F401

# Register read_rdf on pandas and polars (monkey-patch, standard approach)
# There is no official plugin API for top-level read functions in either library.
# This is the same pattern used by pandas-gbq (pd.read_gbq) and similar.
# polars uses functools.partial so return_type defaults to "polars" automatically.
from functools import partial
import pandas as pd
import logging
pd.read_RDF = partial(parse, return_type="pandas")
pd.read_rdf = partial(parse, return_type="pandas")
pd.read_nquads = partial(read_nquads, return_type="pandas")
logging.getLogger(__name__).debug("Registered pandas.read_rdf (and read_RDF)")

try:
    import polars as pl
    pl.read_rdf = partial(parse, return_type="polars")
    pl.read_RDF = partial(parse, return_type="polars")
    pl.read_nquads = partial(read_nquads, return_type="polars")
    logging.getLogger(__name__).debug("Registered polars.read_rdf (polars available)")
except ImportError:
    logging.getLogger(__name__).debug("polars not installed, skipping read_rdf registration")
    pass

# Register read_rdf + connect(table=, schema=) on DuckDB (if duckdb is installed)
try:
    import duckdb as _duckdb
    import logging as _logging
    from .tools.duckdb_table import (
        install_connect as _install_duckdb_connect,
        quote as _duckdb_quote,
        resolve as _duckdb_resolve,
        set as _duckdb_set_table,
        get as _duckdb_get_table,
        set_triplets_table as _set_triplets_table,
    )

    _duckdb_logger = _logging.getLogger(__name__)
    _install_duckdb_connect(_duckdb)
    _duckdb.DuckDBPyConnection.set_triplets_table = _set_triplets_table

    def _duckdb_read_rdf(self, paths, table=None, schema=None, table_name=None, **kwargs):
        """Parse RDF/XML into the connection's triplets table (Arrow zero-copy).

        Optional ``table`` / ``schema`` (or legacy ``table_name``) update this
        connection's defaults, then the load targets that quoted relation.
        """
        if table_name is not None and table is None:
            table = table_name
        if table is not None or schema is not None:
            cfg_schema, cfg_table = _duckdb_get_table(self)
            _duckdb_set_table(self,
                              table=table if table is not None else cfg_table,
                              schema=schema if schema is not None else cfg_schema)
        ref = _duckdb_resolve(self)
        sch, _ = _duckdb_get_table(self)
        if sch is not None:
            self.execute(f"CREATE SCHEMA IF NOT EXISTS {_duckdb_quote(sch)}")
        arrow_table = parse(paths, return_type="arrow", **kwargs)
        self.register("_arrow_import", arrow_table)
        self.execute(f"CREATE OR REPLACE TABLE {ref} AS SELECT * FROM _arrow_import")
        self.unregister("_arrow_import")
        row_count = self.execute(f"SELECT COUNT(*) FROM {ref}").fetchone()[0]
        _duckdb_logger.info("Loaded %s rows into %s", row_count, ref)
        return row_count

    _duckdb.DuckDBPyConnection.read_rdf = _duckdb_read_rdf
    _duckdb_logger.debug("Registered DuckDBPyConnection.read_rdf (via Arrow)")
except ImportError:
    logging.getLogger(__name__).debug("duckdb not installed, skipping read_rdf registration")
    pass

