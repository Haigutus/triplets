# Triplets Repo Cleanup & Phased Release Plan

## Context

The `triplets` repo started as an RDF/XML parser for ENTSO-E/CGMES data but expanded into SHACL validation, SPARQL support, Cython/Rust parser benchmarking, and Polars integration — all on the `dev_shacl` branch. The result is a mix of production code, benchmark artifacts, and exploration scripts. This plan provides a step-by-step path from the current mess to a publishable, maintainable package.

---

## Phase 1: Clean Up the Working Tree

**Branch:** `cleanup/phase1` (from `dev_shacl`)

### 1.1 Delete exploration artifacts from root

**Generated outputs** (delete):
- `polars_benchmark_results.txt`, `violations_*.csv`, `shacl_report_*.xml`, `triplets_0-0-7_*.xlsx`

**Superseded scripts** (delete):
- `main_new.py`, `setup_cython.py` (logic moves to Phase 4)

### 1.2 Move markdown reports to `docs/benchmarks/`

Consolidate into fewer files — extract key findings (approach tested, performance numbers) into:
- `docs/benchmarks/parsing.md` — from `PARSING_BENCHMARK.md`, `IMPORT_PERFORMANCE_ANALYSIS.md`, `RDF_DATAFRAME_PERFORMANCE_REPORT.md`, `RDF_DATAFRAME_IMPLEMENTATION_GUIDE.md`
- `docs/benchmarks/queries.md` — from `QUERY_BENCHMARK.md`, `POLARS_PERFORMANCE_COMPARISON.md`
- `docs/benchmarks/shacl.md` — from `SHACL_PARITY_REPORT.md`, `SHACL_TESTING_SUMMARY.md`, `SPARQL_VALIDATION_SUMMARY.md`
- `docs/guides/shacl_quickstart.md` — from `SHACL_QUICKSTART.md`, `COMPLETE_VALIDATION_GUIDE.md`

Delete all original root-level .md reports after consolidation.

### 1.3 Salvage tests into `tests/`

Create proper pytest structure:
```
tests/
    __init__.py
    conftest.py              # shared fixtures (test_data paths, sample DataFrames)
    test_rdf_parser.py       # basic parsing from test_data files
    test_shacl_pandas.py     # from test_shacl_with_real_data.py, test_shacl_parse_and_validate.py
    test_shacl_polars.py     # from test_shacl_polars.py
    test_queries.py          # from benchmark_queries.py (correctness checks, not timing)
```

Delete original root-level test/benchmark scripts after salvaging:
- `test_shacl_with_real_data.py`, `test_shacl_parse_and_validate.py`, `test_shacl_polars.py`, `test_shacl_sparql.py`, `test_new_validate.py`, `test_import_performance.py`
- `benchmark_parsers.py`, `benchmark_queries.py`, `cross_check_shacl.py`

### 1.4 Delete experimental parser variants from `triplets/`

Remove all 10 variants — the findings are preserved in `docs/benchmarks/parsing.md`:
- `rdf_parser_arrow.py`, `rdf_parser_cython.py`, `rdf_parser_cython_arrow.py`, `rdf_parser_expat.py`, `rdf_parser_lxml_arrow.py`, `rdf_parser_lxml_tuned.py`, `rdf_parser_polars_clean.py`, `rdf_parser_pugixml.py`, `rdf_parser_pygixml.py`, `rdf_parser_pygixml_tuned.py`

### 1.5 Delete Cython/Rust build artifacts (return in Phase 4)

