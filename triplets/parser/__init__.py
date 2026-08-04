"""CIM/RDF XML parser package.

Provides pluggable engines for parsing CIM RDF/XML to DataFrames or Arrow tables:
- python_lxml_pandas  (pure Python + lxml → pd.DataFrame, always available, default)
- python_lxml_arrow   (pure Python + lxml → Arrow RecordBatch, needs pyarrow)
- cython_pugixml_arrow (Cython + pugixml C++ → Arrow RecordBatch, needs build + pyarrow)

Fallback: cython_pugixml_arrow → python_lxml_arrow → python_lxml_pandas

Usage:
    from triplets.parser import parse, read_rdf
    df = parse(["file.xml", "data.zip"], engine="python_lxml_pandas")
    df = parse(path, engine="auto")  # best available
    table = parse(path, return_type="arrow")
"""

from __future__ import annotations

import os
import inspect
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Union, List, Any, Optional, Sequence

from .._registry import EngineRegistry

logger = logging.getLogger(__name__)

# Auto preference: first importable.
_REGISTRY = EngineRegistry(
    "parser_cimxml", __package__,
    modules={
        "cython_pugixml_arrow": ".cython_pugixml_arrow",  # compiled extension, fastest
        "python_lxml_arrow": ".python_lxml_arrow",        # needs pyarrow
        "python_lxml_pandas": ".python_lxml_pandas",      # pure python, always available
    },
    aliases={
        "native": "python_lxml_pandas",
        "pugixml": "cython_pugixml_arrow",
        "performance": "cython_pugixml_arrow",
    },
    hints={"cython_pugixml_arrow": "Build with: pixi run build-cython-pugixml-arrow "
                                   "(or python setup_cython_parser.py build_ext --inplace)."},
    requires={"cython_pugixml_arrow": ("pyarrow",), "python_lxml_arrow": ("pyarrow",)},
)

# Engines whose load_rdf_to_dataframe returns Arrow RecordBatches
_ARROW_ENGINES = {"python_lxml_arrow", "cython_pugixml_arrow"}

# The RDF-compliant sibling parser: full-capture RDF/XML (context struct on
# every row). Same registry mechanics; the caller picks the dialect, the
# registry picks the engine.
_RDFXML_REGISTRY = EngineRegistry(
    "parser_rdfxml", __package__,
    modules={"python_lxml_arrow": ".rdfxml_lxml_arrow"},
    requires={"python_lxml_arrow": ("pyarrow",)},
    default_hint="Install with: pip install triplets[arrow].",
)


def register_engine(name: str, module_or_factory: Any) -> None:
    """Register a custom engine for future extensibility."""
    _REGISTRY.register(name, module_or_factory)


def get_engine(name: str = "auto"):
    """Resolve parser engine name (with aliases) and return (name, module)."""
    return _REGISTRY.get(name)


# Re-exports for compat layer (rdf_parser.py)
from .utils import find_all_xml, iter_all_xml, clean_ID  # noqa: F401

from .nquads import read_nquads  # noqa: F401


