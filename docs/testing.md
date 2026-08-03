# Testing

> **Single source of truth:** edit this file only. The published docs include it
> from `docs/source/guides/testing.md` via MyST `{include}`.

## Quick Start

```shell
# run all tests (performance benchmarks are deselected by default via pyproject addopts;
# tests needing data that is not present skip themselves)
pytest -n auto -q

# run only parser tests
pytest tests/test_parser.py tests/test_parity_parser.py -v
```

## Requirements

```shell
pip install -e ".[dev]"
```

The `dev` extra pulls in every test dependency (pytest, pytest-xdist,
pytest-benchmark, polars, pyarrow, networkx, openpyxl, rdflib, pyshacl,
pyoxigraph, oxrdflib, and `jsonschema>=4.0` — used by the SARIF
schema-conformance test against `tests/data/sarif-schema-2.1.0.json`).

The CI test job installs a narrower set of feature extras:

```shell
pip install -e ".[dev,validation,oxigraph,duckdb,excel,networkx]"
```

| Extra | Enables |
|-------|---------|
| `validation` | `pyshacl`, `rdflib` — SHACL validation tests |
| `oxigraph` | `pyoxigraph`, `oxrdflib` — oxigraph SPARQL engine + n-quads round-trip |
| `duckdb` | `duckdb` — the duckdb tools engine (parity tests) |
| `excel` | `openpyxl` — Excel import/export |
| `networkx` | `networkx` — graph tools |

For the compiled cython engine tests, build the extension first:

```shell
python setup_cython_parser.py build_ext --inplace
```

## Test Files

| File | What it tests | Needs external data? |
|------|--------------|----------------------|
| `test_parser.py` | `parse()`, `clean_ID`, `find_all_xml`, nodeID support, categorical encoding, return types | No (uses `tests/data/minimal_cim.xml`) |
| `test_parity_parser.py` | Cross-engine parser parity, `pandas.read_RDF` registration, all engines produce identical output | No |
| `test_import.py` | Loading NC and CGMES files, column structure, metadata (Distribution, NamespaceMap) | Yes (relicapgrid submodule) |
| `test_tools.py` | Data-manipulation tool functions on the Svedala IGM dataset | Yes (relicapgrid submodule) |
| `test_parity_tools.py` | Cross-engine tool parity (pandas/polars/duckdb), including `type_tableview` / pivot correctness | Yes (relicapgrid submodule) |
| `test_benchmarks_realgrid.py` | Performance benchmarks for parsing and tools across all engines | Yes (RealGrid LFS zip) |
| `test_compiled_modules.py` | Compiled-extension import guard (see below) | No |

## Test Data

**Committed** (always available):
- `tests/data/minimal_cim.xml` — 5 RDF objects, covers Substation, VoltageLevel, BaseVoltage, ConnectivityNode (with `rdf:nodeID`)
- `tests/data/sarif-schema-2.1.0.json` — official SARIF 2.1.0 schema for the exporter conformance test

**Submodule** (`git submodule update --init test_data/relicapgrid`):
- `test_data/relicapgrid/` — NC, CGMES, and Svedala IGM files for import and tools tests

**Git LFS** (`git lfs pull`):
- `test_data/TestConfigurations_packageCASv2.0/RealGrid/CGMES_v2.4.15_RealGridTestConfiguration_v2.zip`
  — full CGMES dataset (~1.14M rows) for benchmarks. This is an LFS-committed zip
  (`.gitattributes`: `*.zip filter=lfs`), **not** a submodule.

Tests that need submodule or LFS data are automatically skipped when the files are not present.

## Engine Detection

Tests auto-detect which engines are available and parametrize accordingly:

- `python_lxml_pandas` — always tested (no extra deps)
- `python_lxml_arrow` — tested if pyarrow is installed
- `cython_pugixml_arrow` — tested if the compiled extension is present

The `parser_engine` fixture in `conftest.py` provides this parametrization.

## Running Specific Engine Tests

```shell
# only the default engine (no pyarrow needed)
pytest tests/test_parser.py -v -k "python_lxml_pandas"

# only arrow engines
pytest tests/test_parser.py -v -k "python_lxml_arrow or cython_pugixml_arrow"

# cross-engine parity
pytest tests/test_parity_parser.py -v
```

## Compiled-Module Guard

`tests/test_compiled_modules.py` asserts that the compiled extensions
(`triplets.parser.cython_pugixml_arrow`, `triplets.export.cimxml_cython_pugixml`)
import successfully. It is **skipped unless `TRIPLETS_REQUIRE_COMPILED=1`** is set.

Everywhere else the compiled-engine tests skip silently when the extension is not
built. `build-wheels.yml` sets `TRIPLETS_REQUIRE_COMPILED=1` in the wheel test
environment so a wheel whose extensions failed to build fails the run instead of
turning CI green by skipping.

```shell
TRIPLETS_REQUIRE_COMPILED=1 pytest tests/test_compiled_modules.py -v
```

## Benchmarks

Tests marked `performance` are **deselected by default** (pyproject
`addopts = '-m "not performance"'` — the full benchmark suite takes ~35 min).
Select them explicitly to override the default:

