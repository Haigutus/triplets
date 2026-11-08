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

The `dev` extra pulls in the test tooling and most engine dependencies (pytest,
pytest-xdist, pytest-benchmark, polars, pyarrow, networkx, openpyxl, rdflib,
pyshacl, pyoxigraph, oxrdflib, and `jsonschema>=4.0` for the SARIF
schema-conformance test against `tests/data/sarif-schema-2.1.0.json`).
`duckdb` comes from its own extra. CI installs `dev` plus the feature extras:

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

For the compiled cython engine tests, build the extension first — see
[Building](building.md) (`pixi run build-cython-pugixml-arrow`).

## Test Files

Grouped by family; each module docstring describes its tests.

| Family | Files | Needs external data |
|---|---|---|
| Parser | `test_parser.py` (incl. `pandas.read_RDF` registration), `test_parser_nquads.py`, `test_path_handling.py`, `test_compiled_modules.py` (guard, see below) | committed `tests/data`; RealGrid when present |
| Import / tools / CGMES tools | `test_import.py`, `test_tools.py`, `test_cgmes_tools.py`, `test_duckdb_table.py`, `test_api_surface.py` | relicapgrid |
| Cross-engine parity (`test_parity_*.py`, shared `tests/_parity.py`) | `test_parity_parser.py`, `test_parity_tools.py`, `test_parity_export.py`, `test_parity_cgmes_tools.py`, `test_parity_validation.py`, `test_parity_shacl_matrix.py`, `test_parity_shacl_raw.py` | relicapgrid; RealGrid LFS + CGMES-AP + APL cgmes-v2-4 for the `-m performance` parts |
| Engine registry / flavors / caches | `test_engines_api.py`, `test_engine_state.py`, `test_engine_detect.py` | none |
| IRI contract | `test_iri.py` | none |
| Export | `test_export_comment.py`, `test_export_profiles.py`, `test_export_undefined.py`, `test_roundtrip_nc.py`, `test_cim_spreadsheet.py` | relicapgrid |
| SPARQL | `test_sparql.py` (rdflib), `test_sparql_oxigraph.py`, `test_sparql_qlever.py`, `test_sparql_qlever_ingest.py`, `test_rdflib_oxigraph_store.py` | relicapgrid; qlever files skip unless the extension is built |
| Validation | `test_validation.py`, `test_shacl_ir.py`, `test_shacl_engines.py`, `test_shacl_report.py`, `test_schema_validation.py`, `test_schema_references.py`, `test_schema_scope.py`, `test_cgmes_validation.py`, `test_validation_context.py`, `test_locations.py`, `test_sarif.py` | relicapgrid, CGMES-AP; APL cgmes-v2-4 for the CGMES 2.4 shapes in `test_shacl_ir.py` |
| RDFS tools | `test_rdfs_tools.py` | `rdfs/` snapshots in the repo |
| Benchmarks (`-m performance`) | `test_benchmarks_realgrid.py` | RealGrid LFS |

## Test Data

**Committed** (always available):
- `tests/data/minimal_cim.xml` — 5 RDF objects, covers Substation, VoltageLevel, BaseVoltage, ConnectivityNode (with `rdf:nodeID`)
- `tests/data/sarif-schema-2.1.0.json` — official SARIF 2.1.0 schema for the exporter conformance test