def parse(
    list_of_paths_to_zip_globalzip_xml: Union[str, List, Any],
    debug: bool = False,
    max_workers: Optional[int] = None,
    engine: str = "auto",
    return_type: str = "pandas",
    categorical_columns: Optional[Sequence[str]] = ("INSTANCE_ID", "KEY"),
    shorten_resources: bool = True,
    string_type: str = "auto",
    dialect: str = "cimxml",
    clean_rules: Optional[dict] = None,
) -> Any:
    """Main entry: parse CIM RDF/XML (or zips) using chosen engine.

    Parameters
    ----------
    debug : bool, default False
        Enable verbose debug output (file discovery, row counts, timing in some engines, etc.).
        When False but the logger is at DEBUG level (logging.basicConfig(level=logging.DEBUG) or
        getLogger("triplets.parser").setLevel(logging.DEBUG)), debug output is auto-enabled.
    engine : str, default "auto"
        Parser engine. "auto" picks best available.
        Options: "python_lxml_pandas", "python_lxml_arrow", "cython_pugixml_arrow".
    return_type : str, default "pandas"
        Output format: "pandas", "arrow", or "polars".
    categorical_columns : tuple or None, default ("INSTANCE_ID", "KEY")
        Columns to dictionary-encode for memory savings. Pass None to disable.
    shorten_resources : bool, default True
        Shorten http(s) resource values to their #fragment (CIM instance data convention).
        Pass False for lossless URIs (e.g. RDFS schema parsing); only the python engines
        support this.
    dialect : str, default "cimxml"
        Parser dialect: "cimxml" (the lossy-by-design CIM parser, all current
        engines) or "rdfxml" (full-capture RDF/XML — every row carries a
        `context` struct; see triplets/parser/rdfxml_lxml_arrow.py).
    clean_rules : dict, optional (dialect="rdfxml" only)
        Per-column ordered prefix tuples to strip, e.g. {"ID": ("#",)} —
        defaults to the CIM cleaning chain; whatever is stripped is recorded
        in the matching context *_PREFIX field, so cleaning is reversible.
    string_type : str, default "auto"
        Arrow layout of the ID and VALUE string columns (arrow/polars output,
        and pandas via ArrowDtype): "utf8" (32-bit offsets), "large_utf8"
        (64-bit), or "string_view" (polars' native layout, adopted zero-copy;
        needs pyarrow >= 16). "auto" picks the layout the return_type adopts
        zero-copy: string_view for polars, utf8 otherwise. Dictionary-encoded
        columns are unaffected (consumers use the indices). Ignored by the
        pandas engine (python_lxml_pandas).
    """
    debug = debug or logger.isEnabledFor(logging.DEBUG)
    if dialect == "rdfxml":
        if string_type != "auto":
            raise ValueError("string_type applies to dialect='cimxml'; the rdfxml "
                             "dialect emits utf8 base columns + a context struct")
        engine_name, engine_mod = _RDFXML_REGISTRY.get(engine)
        is_arrow_engine = True
        string_type = "utf8"
    elif dialect == "cimxml":
        if clean_rules is not None:
            raise ValueError("clean_rules applies to dialect='rdfxml'")
        engine_name, engine_mod = get_engine(engine)
        is_arrow_engine = engine_name in _ARROW_ENGINES
        string_type = _resolve_string_type(string_type, return_type)
    else:
        raise ValueError(f"Unknown dialect: {dialect!r}. Known: cimxml, rdfxml")

    if not shorten_resources and engine_name == "cython_pugixml_arrow":
        raise ValueError("shorten_resources=False is not supported by the cython_pugixml_arrow engine, "
                         "use engine='python_lxml_pandas' or 'python_lxml_arrow'")

    parse_one = getattr(engine_mod, "load_rdf_to_dataframe", None)
    if parse_one is None:
        raise RuntimeError(f"Engine {engine_name} missing load_rdf_to_dataframe entrypoint")
    # Engines that build the requested layout natively take string_type; the
    # rest are cast once at finalize (one pass over the combined table).
    native_string_type = "string_type" in inspect.signature(parse_one).parameters

    # Normalize input to list for find_all_xml
    if (isinstance(list_of_paths_to_zip_globalzip_xml, (str, bytes, os.PathLike))
            or hasattr(list_of_paths_to_zip_globalzip_xml, "read")):
        items = [list_of_paths_to_zip_globalzip_xml]
    else:
        items = list(list_of_paths_to_zip_globalzip_xml) if list_of_paths_to_zip_globalzip_xml else []

    xml_files = find_all_xml(items, debug=debug)

    if not xml_files:
        return _empty(return_type, categorical_columns, string_type, dialect)

    def _one(f: Any):
        one_kwargs = {"string_type": string_type} if native_string_type else {}
        if dialect == "rdfxml" and clean_rules is not None:
            one_kwargs["clean_rules"] = clean_rules
        if not shorten_resources:
            one_kwargs["shorten_resources"] = False
        return parse_one(f, debug=debug, **one_kwargs)

    if max_workers and len(xml_files) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(_one, xml_files))
    else:
        results = [_one(f) for f in xml_files]

    if not results:
        # Fallback empty after file list processing (should be rare; engines return DataFrames/Batches)
        return _empty(return_type, categorical_columns, string_type, dialect)

    if is_arrow_engine:
        return _finalize_arrow(results, return_type, categorical_columns, debug, string_type)
    else:
        return _finalize_pandas(results, return_type, categorical_columns, debug)


