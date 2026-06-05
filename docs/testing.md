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
| **Pull Request** | Build wheels for all platforms + publish RC to [TestPyPI](https://test.pypi.org/project/triplets/) |
| **Tag push** (`v*`) | Build wheels + publish release to [PyPI](https://pypi.org/project/triplets/) |
| **Manual** (workflow_dispatch) | Build wheels only (no publish) |

### Installing an RC from a PR

Every PR automatically publishes a release candidate to TestPyPI. To install it:

```shell
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ triplets
```

The `--extra-index-url` is needed so that dependencies (pandas, lxml, etc.) are still fetched from real PyPI.

### Publishing a Release

```shell
git tag v0.1.0
git push origin v0.1.0
```

This triggers the full build matrix and publishes to PyPI.

### Version Numbering

Versioneer derives the version from git tags:

- Tagged commit `v0.1.0` -> version `0.1.0`
- 3 commits after tag -> version `0.1.0+3.gabcdef1`
- No tag -> version `0+untagged.N.gabcdef1`

TestPyPI accepts these PEP 440 dev versions, so every PR build gets a unique version.

### Setup for Trusted Publishing

The workflow uses PyPI trusted publishing (no API tokens needed). To enable it:

1. Go to [PyPI](https://pypi.org/manage/project/triplets/settings/publishing/) -> Publishing -> Add a new publisher
2. Set: GitHub repository `Haigutus/triplets`, workflow `build-wheels.yml`, environment `(leave blank)`
3. Repeat for [TestPyPI](https://test.pypi.org/manage/project/triplets/settings/publishing/)

### Build Matrix

Wheels are built for:

| Platform | Architecture |
|----------|-------------|
| Linux (manylinux) | x86_64, aarch64 |
| macOS | x86_64 (Intel), arm64 (Apple Silicon) |
| Windows | AMD64 |

CPython 3.10, 3.11, 3.12, 3.13. Each wheel includes the compiled `cython_pugixml_arrow` extension.