- Delete `triplets/rdf_extract_cython.pyx`, `.cpp`, `.so` (plain pugixml — not the fastest)
- Delete `triplets/rdf_extract_lxml_arrow.pyx`, `.cpp`, `.so` (lxml+Arrow — not the fastest)
- **Preserve** `triplets/rdf_extract_cython_arrow.pyx` + `.cpp` — move to `triplets/_native/` in Phase 4
- Delete `rdf_parser_rust/` directory (3.6x vs cython_arrow's 12.9x — findings preserved in docs)

### 1.6 Remove `triplets/shacl/` directory

Delete entirely (superseded by `validation/`). Remove from `triplets/__init__.py` imports and `__all__`.

**Note for Phase 2:** Refactor `validation/pandas_shacl.py` and `validation/polars_shacl.py` to follow the cleaner code formatting style from `shacl/validators.py`.

### 1.7 Clean up `validation/`

- Delete `polars_shacl_parallel.py` (incomplete — logical ops return empty LazyFrames)
- Keep `pyshacl_shacl.py` as optional reference engine (already import-guarded)
- Remove `polars_parallel` engine option from `validation/__init__.py`

### 1.8 Update `.gitignore`

Add:
```
# Generated outputs
*.csv
*.xlsx
shacl_report_*.xml

# Cython artifacts
*.cpp
*.so
*.c
```

### 1.9 Critical files to modify
- `triplets/__init__.py` — remove `shacl` import, keep rest
- `triplets/validation/__init__.py` — remove `polars_parallel` engine branch
- `.gitignore` — add patterns above

---

## Phase 2: Engine Abstraction & Code Quality

**Branch:** `feature/engine-abstraction` (from Phase 1 result)

### 2.1 Refactor validators for consistent code style

Refactor `validation/pandas_shacl.py` and `validation/polars_shacl.py` to follow the cleaner formatting from the old `shacl/validators.py` (which we deleted in Phase 1 but whose style we liked).

### 2.2 Conditional imports in `__init__.py`

```python
from . import rdf_parser, cgmes_tools, rdfs_tools, export_schema, validation

# Optional polars acceleration
try:
    from . import polars_queries
    from . import fast_queries
except ImportError:
    pass

from importlib.metadata import version
__version__ = version("triplets")
```

### 2.3 Make `fast_queries.py` explicit opt-in

Change from auto-monkey-patching on import to requiring `triplets.fast_queries.install()`. This prevents surprising behavior.

### 2.4 Critical files
- `triplets/__init__.py`
- `triplets/fast_queries.py`
- `triplets/validation/pandas_shacl.py`
- `triplets/validation/polars_shacl.py`

---

## Phase 3: Packaging Modernization — First Publishable Release

**Branch:** `feature/modern-packaging` (from Phase 2 result)

### 3.1 Replace versioneer with `setuptools-scm`

Delete: `versioneer.py`, `setup.py`, `setup.cfg`, `MANIFEST.in`, `triplets/_version.py`

### 3.2 Final `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68", "setuptools-scm>=8"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "triplets"
dynamic = ["version"]
requires-python = ">=3.10"
description = "RDF/XML processing and SHACL validation for ENTSO-E/CGMES power grid data"
readme = "README.md"
license = "MIT"
dependencies = [
    "pandas>=2.0",
    "lxml>=4.9",
    "aniso8601",
]

[project.optional-dependencies]
validation = ["rdflib>=6.0"]
polars = ["polars>=1.0.0", "pyarrow>=14.0.0"]
pyshacl = ["pyshacl>=0.24.1", "rdflib>=6.0"]
sparql = ["oxrdflib>=0.5.0", "rdflib>=6.0"]
excel = ["openpyxl>=3.1.5"]
all = ["triplets[validation,polars,sparql,excel]"]
dev = ["triplets[all,pyshacl]", "pytest>=7.0"]

[tool.setuptools-scm]
```

**Key change:** `rdflib` moves from core dep to `validation` extra. The core parser doesn't need it. Users can parse SHACL via `triplets_shacl_parser.py` (DataFrame-based) without rdflib.

### 3.3 Tag `v0.1.0` and publish

```bash
git tag v0.1.0
uv build
uv publish
```

This is the **first modern release** with SHACL validation support.

### 3.4 Critical files
- `pyproject.toml`
- `triplets/__init__.py` (version from importlib.metadata)
- Delete: `versioneer.py`, `setup.py`, `setup.cfg`, `MANIFEST.in`, `triplets/_version.py`

---

## Phase 4: Cython Fast Engine (Optional Build)

**Branch:** `feature/cython-build` (from Phase 3 result)

### 4.1 Create `triplets/_native/`

```
triplets/_native/
    __init__.py                    # try-import, exports NATIVE_AVAILABLE flag
    rdf_extract_cython_arrow.pyx   # the 12.9x fastest variant
    vendor/pugixml/                # vendored pugixml source (MIT, ~300KB)
        pugixml.cpp
        pugixml.hpp
```

### 4.2 Env-gated build in `setup.py`

Keep a thin `setup.py` alongside `pyproject.toml` — only defines Cython extensions when `TRIPLETS_BUILD_NATIVE=1`:

```python
import os
from setuptools import setup
ext_modules = []
if os.environ.get("TRIPLETS_BUILD_NATIVE"):
    from Cython.Build import cythonize
    # define cython_arrow extension with vendored pugixml
    ext_modules = cythonize(...)
setup(ext_modules=ext_modules)
```

Users build with: `TRIPLETS_BUILD_NATIVE=1 uv pip install .`

### 4.3 Engine auto-detection

`triplets/_native/__init__.py` tries to import the compiled extension. The parser module checks `_native.NATIVE_AVAILABLE` and uses it when available, otherwise falls back to lxml.

### 4.4 Tag `v0.2.0`

---

## Phase 5: SPARQL — qlever Evaluation & Structure

**Branch:** `feature/sparql` (from Phase 3 — independent of Phase 4)

### 5.1 Create `triplets/sparql/`

```
triplets/sparql/
    __init__.py           # query() dispatcher
    oxigraph_store.py     # current oxrdflib implementation (extracted from test_shacl_sparql.py)
    qlever_store.py       # future: HTTP-based qlever client
```

### 5.2 Benchmark qlever vs oxigraph

- Load same CGMES test data into both engines
- Run SHACL SPARQL constraints through both
- qlever runs as external server (HTTP SPARQL endpoint), oxigraph is embedded

### 5.3 Optional deps

```toml
sparql-oxigraph = ["oxrdflib>=0.5.0", "rdflib>=6.0"]
sparql-qlever = ["sparqlwrapper>=2.0"]
```

---

## Phase 6: CI/CD & Platform Wheels (Future)

### 6.1 GitHub Actions: test matrix
- Python 3.10-3.13 on ubuntu-latest
- `uv sync --all-extras && pytest tests/`

### 6.2 GitHub Actions: PyPI publish
- Trigger on `v*` tag push
- `uv build && uv publish`

### 6.3 cibuildwheel for native extensions
- Build `triplets` wheels with Cython extensions for linux/macos/windows
- `TRIPLETS_BUILD_NATIVE=1` in build environment

---

## Execution Order

| Phase | What | Result |
|-------|------|--------|
| 1 | Clean up | Clean working tree, no artifacts |
| 2 | Engine abstraction + validator refactor | Clean architecture, consistent code style |
| 3 | Modern packaging | **First PyPI release: v0.1.0** |
| 4 | Cython build | Fast engine for power users: v0.2.0 |
| 5 | SPARQL/qlever | Structured SPARQL support: v0.3.0 |
| 6 | CI/CD | Automated testing & wheel publishing |

Phases 4, 5, 6 are independent and can be reordered.

## Verification

After each phase, verify with:
```bash
uv pip install -e ".[dev]"
pytest tests/
python -c "import triplets; print(triplets.__version__)"
```
