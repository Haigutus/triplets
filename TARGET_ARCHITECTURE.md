# Target Architecture

Best known target state for the triplets library.

---

## Engine Fallback Pattern

Every submodule with multiple engines follows the same logic — try fastest
available, fall back to simplest:

```mermaid
flowchart LR
    Call["function call\n(engine='auto')"] --> Check

    Check{try fastest\navailable?}
    Check -->|yes| Fast
    Check -->|no| Check2

    Check2{try middle?}
    Check2 -->|yes| Mid
    Check2 -->|no| Simple

    Fast["Fastest Engine\n(requires C/C++ build)"]
    Mid["Middle Engine\n(optional pip dep)"]
    Simple["Simplest Engine\n(always available)"]
```

Engines live inside their own submodule. If a compiled `.so` or optional
dependency isn't present, the import fails silently and the dispatcher
picks the next engine down the chain.

---

## Module Map

```mermaid
graph TB
    subgraph "User API"
        A["pd.read_RDF()"] --> P
        B["df.cim.type_tableview()"] --> Q
        C["df.shacl.validate()"] --> V
        D["df.sparql.query()"] --> Q
        E["df.cim.to_excel()"] --> X
    end

    subgraph "triplets"
        P["parser/"]
        Q["query/"]
        V["validation/"]
        X["export/"]
        CG["cgmes/"]
    end

    subgraph "parser/ engines"
        P --> P1["python_lxml_pandas.py ✅"]
        P --> P2["python_lxml_arrow.py 📦"]
        P --> P3["cython_pugixml_arrow.pyx 🔧"]
    end

    subgraph "export/ engines"
        X --> X1["lxml_cimxml.py ✅"]
        X --> X2["polars_cimxml.py 📦"]
        X --> X3["arrow_pugixml_cimxml.pyx 🔧"]
    end

    subgraph "query/ engines"
        Q --> Q1["pandas_engine.py ✅"]
        Q --> Q2["polars_engine.py 📦"]
        Q --> Q3["sparql_oxigraph.py 📦"]
        Q --> Q4["sparql_qlever.pyx 🔧"]
    end

    subgraph "validation/ engines"
        V --> V1["pandas_shacl.py ✅"]
        V --> V2["polars_shacl.py 📦"]
        V --> V3["pyshacl_engine.py 📦"]
    end
```

✅ always available &nbsp; 📦 optional pip dependency &nbsp; 🔧 requires C/C++ build

---

## Filesystem

```
triplets/
├── __init__.py                  # version, top-level imports, pd.read_RDF
├── _accessor.py                 # three namespaces: df.cim.*, df.shacl.*, df.sparql.*
│
├── parser/
│   ├── __init__.py              # parse() dispatcher, find_all_xml, clean_ID, get_namespace_map
│   ├── utils.py                 # shared: RDF constants, clean_ID, find_all_xml, _split_prefixed_name
│   ├── python_lxml_pandas.py    # engine: lxml → list → pandas DataFrame (default, no extra deps)
│   ├── python_lxml_arrow.py     # engine: lxml → Arrow RecordBatch (needs pyarrow)
│   ├── cython_pugixml_arrow.pyx # engine: pugixml C++ → Arrow RecordBatch (needs C++ build + pyarrow)
│   └── vendor/
│       └── pugixml/             # vendored pugixml source (MIT, ~300KB)
│
├── query/
│   ├── __init__.py              # dispatchers for tableview/references/filter + sparql
│   ├── pandas_engine.py         # engine: pure pandas (default)
│   ├── polars_engine.py         # engine: polars (4-29x)
│   ├── sparql_oxigraph.py       # engine: oxigraph via oxrdflib
│   └── sparql_qlever.pyx        # engine: libqlever (14-240x)
│       qlever_wrapper.h         # C++ glue for libqlever
│       qlever_wrapper.cpp
│
├── validation/
│   ├── __init__.py              # validate() dispatcher
│   ├── pandas_shacl.py          # engine: pandas (default)
│   ├── polars_shacl.py          # engine: polars (2.4x)
│   ├── pyshacl_engine.py        # engine: pyshacl (external reference impl)
│   ├── rdflib_shacl_parser.py   # parse SHACL constraints from RDF/XML via rdflib
│   ├── triplets_shacl_parser.py # parse SHACL constraints from triplet DataFrames
│   └── shacl_report.py          # SHACL validation report generation
│
├── export/
│   ├── __init__.py
│   ├── excel.py                 # export_to_excel
│   ├── cimxml.py                # export_to_cimxml, generate_xml
│   └── networkx.py              # export_to_networkx
│
├── cgmes/
│   ├── __init__.py
│   └── tools.py                 # CGMES metadata, filename, FullModel utilities
│
├── export_schema/               # ENTSO-E JSON schema files (package data)
│   └── *.json
│
├── rdfs_tools/                  # RDF Schema utilities
│   └── *.py
│
└── rdf_parser.py                # backwards-compatible re-export shim (deprecated)

tests/
├── conftest.py                  # fixtures, markers, skip logic
├── data/
│   ├── tiny_eq.xml              # <20 triples, committed
│   └── tiny_shacl.xml           # minimal SHACL shapes
├── test_parser.py               # python_lxml_pandas + python_lxml_arrow + cython_pugixml_arrow engines
├── test_query.py                # pandas + polars + sparql engines
├── test_validation.py           # pandas + polars + pyshacl engines
├── test_export.py               # excel, cimxml roundtrip
├── test_cgmes.py                # metadata, filenames
├── test_accessor.py             # df.cim.*, df.shacl.*, df.sparql.* namespaces
├── integration/
│   └── test_realgrid.py         # full CGMES dataset
└── benchmarks/
    ├── bench_parser.py
    ├── bench_query.py
    └── bench_sparql.py

docs/
├── benchmarks/
│   ├── parsing.md
│   ├── queries.md
│   ├── shacl.md
│   └── sparql_engines.md
└── guides/
    └── shacl_quickstart.md

pixi.toml
```