**Submodules** (`git submodule update --init test_data/relicapgrid test_data/CGMES-AP test_data/application-profiles-library-cgmes-v2-4`):
- `test_data/relicapgrid/` — [ReliCapGrid](https://github.com/entsoe/relicapgrid) release `cgmes-3.0_ncp-2.5_tc-2.0`: CGMES 3.0 grids and NCP 2.5 instances for import and tools tests
- `test_data/CGMES-AP/` — [CGMES-AP](https://github.com/entsoe/CGMES-AP): CGMES 3.0 SHACL shapes for the validation tests
- `test_data/application-profiles-library-cgmes-v2-4/` — [application-profiles-library](https://github.com/entsoe/application-profiles-library) branch `cgmes-v2-4`: CGMES 2.4 Enhanced SHACL shapes (RealGrid parity tests)

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

## Local flow

Tests run in the uv venv, never inside pixi; pixi only supplies the C++
toolchain for the optional compiled extensions:

```shell
uv sync --all-extras
uv run pytest -n auto -q

# optional compiled extensions (built against the venv's pyarrow)
pixi run build-cython-pugixml-arrow
pixi run -e qlever build-qlever       # needs the qlever static libs, see docs/building.md
```

The same `uv run pytest` then picks the extensions up: the compiled-engine tests
stop skipping and `auto` engine selection prefers them.

## CI Workflows

Two GitHub Actions workflows run automatically.

### `tests.yml` — unit tests

Runs on push to `main`, on pull requests, and on manual dispatch. The `pytest`
matrix runs the pure-Python engines; the `pytest-compiled` job (CPython 3.13)
builds the cython extensions and runs the suite with `TRIPLETS_REQUIRE_COMPILED=1`.
The qlever extension has no CI.

- **Matrix:** CPython 3.11, 3.13, 3.14 on `ubuntu-latest`
- **Checkout:** LFS enabled (RealGrid zips); inits the three test-data submodules
  `test_data/relicapgrid`, `test_data/CGMES-AP`,
  `test_data/application-profiles-library-cgmes-v2-4` (never `vendor/qlever`);
  `pytest-compiled` also inits `vendor/pugixml`
- **Install:** `pip install -e .[dev,validation,oxigraph,duckdb,excel,networkx]`
- **Run:** `pytest -n auto -q` (`pytest-compiled`: `pytest -n auto -q -rs`)

### `build-wheels.yml` — wheels + publish

| Trigger | What happens |
|---------|-------------|
| **GitHub Release** (pre-release or final) | Build sdist + wheels, publish to PyPI |
| **Pull Request** touching the build | Build sdist + wheels only (no publish) |
| **Manual** (workflow_dispatch) | Build sdist + wheels only (no publish) |

Publishing uses PyPI **trusted publishing** (OIDC, no API tokens) via the `pypi`
environment. To enable it: on [PyPI](https://pypi.org/manage/project/triplets/settings/publishing/)
add a publisher for GitHub repo `Haigutus/triplets`, workflow `build-wheels.yml`,
environment `pypi`.

On pull requests the workflow runs only when the diff touches a path in its
`paths:` filter: the workflow itself, `setup.py`, `setup.cfg`, `pyproject.toml`,
`MANIFEST.in`, versioneer, `.gitmodules`, the `vendor/pugixml` submodule, cython/C++
sources under `triplets/` (`*.pyx`, `*.pxd`, `*.h`, `*.hpp`, `*.cpp`) and the two test
files the wheel job runs. Pure-Python PRs skip it; `pytest-compiled` in `tests.yml`
still builds the extensions on Linux. Manual dispatch builds wheels for any branch.

Each wheel is tested during the build with `TRIPLETS_REQUIRE_COMPILED=1`, so a
wheel missing its compiled extensions fails rather than skips.

#### Build matrix

Wheels are built only for the active runner targets:

| Platform | Runner | Architecture |
|----------|--------|-------------|
| Linux (manylinux) | `ubuntu-latest` | x86_64 |
| Linux (manylinux) | `ubuntu-24.04-arm` | aarch64 |
| macOS (Apple Silicon) | `macos-15` | arm64 |
| Windows | `windows-latest` | AMD64 |

CPython 3.11–3.14 (`requires-python >=3.11`). Each wheel includes the compiled
`cython_pugixml_arrow` extension (usable when pyarrow is also installed). macOS
x86_64 (Intel, macos-13) is commented out in the workflow; Intel-Mac users can
install from the sdist.

## Publishing a Release

Tags use bare numbers (matching existing releases like `0.0.17`).

1. Tag the release:
   ```shell
   git tag 0.3.0rc1      # pre-release
   git push origin 0.3.0rc1
   ```
2. Create a GitHub Release from the tag (mark pre-releases as such).
3. `build-wheels.yml` builds the sdist + wheels and publishes to PyPI.

Install:

```shell
pip install --pre triplets       # latest pre-release
pip install triplets==0.3.0rc1   # pin an exact pre-release
pip install triplets             # latest stable
```

PyPI treats `a` (alpha), `b` (beta), and `rc` versions all as pre-releases —
they are installed only when `--pre` is passed or a specific version is pinned.

| Tag | PyPI version | pip install |
|-----|-------------|-------------|
| `0.3.0a1` | `0.3.0a1` (pre-release) | `pip install --pre triplets` |
| `0.3.0rc1` | `0.3.0rc1` (pre-release) | `pip install --pre triplets` |
| `0.3.0` | `0.3.0` (stable) | `pip install triplets` |

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
review even though Python hides them by default) and ignores two rdflib 7.6.0
self-deprecations (rdflib's own internals call its deprecated
`Dataset.default_context`/`Dataset.identifier`), each filtered for the `rdflib`
and the `pyshacl` module; drop the ignores when rdflib migrates. A test that legitimately exercises a deprecated triplets alias
should assert it with `pytest.warns` or carry a scoped
`pytest.mark.filterwarnings`, not leak it into the summary.

Markers: `performance` (deselected by default, `pytest -m performance`) and
`requires_perf_backend` (needs the compiled cython extension). CI runs the
suite with `pytest -n auto` (pytest-xdist) on Python 3.11/3.13/3.14.