```shell
pytest -m performance                 # all performance benchmarks
```

Benchmarks use `pytest-benchmark` and require the RealGrid LFS zip (`git lfs pull`):

```shell
# parse benchmarks (all engines, pandas + polars output)
pytest tests/test_benchmarks_realgrid.py -m performance -k "parse" -v

# save results to JSON
pytest tests/test_benchmarks_realgrid.py -m performance \
  --benchmark-json=tests/performance_results/parsers_performance.json -k "parse"
```

## pixi Tasks

If using pixi:

```shell
pixi run test                         # all tests
pixi run build-cython-pugixml-arrow   # build cython extension
```

Parser-only run:

```shell
pixi run pytest tests/test_parity_parser.py -q
```

## CI Workflows

Two GitHub Actions workflows run automatically.

### `tests.yml` — unit tests

Runs on push to `main`, on pull requests, and on manual dispatch. Pure-Python
engines only (the compiled parser and qlever extensions are covered by
`build-wheels.yml`).

- **Matrix:** CPython 3.11, 3.13, 3.14 on `ubuntu-latest`
- **Checkout:** LFS enabled (RealGrid zips); inits only the
  `test_data/relicapgrid` submodule (not `vendor/qlever`)
- **Install:** `pip install -e .[dev,validation,oxigraph,duckdb,excel,networkx]`
- **Run:** `pytest -n auto -q`

### `build-wheels.yml` — wheels + publish

| Trigger | What happens |
|---------|-------------|
| **GitHub Release** (pre-release or final) | Build sdist + wheels, publish to PyPI |
| **Pull Request** | Build sdist + wheels only (no publish) |
| **Manual** (workflow_dispatch) | Build sdist + wheels only (no publish) |

Publishing uses PyPI **trusted publishing** (OIDC, no API tokens) via the `pypi`
environment. To enable it: on [PyPI](https://pypi.org/manage/project/triplets/settings/publishing/)
add a publisher for GitHub repo `Haigutus/triplets`, workflow `build-wheels.yml`,
environment `pypi`.

Each wheel is tested during the build with `TRIPLETS_REQUIRE_COMPILED=1`, so a
wheel missing its compiled extensions fails rather than skips.

#### Build matrix

Wheels are built only for the active runner targets:

| Platform | Runner | Architecture |
|----------|--------|-------------|
| Linux (manylinux) | `ubuntu-latest` | x86_64 |
| macOS (Apple Silicon) | `macos-14` | arm64 |
| Windows | `windows-latest` | AMD64 |

CPython 3.11–3.14 (`requires-python >=3.11`). Each wheel includes the compiled
`cython_pugixml_arrow` extension (usable when pyarrow is also installed). Linux
aarch64 (QEMU) and macOS x86_64 (Intel, macos-13) targets are present but
commented out in the workflow; Intel-Mac users can install from the sdist.

## Publishing a Release

Tags use bare numbers (matching existing releases like `0.0.17`).

1. Tag the release:
   ```shell
   git tag 0.2.0a2       # pre-release
   git push origin 0.2.0a2
   ```
2. Create a GitHub Release from the tag (mark pre-releases as such).
3. `build-wheels.yml` builds the sdist + wheels and publishes to PyPI.

Install:

```shell
pip install --pre triplets       # latest pre-release
pip install triplets==0.2.0a2    # pin an exact pre-release
pip install triplets             # latest stable
```

PyPI treats `a` (alpha), `b` (beta), and `rc` versions all as pre-releases —
they are installed only when `--pre` is passed or a specific version is pinned.

| Tag | PyPI version | pip install |
|-----|-------------|-------------|
| `0.2.0a2` | `0.2.0a2` (pre-release) | `pip install --pre triplets` |
| `0.2.0rc1` | `0.2.0rc1` (pre-release) | `pip install --pre triplets` |
| `0.2.0` | `0.2.0` (stable) | `pip install triplets` |

### Verifying the Cython Engine in a Wheel

After installing a wheel, verify the compiled extension is included:

```python
import triplets

# Check which engine auto-detection picks
engine_name, _ = triplets.parser.get_engine("auto")
print(engine_name)  # "cython_pugixml_arrow" if the wheel has it

# Or import directly
from triplets.parser import cython_pugixml_arrow
print("cython engine available")
```

## Warnings policy

The suite runs **warning-free** and must stay that way: `pyproject.toml`
`filterwarnings` enables `always::ResourceWarning` (so unclosed handles fail
review even though Python hides them by default) and ignores exactly two
rdflib 7.6.0 self-deprecations (its own internals call its own deprecated
`Dataset.default_context`/`Dataset.identifier`; drop the ignores when rdflib
migrates). A test that legitimately exercises a deprecated triplets alias
should assert it with `pytest.warns` or carry a scoped
`pytest.mark.filterwarnings`, not leak it into the summary.

Markers: `performance` (deselected by default, `pytest -m performance`) and
`requires_perf_backend` (needs the compiled cython extension). CI runs the
suite with `pytest -n auto` (pytest-xdist) on Python 3.11/3.13/3.14.
