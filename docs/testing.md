# Testing

## Quick Start

```shell
# run all tests (excluding benchmarks and tests needing test_data submodule)
pytest tests/ -v -k "not realgrid and not benchmark"

# run only parser tests
pytest tests/test_parser.py tests/test_parser_backends.py -v
```

## Requirements

```shell
pip install -e ".[dev]"
```

For cython engine tests, build the extension first:
```shell
python setup_cython_parser.py build_ext --inplace
```

## Test Files

| File | What it tests | Needs test_data? |
|------|--------------|------------------|
| `test_parser.py` | `parse()`, `clean_ID`, `find_all_xml`, nodeID support, categorical encoding, return types | No (uses `tests/data/minimal_cim.xml`) |
| `test_parser_backends.py` | Engine parity, `pandas.read_RDF` registration, all three engines produce identical output | No |
| `test_import.py` | Loading NC and CGMES files, column structure, metadata (Distribution, NamespaceMap) | Yes |
| `test_type_tableview.py` | `type_tableview()` on pandas and polars, pivot correctness | Partially (RealGrid tests) |
| `test_benchmarks_realgrid.py` | Performance benchmarks for parsing and type_tableview across all engines | Yes |

## Test Data

**Committed** (always available):
- `tests/data/minimal_cim.xml` — 5 RDF objects, covers Substation, VoltageLevel, BaseVoltage, ConnectivityNode (with `rdf:nodeID`)

**Submodule** (needs `git submodule update --init`):
- `test_data/relicapgrid/` — NC and CGMES files for import tests
- `test_data/TestConfigurations_packageCASv2.0/RealGrid/` — full CGMES dataset (~82 MB, 1.14M rows) for benchmarks

Tests that need submodule data are automatically skipped when the files are not present.

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

# parity test (compares all available engines)
pytest tests/test_parser_backends.py::TestParity -v
```

## Benchmarks

Benchmarks use `pytest-benchmark` and require the RealGrid test data:

```shell
# parse benchmarks (all engines, pandas + polars output)
pytest tests/test_benchmarks_realgrid.py --benchmark-only -k "parse" -v

# type_tableview benchmarks
pytest tests/test_benchmarks_realgrid.py --benchmark-only -k "tableview" -v

# save results to JSON
pytest tests/test_benchmarks_realgrid.py --benchmark-only \
  --benchmark-json=documents/parsers_performance.json -k "parse"
```

## pixi Tasks

If using pixi:

```shell
pixi run test                         # all tests
pixi run test-parser                  # parser tests only
pixi run build-cython-pugixml-arrow   # build cython extension
```

## CI / Release Workflow

The GitHub Actions workflow (`.github/workflows/build-wheels.yml`) runs automatically:

| Trigger | What happens |
|---------|-------------|
| **GitHub Release** (RC or final) | Build wheels for all platforms + publish to PyPI |
| **Pull Request** | Build wheels only (no publish, catches build failures early) |
| **Manual** (workflow_dispatch) | Build wheels only (no publish) |

### Publishing a Release Candidate

1. Tag with an RC version (bare number, matching existing pattern):
   ```shell
   git tag 0.1.0rc1
   git push origin 0.1.0rc1
   ```

2. Create a GitHub Release from the tag (mark as pre-release)

3. The workflow builds wheels for all platforms and publishes to PyPI

4. Users install the RC:
   ```shell
   pip install --pre triplets
   # or pin the exact RC:
   pip install triplets==0.1.0rc1
   ```

### Publishing a Final Release

```shell
git tag 0.1.0
git push origin 0.1.0
```

Create a GitHub Release from the tag. The workflow builds and publishes to PyPI.

```shell
pip install triplets
```

### Verifying the Cython Engine in a Wheel

After installing a wheel (RC or release), verify the compiled extension is included:

```python
import triplets

# Check which engine auto-detection picks
engine_name, _ = triplets.parser.get_engine("auto")
print(engine_name)  # "cython_pugixml_arrow" if the wheel has it

# Or import directly
from triplets.parser import cython_pugixml_arrow
print("cython engine available")
```

### Version Numbering

Tags use bare numbers (matching existing releases like `0.0.17`):

| Tag | PyPI version | pip install |
|-----|-------------|-------------|
| `0.1.0rc1` | `0.1.0rc1` (pre-release) | `pip install --pre triplets` |
| `0.1.0rc2` | `0.1.0rc2` (pre-release) | `pip install --pre triplets` |
| `0.1.0` | `0.1.0` (stable) | `pip install triplets` |

PyPI treats `rc` versions as pre-releases — they are only installed when `--pre` is passed or a specific version is pinned.

### Setup for Trusted Publishing

The workflow uses PyPI trusted publishing (no API tokens needed). To enable it:

1. Go to [PyPI](https://pypi.org/manage/project/triplets/settings/publishing/) -> Publishing -> Add a new publisher
2. Set: GitHub repository `Haigutus/triplets`, workflow `build-wheels.yml`, environment `pypi`

The legacy `python-publish.yml` has been removed; all releases (including pure-python sdists) now use `build-wheels.yml` with trusted publishing and cibuildwheel.

### Build Matrix

Wheels are built for:

| Platform | Architecture |
|----------|-------------|
| Linux (manylinux) | x86_64, aarch64 |
| macOS | x86_64 (Intel), arm64 (Apple Silicon) |
| Windows | AMD64 |

CPython 3.11, 3.12, 3.13 (requires-python >=3.11). Each wheel includes the compiled `cython_pugixml_arrow` extension (usable when pyarrow also installed).