---

## Engine Tables

### parser/

| Engine | File | Requires | Speed |
|--------|------|----------|-------|
| `python_lxml_pandas` | `python_lxml_pandas.py` | lxml + pandas (core deps) | 1x baseline, **always works** |
| `python_lxml_arrow` | `python_lxml_arrow.py` | lxml + pyarrow (`pip install triplets[arrow]`) | ~1x parse, better interop |
| `cython_pugixml_arrow` | `cython_pugixml_arrow.pyx` | C++ build + pugixml + pyarrow | 12.9x |

`python_lxml_pandas` is the old proven lxml → list-of-tuples → `pd.DataFrame()` path, restored
as the default so the core package has **zero dependencies beyond lxml + pandas**.

`python_lxml_arrow` uses lxml for XML parsing but streams to Arrow `StringBuilder` builders
instead of Python lists, producing `pa.RecordBatch`. Better for polars interop and
dictionary-encoding (categorical columns). Requires pyarrow.

`cython_pugixml_arrow` does everything in C++ (pugixml parse + Arrow C++ builders via Cython).
Fastest path. Supports mmap for local files.

See `CYTHON_PERFORMANCE_IDEAS.md` (in this directory) for additional optimization opportunities
beyond mmap (in-builder dictionary encoding for KEY/INSTANCE_ID, single-pass attribute walking,
nogil for the hot loop, builder Reserve, etc.).

Fallback: `cython_pugixml_arrow` → `python_lxml_arrow` → `python_lxml_pandas`

### query/

| Engine | File | Requires | Speed | Query type |
|--------|------|----------|-------|------------|
| `pandas` | `pandas_engine.py` | pandas (core dep) | 1x, **always works** | DataFrame |
| `polars` | `polars_engine.py` | `pip install triplets[polars]` | 4-29x | DataFrame |
| `sparql_oxigraph` | `sparql_oxigraph.py` | `pip install triplets[sparql]` | 1x | SPARQL |
| `sparql_qlever` | `sparql_qlever.pyx` | C++ build + libqlever | 14-240x | SPARQL |

Fallback (DataFrame queries): `polars` → `pandas`
Fallback (SPARQL queries): `sparql_qlever` → `sparql_oxigraph`

Future: `mql_engine.py` (CIMdesk Model Query Language), other graph query engines.

### export/

| Engine | File | Requires | Speed |
|--------|------|----------|-------|
| `lxml` | `lxml_cimxml.py` | lxml (core dep) | 1x baseline, **always works** |
| `polars` | `polars_cimxml.py` | `pip install triplets[polars]` | 6.4x |
| `arrow_pugixml` | `arrow_pugixml_cimxml.pyx` | C++ build + pugixml + pyarrow | 11x |

