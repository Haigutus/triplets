"""API surface of the tools engines. Run: pytest tests/test_api_surface.py -s

The union of functions implemented across the tools engines (pandas / polars /
duckdb) is the reference. For each engine we report which of those are missing
from each namespace the methods are registered in: "" is the object itself
(monkey-patch), "triplets" is the df.triplets namespace.
"""

import inspect

import duckdb
import pandas
import polars

import triplets  # noqa: F401 — registers namespaces and monkey-patches
from triplets.tools import duckdb_engine, pandas_engine, polars_engine

# engine name -> (functions module, a fresh object methods get registered on)
ENGINES = {
    "pandas": (pandas_engine, pandas.DataFrame()),
    "polars": (polars_engine, polars.DataFrame()),
    "duckdb": (duckdb_engine, duckdb.connect()),
}

# namespaces methods land in; "" is the object itself
NAMESPACES = ("", "triplets")


def implemented(module) -> set[str]:
    """Public functions defined in *module*."""
    return {n for n, o in inspect.getmembers(module, inspect.isfunction)
            if not n.startswith("_") and o.__module__ == module.__name__}


def registered(obj, namespace) -> set[str]:
    """Public methods on *obj*'s namespace ("" = the object itself)."""
    target = getattr(obj, namespace, None) if namespace else obj
    return {n for n in dir(target) if not n.startswith("_")} if target is not None else set()


ALL_FUNCTIONS = set().union(*(implemented(module) for module, _ in ENGINES.values()))


def test_tools_api_surface():
    print(f"\n{len(ALL_FUNCTIONS)} tools functions: {', '.join(sorted(ALL_FUNCTIONS))}\n")
    for name, (_, obj) in ENGINES.items():
        for namespace in NAMESPACES:
            missing = sorted(ALL_FUNCTIONS - registered(obj, namespace))
            label = f"{name}.{namespace}" if namespace else name
            count = f"{len(ALL_FUNCTIONS) - len(missing)}/{len(ALL_FUNCTIONS)}"
            detail = f"missing: {', '.join(missing)}" if missing else "complete"
            print(f"  {label:18} {count:>6}  {detail}")
    assert ALL_FUNCTIONS, "no tools-engine functions discovered"
