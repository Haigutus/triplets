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

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Union, List, Any, Optional, Sequence

logger = logging.getLogger(__name__)

# Engine registry (lazily populated)
_ENGINES: dict[str, Any] = {}
_ENGINE_ALIASES = {
    "native": "python_lxml_pandas",
    "pugixml": "cython_pugixml_arrow",
    "performance": "cython_pugixml_arrow",
}

# Engine name → (returns_arrow: bool)
_ARROW_ENGINES = {"python_lxml_arrow", "cython_pugixml_arrow"}


def register_engine(name: str, module_or_factory: Any) -> None:
    """Register a custom engine for future extensibility."""
    _ENGINES[name] = module_or_factory


def _load_engine(name: str):
    """Import engine module on demand."""
    if name in _ENGINES:
        return _ENGINES[name]
    if name == "python_lxml_pandas":
        from . import python_lxml_pandas as m
        _ENGINES[name] = m
        return m
    if name == "python_lxml_arrow":
        from . import python_lxml_arrow as m
        _ENGINES[name] = m
        return m
    if name == "cython_pugixml_arrow":
        try:
            from . import cython_pugixml_arrow as m  # type: ignore[attr-defined]
            _ENGINES[name] = m
            return m
        except ImportError as e:
            raise ImportError(
                "cython_pugixml_arrow engine not available (compiled extension missing). "
                "Build with: pixi run build-cython-pugixml-arrow "
                "(or python setup_cython_parser.py build_ext --inplace). "
                f"Original error: {e}"
            ) from e
    raise ValueError(f"Unknown engine: {name}. Known: python_lxml_pandas, python_lxml_arrow, cython_pugixml_arrow")


def get_engine(name: str = "auto"):
    """Resolve engine name (with aliases) and return its module."""
    if name == "auto":
        for candidate in ("cython_pugixml_arrow", "python_lxml_arrow", "python_lxml_pandas"):
            try:
                logger.debug(f"auto - test engine availability: {candidate}")
                return candidate, _load_engine(candidate)
            except ImportError:
                continue
            except Exception:
                # real error in engine (e.g. during load), surface it rather than silent fallback
                raise

    resolved = _ENGINE_ALIASES.get(name, name)

    logger.debug(f"engine set: {resolved}")
    return resolved, _load_engine(resolved)


# Re-exports for compat layer (rdf_parser.py)
from .utils import find_all_xml, clean_ID  # noqa: F401


def parse(
    list_of_paths_to_zip_globalzip_xml: Union[str, List, Any],
    debug: bool = False,
    max_workers: Optional[int] = None,
    engine: str = "auto",
    return_type: str = "pandas",
    categorical_columns: Optional[Sequence[str]] = ("INSTANCE_ID", "KEY"),
    **kwargs: Any,
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
    """
    debug = debug or logger.isEnabledFor(logging.DEBUG)
    engine_name, engine_mod = get_engine(engine)
    is_arrow_engine = engine_name in _ARROW_ENGINES

    parse_one = getattr(engine_mod, "load_rdf_to_dataframe", None)
    if parse_one is None:
        raise RuntimeError(f"Engine {engine_name} missing load_rdf_to_dataframe entrypoint")

    # Normalize input to list for find_all_xml
    if isinstance(list_of_paths_to_zip_globalzip_xml, (str, bytes)) or hasattr(list_of_paths_to_zip_globalzip_xml, "read"):
        items = [list_of_paths_to_zip_globalzip_xml]
    else:
        items = list(list_of_paths_to_zip_globalzip_xml) if list_of_paths_to_zip_globalzip_xml else []

    xml_files = find_all_xml(items, debug=debug)

    if not xml_files:
        import pandas as pd
        empty = pd.DataFrame(columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
        if return_type == "arrow":
            import pyarrow as pa
            return pa.Table.from_pandas(empty)
        if return_type == "polars":
            import polars as pl
            return pl.from_pandas(empty)
        return empty

    def _one(f: Any):
        return parse_one(f, debug=debug)

    if max_workers and len(xml_files) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(_one, xml_files))
    else:
        results = [_one(f) for f in xml_files]

    if not results:
        # Fallback empty after file list processing (should be rare; engines return DataFrames/Batches)
        import pandas as pd
        empty = pd.DataFrame(columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
        if return_type == "arrow":
            import pyarrow as pa
            return pa.Table.from_pandas(empty)
        if return_type == "polars":
            import polars as pl
            return pl.from_pandas(empty)
        return empty

    if is_arrow_engine:
        return _finalize_arrow(results, return_type, categorical_columns, debug)
    else:
        return _finalize_pandas(results, return_type, categorical_columns, debug)


def _finalize_arrow(batches, return_type, categorical_columns, debug):
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

    if return_type == "pandas":
        import pandas as _pd
        try:
            return table.to_pandas(dtype_backend="pyarrow")
        except TypeError:
            try:
                return table.to_pandas(types_mapper=_pd.ArrowDtype)
            except Exception:
                return table.to_pandas()
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