Fallback: `arrow_pugixml` → `polars` → `lxml`

### validation/

| Engine | File | Requires | Speed |
|--------|------|----------|-------|
| `pandas` | `pandas_shacl.py` | pandas (core dep) | 1x, **always works** |
| `polars` | `polars_shacl.py` | `pip install triplets[polars]` | 2.4x |
| `pyshacl` | `pyshacl_engine.py` | `pip install triplets[pyshacl]` | external reference |

Fallback: `polars` → `pandas`
`pyshacl` is explicit only (`engine="pyshacl"`) — not in auto fallback chain.

---

## Dispatcher Pattern

Same structure in every submodule `__init__.py`:

```python
# parser/__init__.py

def _auto_engine():
    """Pick best available parser engine (fastest first)."""
    try:
        from .cython_pugixml_arrow import load_rdf_to_arrow  # noqa: F401
        return "cython_pugixml_arrow"
    except ImportError:
        pass
    try:
        from .python_lxml_arrow import load_rdf_to_arrow  # noqa: F401
        return "python_lxml_arrow"
    except ImportError:
        pass
    return "python_lxml_pandas"

def parse(sources, engine="auto", return_type="pandas", **kwargs):
    if engine == "auto":
        engine = _auto_engine()
    if engine == "cython_pugixml_arrow":
        from .cython_pugixml_arrow import load_rdf_to_arrow as fn
    elif engine == "python_lxml_arrow":
        from .python_lxml_arrow import load_rdf_to_arrow as fn
    else:
        from .python_lxml_pandas import load_rdf_to_dataframe as fn
    # ... dispatch to fn, handle batching, return_type conversion
```

```python
# query/__init__.py  (same pattern, different engines)

def _auto_engine(df):
    """Pick best available engine for the given DataFrame."""
    if "polars" in type(df).__module__:
        return "polars"
    try:
        import polars
        return "polars"
    except ImportError:
        return "pandas"

def type_tableview(df, *args, engine="auto", **kwargs):
    if engine == "auto":
        engine = _auto_engine(df)
    if engine == "polars":
        from .polars_engine import type_tableview as fn
    else:
        from .pandas_engine import type_tableview as fn
    return fn(df, *args, **kwargs)

# ... same pattern for every function
```

---

## Three Accessors

Three separate namespaces, each focused on its domain:

```python
# _accessor.py
import pandas as pd
from . import query, validation, export

# ── df.cim.* — CIM data query & export ──
@pd.api.extensions.register_dataframe_accessor("cim")
class CimAccessor:
    def __init__(self, df):
        self._df = df

    # Query
    def type_tableview(self, *a, **kw):     return query.type_tableview(self._df, *a, **kw)
    def key_tableview(self, *a, **kw):      return query.key_tableview(self._df, *a, **kw)
    def id_tableview(self, *a, **kw):       return query.id_tableview(self._df, *a, **kw)
    def references_to(self, *a, **kw):      return query.references_to(self._df, *a, **kw)
    def references_from(self, *a, **kw):    return query.references_from(self._df, *a, **kw)
    def references(self, *a, **kw):         return query.references(self._df, *a, **kw)
    def filter_by_type(self, *a, **kw):     return query.filter_by_type(self._df, *a, **kw)
    def types_dict(self, **kw):             return query.types_dict(self._df, **kw)

    # Export
    def to_excel(self, path, **kw):         return export.to_excel(self._df, path, **kw)
    def to_cimxml(self, path, **kw):        return export.to_cimxml(self._df, path, **kw)

# ── df.shacl.* — SHACL validation ──
@pd.api.extensions.register_dataframe_accessor("shacl")
class ShaclAccessor:
    def __init__(self, df):
        self._df = df

    def validate(self, rules, **kw):        return validation.validate(self._df, rules, **kw)
    def parse_shapes(self, path, **kw):     return validation.parse_shacl(path, **kw)
    def report(self, violations, **kw):     return validation.report(violations, **kw)

# ── df.sparql.* — SPARQL queries ──
@pd.api.extensions.register_dataframe_accessor("sparql")
class SparqlAccessor:
    def __init__(self, df):
        self._df = df

    def query(self, query_str, **kw):       return query.sparql(self._df, query_str, **kw)

# ── Polars: same three namespaces ──
try:
    import polars as pl

    @pl.api.register_dataframe_namespace("cim")
    class PolarsCimAccessor:
        def __init__(self, df): self._df = df
        def type_tableview(self, *a, **kw): return query.type_tableview(self._df, *a, **kw)
        # ... same methods

    @pl.api.register_dataframe_namespace("shacl")
    class PolarsShaclAccessor:
        def __init__(self, df): self._df = df
        def validate(self, rules, **kw):    return validation.validate(self._df, rules, **kw)

    @pl.api.register_dataframe_namespace("sparql")
    class PolarsSparqlAccessor:
        def __init__(self, df): self._df = df
        def query(self, query_str, **kw):   return query.sparql(self._df, query_str, **kw)

except ImportError:
    pass
```

