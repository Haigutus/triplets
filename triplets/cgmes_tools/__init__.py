"""CGMES tools — metadata, visualization, and data quality utilities.

Functions accept triplet data in any supported flavor: pandas, polars, pyarrow
Table/RecordBatch, or a DuckDB connection holding a triplets table (the connection's configured one). A dispatcher
routes each call by input type:

- polars input → native polars engine (polars_engine.py), no pandas round-trip;
- pandas input → pandas engine (pandas_engine.py);
- pyarrow / DuckDB input → converted to pandas at the boundary (Arrow-backed,
  ~10 ms per million rows), run on the pandas engine, result converted back.

Functions without a polars implementation always take the pandas-boundary path;
the draw_references_* visualizers have native polars engines that query references
in polars and render via the shared pandas graph helper.
"""
import inspect
import functools
import logging

import pandas

from . import pandas_engine
from .._engine_detect import flavor, match_flavor, to_pandas
from .._header import PROFILE_KEYS, REFERENCE_KEYS, HEADER_TYPES  # noqa: F401 — header vocabulary, re-exported
from ..tools import _engine_functions, _deprecated_alias
from .pandas_engine import (  # noqa: F401 — no triplet-data argument, re-exported as-is
    dependencies,
    default_filename_mask,
    generate_instance_ids,
    get_metadata_from_filename,
    get_filename_from_metadata,
    get_metadata_from_xml,
)

try:
    from . import polars_engine
except ImportError:                                   # polars not installed
    polars_engine = None

logger = logging.getLogger(__name__)

# Functions taking a triplet dataset as first argument — wrapped below so any
# supported data flavor can be passed directly. Derived from the pandas engine
# (the reference surface): a data function is one whose first parameter is
# named ``data``; the filename/metadata helpers above take other inputs and
# are re-exported as-is.
DATA_FUNCTIONS = sorted(
    name for name, fn in _engine_functions(pandas_engine).items()
    if next(iter(inspect.signature(fn).parameters)) == "data")

# Old name → new name; old names keep working but emit DeprecationWarning
DEPRECATED_ALIASES = {
    "statistics_GeneratingUnit_types": "count_GeneratingUnit_types",
    "generate_instances_ID": "generate_instance_ids",
    "get_model_data": "get_model_triplets",
    "draw_relations_to": "draw_references_to",
    "draw_relations_from": "draw_references_from",
    "draw_relations": "draw_references",
}


def _to_pandas(data):
    """Any flavor → plain pandas (safe for in-place VALUE mutation in the engine)."""
    return to_pandas(data, plain=True)


def _match_input_flavor(result, data):
    """Convert a pandas DataFrame result back to the flavor of the input data."""
    return match_flavor(result, data)


def _resolve_engine(engine, data):
    if engine != "auto":
        return engine
    kind = flavor(data)
    return kind if kind in ("polars", "pandas") else "other"   # pyarrow / duckdb → pandas boundary


def _data_dispatch(name):
    """Route a cgmes data function by input flavor.

    With ``engine="auto"``: native polars for polars input (pandas boundary when no
    polars implementation exists), native pandas for pandas input, pandas boundary
    for anything else (pyarrow / DuckDB). An explicit ``engine="polars"`` requires
    polars input and a polars implementation; an explicit ``engine="pandas"`` forces
    the pandas implementation, through the boundary for non-pandas input.
    """
    pandas_fn = getattr(pandas_engine, name)

    @functools.wraps(pandas_fn)
    def wrapper(data, *args, engine="auto", **kwargs):
        if engine not in ("auto", "pandas", "polars"):
            raise ValueError(f"cgmes_tools supports engine='pandas'/'polars' (got {engine!r}); "
                             f"duckdb/arrow input runs via the pandas boundary")
        eng = _resolve_engine(engine, data)
        native_polars = polars_engine is not None and hasattr(polars_engine, name)
        if eng == "polars" and engine != "auto":
            if flavor(data) != "polars":
                raise TypeError("engine='polars' but the input is not a polars DataFrame")
            if not native_polars:
                raise NotImplementedError(f"cgmes_tools.{name} has no polars engine")
        if eng == "polars" and native_polars:
            return getattr(polars_engine, name)(data, *args, **kwargs)
        if eng == "pandas" and isinstance(data, pandas.DataFrame):
            return pandas_fn(data, *args, **kwargs)
        return _match_input_flavor(pandas_fn(_to_pandas(data), *args, **kwargs), data)

    wrapper.__module__ = __name__  # else pydoc/Sphinx hide it as an imported name
    signature = inspect.signature(pandas_fn)
    wrapper.__signature__ = signature.replace(parameters=[
        *signature.parameters.values(),
        inspect.Parameter("engine", inspect.Parameter.KEYWORD_ONLY, default="auto")])
    return wrapper


# ── schema validation by instance header (the CIM orchestration) ────────────

def declared_profiles(data, rdf_map, data_scope=None, **kwargs):
    """``{INSTANCE_ID: [schema section, ...]}`` — the profiles each instance header declares.

    The header's profile-identity fields (``Model.messageType``, ``keyword``,
    ``Model.profile``, ``conformsTo``) are matched against the schema profiles'
    own declared identity (section key / keyword / versionIRI / profileVersionIRI /
    conformsTo; an identifier several profiles share identifies none), legacy
    CGMES 2.4.15 profile URLs by substring. An instance without a match maps to
    an empty list. DuckDB: ``table`` / ``schema`` / ``table_name`` kwargs.

    Examples
    --------
    >>> declared_profiles(data, schemas.ENTSOE_CGMES_3_0_0_552_ED2)
    {'4f8d…': ['EQ'], '9a1c…': ['SSH']}
    """
    from .._header import profile_sections
    from ..iri import load_rdf_map

    rdf_map = load_rdf_map(rdf_map)
    hints = _instance_hints(data, _table(data, **kwargs))
    instances = hints if data_scope is None else {instance: hints.get(str(instance), []) for instance in data_scope}
    return {instance: profile_sections(found, rdf_map) for instance, found in instances.items()}