def parse_batches(
    list_of_paths_to_zip_globalzip_xml: Union[str, List, Any],
    debug: bool = False,
    engine: str = "auto",
    shorten_resources: bool = True,
    max_workers: Optional[int] = None,
    dialect: str = "cimxml",
    clean_rules: Optional[dict] = None,
) -> Any:
    """Parse CIM RDF/XML lazily into a ``pyarrow.RecordBatchReader``.

    One RecordBatch per XML file, produced as the reader is consumed — the
    dataset is never materialized in Python (out-of-core ingest, e.g. the
    duckdb ``read_rdf``). Fixed all-utf8 schema [ID, KEY, VALUE, INSTANCE_ID]:
    no dictionary encoding and no string_type layout, because per-file
    dictionaries would differ batch to batch and database consumers re-encode
    internally anyway.

    ``max_workers`` parses up to that many files ahead on a thread pool — a
    bounded, in-order prefetch: multi-file ingest parallelizes while memory
    stays bounded by max_workers+1 batches (None = fully sequential, one
    batch alive at a time). Batch order always follows file order.

    Requires an arrow parser engine ("auto" resolves one whenever pyarrow is
    installed); raises ValueError otherwise — no silent pandas fallback.
    """
    import pyarrow as pa

    debug = debug or logger.isEnabledFor(logging.DEBUG)
    if dialect == "rdfxml":
        engine_name, engine_mod = _RDFXML_REGISTRY.get(engine)
    elif dialect == "cimxml":
        if clean_rules is not None:
            raise ValueError("clean_rules applies to dialect='rdfxml'")
        engine_name, engine_mod = get_engine(engine)
        if engine_name not in _ARROW_ENGINES:
            raise ValueError(f"parse_batches requires an arrow parser engine, got {engine_name!r}. "
                             f"Install with: pip install triplets[arrow].")
    else:
        raise ValueError(f"Unknown dialect: {dialect!r}. Known: cimxml, rdfxml")
    if not shorten_resources and engine_name == "cython_pugixml_arrow":
        raise ValueError("shorten_resources=False is not supported by the cython_pugixml_arrow engine, "
                         "use engine='python_lxml_arrow'")
    parse_one = engine_mod.load_rdf_to_dataframe

    fields = [(c, pa.string()) for c in ("ID", "KEY", "VALUE", "INSTANCE_ID")]
    if dialect == "rdfxml":
        from .rdfxml_lxml_arrow import context_struct_type
        fields.append(("context", context_struct_type()))
    schema = pa.schema(fields)
    one_kwargs = {} if shorten_resources else {"shorten_resources": False}
    if dialect == "rdfxml" and clean_rules is not None:
        one_kwargs["clean_rules"] = clean_rules

    def one(xml_file):
        batch = parse_one(xml_file, debug=debug, **one_kwargs)
        return batch if batch.schema == schema else batch.cast(schema)

    def batches():
        xml_files = iter_all_xml(list_of_paths_to_zip_globalzip_xml, debug=debug)
        if not max_workers:
            for xml_file in xml_files:
                yield one(xml_file)
            return
        from collections import deque
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            window = deque()
            for xml_file in xml_files:
                window.append(pool.submit(one, xml_file))
                if len(window) > max_workers:
                    yield window.popleft().result()
            while window:
                yield window.popleft().result()

    return pa.RecordBatchReader.from_batches(schema, batches())


_STRING_TYPES = ("utf8", "large_utf8", "string_view")


def _string_view_available():
    import pyarrow as pa
    return hasattr(pa, "string_view")


def _resolve_string_type(string_type, return_type):
    """"auto" = the layout the return_type adopts zero-copy: string_view for
    polars (its native layout), utf8 otherwise (the stable default contract)."""
    if string_type == "auto":
        return "string_view" if return_type == "polars" and _string_view_available() else "utf8"
    if string_type not in _STRING_TYPES:
        raise ValueError(f"Unknown string_type: {string_type!r}. "
                         f"Known: auto, {', '.join(_STRING_TYPES)}")
    if string_type == "string_view" and not _string_view_available():
        raise ValueError('string_type="string_view" requires pyarrow >= 16')
    return string_type