---

## Usage

```python
import triplets

# ── Parse ──
df = triplets.parser.parse(["grid_EQ.xml"])                                      # auto (best available)
df = triplets.parser.parse(["grid_EQ.xml"], engine="python_lxml_pandas")         # explicit: no pyarrow needed
df = triplets.parser.parse(["grid_EQ.xml"], engine="python_lxml_arrow")          # explicit: arrow intermediate
df = triplets.parser.parse(["grid_EQ.xml"], engine="cython_pugixml_arrow")       # explicit: fastest
df = pd.read_RDF(["grid_EQ.xml"])                                                # convenience

# ── CIM query (df.cim.*) ──
df.cim.type_tableview("ACLineSegment")
df.cim.type_tableview("ACLineSegment", engine="polars")
df.cim.references_to("some-uuid")
df.cim.types_dict()

# ── SPARQL query (df.sparql.*) ──
df.sparql.query("SELECT ?s WHERE { ?s a cim:Substation }")
df.sparql.query("...", engine="sparql_qlever")

# ── SHACL validation (df.shacl.*) ──
rules = df.shacl.parse_shapes("shapes.xml")
violations = df.shacl.validate(rules)
violations = df.shacl.validate(rules, engine="polars")
violations = df.shacl.validate(rules, engine="pyshacl")

# ── Export (df.cim.*) ──
df.cim.to_excel("output.xlsx")
df.cim.to_cimxml("output.zip")

# ── Direct calls (same API, no accessor needed) ──
triplets.query.type_tableview(df, "ACLineSegment", engine="polars")
triplets.query.sparql(df, "SELECT ...", engine="sparql_qlever")
triplets.validation.validate(df, rules, engine="pyshacl")
```

---

## rdf_parser.py Migration

Current `rdf_parser.py` (2137 lines, 33 functions) → decomposed into modules:

| Functions | New home |
|---|---|
| `load_RDF_*`, `find_all_xml`, `clean_ID`, `get_namespace_map` | `parser/` |
| `type_tableview`, `key_tableview`, `id_tableview`, `references_*`, `filter_by_*`, `types_dict`, `get_object_data`, `update_*`, `remove_*`, `set_VALUE_*`, `diff_*`, `tableview_to_triplet` | `query/` |
| `export_to_excel` | `export/excel.py` |
| `export_to_cimxml`, `generate_xml` | `export/cimxml.py` |
| `export_to_networkx` | `export/networkx.py` |

`rdf_parser.py` becomes a thin re-export shim → deprecation warnings → removal.

---

## pixi.toml

```toml
[project]
name = "triplets"
version = "0.1.0"
description = "RDF/XML toolkit for ENTSO-E/CGMES power grid data"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"
pandas = ">=2.0"
lxml = ">=4.9"
aniso8601 = "*"

[feature.arrow.dependencies]
pyarrow = ">=14.0.0"

[feature.polars.dependencies]
polars = ">=1.0.0"
pyarrow = ">=14.0.0"

[feature.validation.dependencies]
rdflib = ">=6.0"

[feature.pyshacl.dependencies]
rdflib = ">=6.0"
[feature.pyshacl.pypi-dependencies]
pyshacl = ">=0.24.1"

[feature.sparql.pypi-dependencies]
oxrdflib = ">=0.5.0"

[feature.build.dependencies]
cython = ">=3.0"
cmake = ">=3.27"
boost = ">=1.83"
icu = ">=70"
openssl = "*"
zstd = "*"
jemalloc = "*"

[feature.excel.dependencies]
openpyxl = ">=3.1.5"

[feature.dev.dependencies]
pytest = ">=7.0"

[environments]
default = { features = ["validation", "arrow", "polars", "excel"] }
full = { features = ["validation", "arrow", "polars", "sparql", "pyshacl", "excel"] }
build = { features = ["validation", "arrow", "polars", "sparql", "pyshacl", "excel", "build"] }
dev = { features = ["validation", "arrow", "polars", "sparql", "pyshacl", "excel", "build", "dev"] }

[tasks]
test = "pytest tests/"
test-unit = "pytest tests/ -m 'not integration and not benchmark'"
test-integration = "pytest tests/ -m integration"
bench = "pytest tests/benchmarks/"
build-cython-pugixml-arrow = "python setup_cython_parser.py build_ext --inplace"
build-qlever = "cd triplets/query && cmake ..."
```

