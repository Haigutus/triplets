"""Method registration for pandas / polars DataFrames and DuckDB connections.

Binds each engine's own functions directly onto its class — both on the root
(monkey-patch, backwards compat) and under the `triplets` namespace accessor.
No per-call dispatch: the object a method is called on determines the engine.

Usage:
    import triplets

    data = pandas.read_RDF(["grid.xml"])
    data.triplets.type_tableview("ACLineSegment")
    data.triplets.types_dict()
    data.triplets.export_to_excel(export_to_memory=True)

    data = polars.read_rdf(["grid.xml"])
    data.triplets.type_tableview("ACLineSegment")
"""

import logging
import functools

import pandas

from . import tools, export
from .tools import _engine_functions, pandas_engine

logger = logging.getLogger(__name__)

try:
    import polars
except ImportError:
    polars = None

try:
    import duckdb
except ImportError:
    duckdb = None


# Export methods live in export/ — not the engine modules — so they stay listed
# explicitly. The exporters accept any DataFrame flavor (converting internally).
EXPORT_METHODS = [
    "export_to_excel", "export_to_csv", "export_to_cimxml",
    "export_to_nquads", "export_to_networkx", "export_to_arrow",
]

DUCKDB_EXPORT_METHODS = ["export_to_excel", "export_to_csv", "export_to_nquads", "export_to_cimxml"]

_EXPORTS = {name: getattr(export, name) for name in EXPORT_METHODS}


def _is_native(target_class, name):
    """True if *name* is already a non-triplets (native) attribute of target_class."""
    existing = getattr(target_class, name, None)
    return existing is not None and not getattr(existing, "__module__", "").startswith("triplets")


def _accessor_method(function):
    @functools.wraps(function)
    def method(self, *args, **kwargs):
        return function(self._obj, *args, **kwargs)
    return method


def _namespace(methods, attach_namespace, doc=None):
    """Build an accessor class delegating each method to function(self._obj, ...) and attach it."""
    class Accessor:
        def __init__(self, obj):
            self._obj = obj

    Accessor.__doc__ = doc
    for name, function in methods.items():
        setattr(Accessor, name, _accessor_method(function))
    attach_namespace(Accessor)


def _register(target_class, methods, attach_namespace):
    """Bind each callable onto the class root and attach the set as a `triplets` namespace.

    Root: plain functions bind self as their data/connection argument. Names that are
    native attributes are skipped so we never clobber them, but the current
    implementation still supersedes triplets' own legacy patches (e.g. the deprecated
    rdf_parser monkey-patches applied earlier at import time).
    """
    for name, function in methods.items():
        if _is_native(target_class, name):
            logger.debug("skip %s.%s — native attribute present", target_class.__name__, name)
            continue
        setattr(target_class, name, function)

    _namespace(methods, attach_namespace, doc="Triplet operations via the `triplets` namespace.")
    logger.debug("Registered %d triplets methods on %s (root + namespace)",
                 len(methods), target_class.__name__)


def _methods(engine_module):
    """Engine functions + first-class aliases + deprecated warning wrappers, all direct."""
    implemented = _engine_functions(engine_module)
    methods = dict(implemented)
    methods |= {alias: implemented[target]
                for alias, target in tools.ALIASES.items() if target in implemented}
    methods |= {old: tools._deprecated_alias(old, new, implemented[new])
                for old, new in tools.DEPRECATED_ALIASES.items() if new in implemented}
    return methods


# ── Engine table ─────────────────────────────────────────────────────────────
_register(pandas.DataFrame, _methods(pandas_engine) | _EXPORTS,
          pandas.api.extensions.register_dataframe_accessor("triplets"))

if polars:
    from .tools import polars_engine

    _register(polars.DataFrame, _methods(polars_engine) | _EXPORTS,
              polars.api.register_dataframe_namespace("triplets"))
else:
    logger.debug("polars not installed, skipping triplets namespace accessor")

if duckdb:
    from .tools import duckdb_engine

    from ._engine_detect import to_arrow as _to_arrow, to_pandas as _to_pandas

    def _duckdb_export_fn(name):
        """A connection-first export callable: fetch the triplets table, run the export.

        Fetches the configured triplets table through duckdb's native arrow
        result path (~4x cheaper than materialising pandas; the exporters
        adopt arrow near zero-copy). The whole table is still in memory —
        larger-than-RAM export needs the chunked design in TODO.md.
        """
        function = getattr(export, name)

        def fn(connection, *args, table=None, schema=None, table_name=None, **kwargs):
            data = _to_arrow(connection, table=table, schema=schema, table_name=table_name)
            return function(data, *args, **kwargs)

        fn.__name__ = name
        fn.__doc__ = function.__doc__
        return fn

    def _duckdb_export_to_arrow(connection, table=None, schema=None, table_name=None):
        """Triplet columns as a pyarrow.Table, straight from duckdb's native
        arrow result path (no pandas materialization)."""
        return _to_arrow(connection, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"],
                         table=table, schema=schema, table_name=table_name)

    # duckdb has no register_*_namespace API — attach the accessor via a property
    # (accepted on the C-extension type). None of the method names is "triplets".
    _register(duckdb.DuckDBPyConnection,
              _methods(duckdb_engine)
              | {name: _duckdb_export_fn(name) for name in DUCKDB_EXPORT_METHODS}
              | {"export_to_arrow": _duckdb_export_to_arrow},
              lambda accessor: setattr(duckdb.DuckDBPyConnection, "triplets", property(accessor)))
else:
    logger.debug("duckdb not installed, skipping DuckDB tools/export patches")


# ── SPARQL / SHACL namespaces ────────────────────────────────────────────────
# Namespace-only (no root monkey-patches): "query" and "validate" are too
# generic to claim on DataFrame/connection classes. The dispatchers auto-detect
# the input flavor, so the same function backs all three namespaces.
from . import sparql, validation

_QUERY_NAMESPACES = {
    "sparql": ({"query": sparql.query}, "SPARQL queries via the `sparql` namespace."),
    # enrich/locate/to_sarif/to_shacl_report take a *violations* frame (plain pandas) —
    # the namespace is registered on every DataFrame, so violations.shacl.to_sarif() works
    "shacl": ({"validate": validation.validate,
               "enrich": validation.enrich,
               "locate": validation.locate_violations,
               "to_sarif": validation.export_to_sarif,
               "to_shacl_report": validation.export_to_shacl_report},
              "SHACL validation via the `shacl` namespace."),
}

for _name, (_ns_methods, _doc) in _QUERY_NAMESPACES.items():
    _namespace(_ns_methods, pandas.api.extensions.register_dataframe_accessor(_name), doc=_doc)
    if polars:
        _namespace(_ns_methods, polars.api.register_dataframe_namespace(_name), doc=_doc)
    if duckdb:
        _namespace(_ns_methods,
                   lambda accessor, name=_name: setattr(duckdb.DuckDBPyConnection, name, property(accessor)),
                   doc=_doc)
logger.debug("Registered sparql + shacl namespaces")