def validate_by_header(data, rdf_map, data_scope=None, rdf_map_scope=None, report_undefined_keys=False,
                       references=False, report_missing_targets=True, engine="auto", **kwargs):
    """Schema-validate every instance against the profiles its header declares.

    Instances declaring the same profiles run together through
    ``triplets.validation.validate_schema`` (each instance still counted on its
    own, its declared profiles merged). With *references*,
    ``validate_schema_references`` checks each instance's references against
    every loaded instance and its declared profiles. An instance whose header
    matches no profile is skipped and named in ``skipped_shapes``.

    data_scope : iterable of INSTANCE_ID, optional
        The instances to validate; None = every instance. References are still
        looked up in every loaded instance.
    rdf_map_scope : profile identifier(s), optional
        Override the declared profiles for every instance (header-less data).
    """
    from .. import validation
    from ..iri import load_rdf_map

    rdf_map = load_rdf_map(rdf_map)
    loaded = declared_profiles(data, rdf_map, **kwargs)
    if rdf_map_scope is not None:
        override = [rdf_map_scope] if isinstance(rdf_map_scope, str) else list(rdf_map_scope)
        loaded = {instance: override for instance in loaded}
    declared = loaded if data_scope is None else {str(instance): loaded.get(str(instance), [])
                                                   for instance in data_scope}
    groups, unresolved = {}, []
    for instance, sections in declared.items():
        if sections:
            groups.setdefault(tuple(sections), []).append(instance)
        else:
            unresolved.append(f"instance {instance}: no schema profile matched its header")
    target_profiles = list(dict.fromkeys(section for sections in loaded.values() for section in sections))
    frames = []
    for sections, instances in groups.items():
        frames.append(validation.validate_schema(data, rdf_map, data_scope=instances, rdf_map_scope=list(sections),
                                                 report_undefined_keys=report_undefined_keys, engine=engine, **kwargs))
        if references:
            frames.append(validation.validate_schema_references(
                data, rdf_map, instances, list(sections), list(loaded), target_profiles,
                report_missing_targets=report_missing_targets, engine=engine, **kwargs))
    violations = validation.concat_violations(frames)
    if "PROFILE" not in violations.columns:            # no instance resolved: no run stamped it
        violations["PROFILE"] = pandas.Series(dtype=object)
    meta = violations.attrs.setdefault("validation", {"language": "rdfs", "skipped_shapes": []})
    meta["skipped_shapes"] = [*meta.get("skipped_shapes", []), *unresolved]
    return violations


def _table(data, table=None, schema=None, table_name=None, **kwargs):
    """The triplets relation of a DuckDB connection; unused for other flavors."""
    if flavor(data) == "duckdb":
        from ..tools.duckdb_engine import _resolve_table
        return _resolve_table(data, table=table, schema=schema, table_name=table_name)
    return table_name or "triplets"


def _instance_hints(data, table_name="triplets"):
    """{INSTANCE_ID: [header profile hints, priority-ordered]} — every
    instance appears, hint-less ones with an empty list (any input flavor)."""
    kind = flavor(data)
    if kind == "duckdb":
        placeholders = ", ".join("?" for _ in PROFILE_KEYS)
        header = pandas.DataFrame(data.execute(
            f"SELECT INSTANCE_ID, KEY, VALUE FROM {table_name} WHERE KEY IN ({placeholders})",
            list(PROFILE_KEYS)).fetchall(), columns=["INSTANCE_ID", "KEY", "VALUE"])
        instances = [row[0] for row in data.execute(
            f"SELECT DISTINCT INSTANCE_ID FROM {table_name}").fetchall()]
    else:
        if kind == "pyarrow":
            data = data.to_pandas(types_mapper=pandas.ArrowDtype)
            kind = "pandas"
        if kind == "polars":
            header = data.filter(data["KEY"].is_in(list(PROFILE_KEYS))).to_pandas()
            instances = data["INSTANCE_ID"].unique().to_list()
        else:
            header = data.loc[data["KEY"].isin(PROFILE_KEYS)]
            instances = data["INSTANCE_ID"].unique().tolist()

    hints = {str(instance): [] for instance in instances}
    priority = {key: rank for rank, key in enumerate(PROFILE_KEYS)}
    header = header.sort_values("KEY", key=lambda keys: keys.map(priority), kind="stable")
    for instance, value in zip(header["INSTANCE_ID"].astype(str), header["VALUE"]):
        if value is not None and not pandas.isna(value):
            hints[instance].append(str(value))
    return hints


for _name in DATA_FUNCTIONS:
    globals()[_name] = _data_dispatch(_name)

for _old, _new in DEPRECATED_ALIASES.items():
    globals()[_old] = _deprecated_alias(_old, _new, globals()[_new])

__all__ = [
    "dependencies", "default_filename_mask", "generate_instance_ids",
    "get_metadata_from_filename", "get_filename_from_metadata",
    "get_metadata_from_xml",
    "PROFILE_KEYS", "REFERENCE_KEYS", "HEADER_TYPES",
    "declared_profiles", "validate_by_header",
] + DATA_FUNCTIONS + list(DEPRECATED_ALIASES)