def _string_target(string_type):
    import pyarrow as pa
    return {"utf8": pa.string(), "large_utf8": pa.large_string(),
            "string_view": pa.string_view()}[string_type]


def _empty(return_type, categorical_columns=(), string_type="utf8", dialect="cimxml"):
    """Empty result with the standard triplet columns, matching the non-empty schema.

    Arrow/polars empties carry the same string / dictionary-encoded columns as a
    non-empty parse, so they concatenate cleanly with real results.
    """
    if return_type == "pandas" and dialect == "cimxml":
        import pandas as pd
        return pd.DataFrame(columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    import pyarrow as pa
    cats = set(categorical_columns or ())
    target = _string_target(string_type)
    fields = [(c, pa.dictionary(pa.int32(), pa.string()) if c in cats else target)
              for c in ("ID", "KEY", "VALUE", "INSTANCE_ID")]
    if dialect == "rdfxml":
        from .rdfxml_lxml_arrow import context_struct_type
        fields.append(("context", context_struct_type()))
    schema = pa.schema(fields)
    table = schema.empty_table()
    if return_type == "pandas":
        import pandas as pd
        return table.to_pandas(types_mapper=pd.ArrowDtype)
    if return_type == "polars":
        import polars as pl
        return pl.from_arrow(table)
    return table


def _finalize_arrow(batches, return_type, categorical_columns, debug, string_type="utf8"):
    """Combine Arrow RecordBatches, dictionary-encode, and convert to return_type."""
    import pyarrow as pa

    table = pa.Table.from_batches(batches)

    # Dictionary-encode repetitive columns
    if categorical_columns:
        for col_name in categorical_columns:
            if col_name in table.column_names:
                try:
                    dict_arr = pa.compute.dictionary_encode(table[col_name])
                    table = table.set_column(table.schema.get_field_index(col_name), col_name, dict_arr)
                except Exception as e:
                    if debug:
                        logger.debug("Could not dictionary-encode %s: %s", col_name, e)

    # Cast plain string columns to the requested layout (no-op when the engine
    # already built it natively; one pass for engines that emit utf8 only)
    target = _string_target(string_type)
    string_family = {pa.string(), pa.large_string()} | ({pa.string_view()} if _string_view_available() else set())
    for col_name in table.column_names:
        field_type = table.schema.field(col_name).type
        if field_type in string_family and field_type != target:
            index = table.schema.get_field_index(col_name)
            table = table.set_column(index, col_name, table[col_name].cast(target))

    if return_type == "pandas":
        import pandas as pd
        try:
            return table.to_pandas(types_mapper=pd.ArrowDtype)  # zero-copy, Arrow-backed dtypes
        except Exception:
            return table.to_pandas()
    if return_type == "arrow":
        return table
    if return_type == "polars":
        import polars as pl
        return pl.from_arrow(table)
    raise ValueError(f"Unknown return_type: {return_type}")


def _finalize_pandas(dataframes, return_type, categorical_columns, debug):
    """Combine pandas DataFrames, optionally apply Categorical, and convert to return_type."""
    import pandas as pd

    if len(dataframes) == 1:
        df = dataframes[0]
    else:
        df = pd.concat(dataframes, ignore_index=True)

    # Apply Categorical for memory savings (pandas equivalent of Arrow dictionary encoding)
    if categorical_columns:
        for col_name in categorical_columns:
            if col_name in df.columns:
                try:
                    df[col_name] = df[col_name].astype("category")
                except Exception as e:
                    if debug:
                        logger.debug("Could not categorize %s: %s", col_name, e)

    if return_type == "pandas":
        return df
    if return_type == "arrow":
        import pyarrow as pa
        return pa.Table.from_pandas(df, preserve_index=False)
    if return_type == "polars":
        import polars as pl
        return pl.from_pandas(df)
    raise ValueError(f"Unknown return_type: {return_type}")


def read_rdf(*args: Any, **kwargs: Any) -> Any:
    """Alias for parse (for pandas.read_rdf registration)."""
    return parse(*args, **kwargs)