---

## Test Harness

Each test file tests **all engines** for its module, skipping when deps are missing:

```python
# test_query.py
import pytest
import triplets

HAS_POLARS = importlib.util.find_spec("polars") is not None
HAS_OXRDFLIB = importlib.util.find_spec("oxrdflib") is not None

class TestPandasEngine:
    def test_type_tableview(self, small_df):
        result = triplets.query.type_tableview(small_df, "Substation", engine="pandas")
        assert len(result) > 0

class TestPolarsEngine:
    pytestmark = pytest.mark.skipif(not HAS_POLARS, reason="polars not installed")

    def test_type_tableview(self, small_df):
        result = triplets.query.type_tableview(small_df, "Substation", engine="polars")
        assert len(result) > 0

class TestSparqlOxigraph:
    pytestmark = pytest.mark.skipif(not HAS_OXRDFLIB, reason="oxrdflib not installed")

    def test_count_query(self, small_df):
        result = triplets.query.sparql(small_df, "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }")
        assert len(result) > 0

class TestSparqlQlever:
    pytestmark = pytest.mark.native

    def test_count_query(self, small_df):
        result = triplets.query.sparql(small_df, "SELECT ...", engine="sparql_qlever")
        assert len(result) > 0
```

pytest markers:
- `@pytest.mark.integration` — needs `test_data/` submodules
- `@pytest.mark.benchmark` — slow, performance measurement
- `@pytest.mark.native` — needs compiled Cython extensions

---

## Naming Convention

| Pattern | Example | Meaning |
|---|---|---|
| `{runtime}_{lib}_{output}.py` | `python_lxml_pandas.py` | Python + lxml, produces pandas DataFrame |
| `{runtime}_{lib}_{output}.py` | `python_lxml_arrow.py` | Python + lxml, produces Arrow tables |
| `{runtime}_{lib}_{output}.pyx` | `cython_pugixml_arrow.pyx` | Cython + pugixml, produces Arrow tables |
| `{framework}_engine.py` | `pandas_engine.py` | query engine using pandas |
| `{framework}_shacl.py` | `polars_shacl.py` | SHACL validation using polars |
| `{tool}_engine.py` | `pyshacl_engine.py` | validation engine wrapping pyshacl |
| `sparql_{backend}.py` | `sparql_oxigraph.py` | SPARQL query via oxigraph |
| `{source}_shacl_parser.py` | `rdflib_shacl_parser.py` | parse SHACL shapes via rdflib |

For **parser/ engines**, the three-part `{runtime}_{lib}_{output}` convention makes clear:
- **runtime**: `python` (pure Python) or `cython` (compiled)
- **lib**: which XML library (`lxml`, `pugixml`)
- **output**: what it produces (`pandas` DataFrame or `arrow` RecordBatch)

---

## Summary

| Principle | Implementation |
|---|---|
| **Engines inside submodules** | `parser/python_lxml_pandas.py`, `parser/cython_pugixml_arrow.pyx`, `query/sparql_qlever.pyx` |
| **Fallback to simplest** | Auto-detect fastest available, always fall back to pure pandas/lxml |
| **`engine=` everywhere** | Every public function accepts `engine=` parameter |
| **SPARQL under query** | `query/sparql_oxigraph.py`, `query/sparql_qlever.pyx` — alongside pandas/polars |
| **Three accessors** | `df.cim.*` (query/export), `df.shacl.*` (validation), `df.sparql.*` (SPARQL) |
| **Keep pyshacl** | `engine="pyshacl"` in validation |
| **Uniform naming** | `{runtime}_{lib}_{output}.py` for parser, `{framework}_{purpose}.py` elsewhere |
| **pixi for builds** | C/C++ deps managed alongside Python |
| **Gradual migration** | `rdf_parser.py` → re-export shim → deprecation → removal |
