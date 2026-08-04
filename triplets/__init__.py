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
from ._registry import engines, set_engine  # noqa: F401 — engine selection report/override

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
    'engines',
    'set_engine',
]

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions

# Expose the new parser API at top level
from .parser import parse, parse_batches, read_rdf as read_rdf_func, read_nquads  # noqa: F401

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
    from .tools import duckdb_engine as _duckdb_engine

    _duckdb_logger = _logging.getLogger(__name__)
    _duckdb_engine._install_connect(_duckdb)
    _duckdb.DuckDBPyConnection.set_triplets_table = _duckdb_engine._set_triplets_table

    def _duckdb_read_rdf(self, paths, table=None, schema=None, table_name=None,
                         append=False, **kwargs):
        """Parse RDF/XML into the connection's triplets table, streaming.

        One Arrow RecordBatch per XML file flows straight into DuckDB — the
        dataset is never materialized in Python (out-of-core ingest). With
        ``append=True`` rows are added to the existing table (created if
        missing); the default replaces it. Optional ``table`` / ``schema``
        (or legacy ``table_name``) update this connection's defaults first.

        The replace load is one transactional statement, so a mid-stream parse
        failure leaves the previous table intact (a failed ``append=True`` adds
        no rows, but may leave a newly created empty table behind).
        ``max_workers`` parses up to that many files ahead (bounded, in-order
        prefetch — see :func:`triplets.parser.parse_batches`). An arrow parser
        engine is required; ``string_type`` / ``categorical_columns`` do not
        apply on this path and raise. Returns the rows loaded by this call.
        """
        if table_name is not None and table is None:
            table = table_name
        if table is not None or schema is not None:
            cfg_schema, cfg_table = _duckdb_engine._get_table(self)
            _duckdb_engine._set_table(
                self,
                table=table if table is not None else cfg_table,
                schema=schema if schema is not None else cfg_schema,
                persist=True,
            )
        ref = _duckdb_engine._resolve_table(self)
        sch, _ = _duckdb_engine._get_table(self)
        if sch is not None:
            self.execute(f"CREATE SCHEMA IF NOT EXISTS {_duckdb_engine._quote(sch)}")
        reader = parse_batches(paths, **kwargs)
        self.register("_arrow_import", reader)
        cfg_schema, cfg_table = _duckdb_engine._table_parts(self)
        exists = self.execute(
            "SELECT 1 FROM duckdb_tables() "
            "WHERE table_name = ? AND schema_name = COALESCE(?, current_schema())",
            [cfg_table, cfg_schema]).fetchone()
        if append and exists:
            row_count = self.execute(f"INSERT INTO {ref} BY NAME SELECT * FROM _arrow_import").fetchone()[0]
        else:
            # append into a missing table degrades to create — schema-generic
            # (works for any reader schema, incl. the rdfxml context struct)
            self.execute(f"CREATE OR REPLACE TABLE {ref} AS SELECT * FROM _arrow_import")
            row_count = self.execute(f"SELECT COUNT(*) FROM {ref}").fetchone()[0]
        self.unregister("_arrow_import")
        _duckdb_logger.info("Loaded %s rows into %s", row_count, ref)
        return row_count

    _duckdb.DuckDBPyConnection.read_rdf = _duckdb_read_rdf
    _duckdb_logger.debug("Registered DuckDBPyConnection.read_rdf (via Arrow)")
except ImportError:
    logging.getLogger(__name__).debug("duckdb not installed, skipping read_rdf registration")
    pass

