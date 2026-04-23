import pandas as pd

from .rdflib_shacl_parser import parse_shacl
from .shacl_report import generate_shacl_report, write_shacl_report

def validate_shacl(df, rules, engine=None, check_external=True, **kwargs):
    """
    Unified entry point for SHACL validation on DataFrames.
    
    :param df: pandas.DataFrame or polars.DataFrame
    :param rules: List of SHACL constraint dictionaries
    :param engine: 'pandas', 'polars' or 'polars_parallel' (autodetected if None)
    :param check_external: Whether to validate referenced objects not present in df (default True)
    """
    # Autodetect engine if not provided
    if engine is None:
        type_str = str(type(df)).lower()
        if 'polars' in type_str: engine = 'polars'
        else: engine = 'pandas'

    if engine == 'polars':
        from .polars_shacl import validate as polars_validate
        return polars_validate(df, rules, check_external=check_external, **kwargs)
    elif engine == 'polars_parallel':
        from .polars_shacl_parallel import validate as polars_parallel_validate
        return polars_parallel_validate(df, rules, check_external=check_external, **kwargs)
    elif engine == 'pyshacl':
        from .pyshacl_shacl import validate as pyshacl_validate
        return pyshacl_validate(df, rules, **kwargs)
    else:
        from .pandas_shacl import validate as pandas_validate
        return pandas_validate(df, rules, check_external=check_external, **kwargs)

# Alias for convenience
shacl = validate_shacl

# Register Pandas Accessor
@pd.api.extensions.register_dataframe_accessor("shacl")
class SHACLAccessor:
    def __init__(self, pandas_obj): self._obj = pandas_obj
    def __call__(self, rules, engine='pandas', **kwargs): return validate_shacl(self._obj, rules, engine=engine, **kwargs)

# Register Polars Namespace (if polars is installed)
try:
    import polars as pl
    @pl.api.register_dataframe_namespace("shacl")
    class PolarsSHACL:
        def __init__(self, df): self._df = df
        def __call__(self, rules, engine='polars', **kwargs): return validate_shacl(self._df, rules, engine=engine, **kwargs)
except ImportError: pass

def register_extensions():
    """Explicitly ensure extensions are registered (usually happens on import)."""
    pass
