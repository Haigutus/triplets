# Building

## Local Development Build

Build the cython extension for local development:

```shell
# Option 1: pixi (recommended, manages C++ toolchain)
pixi install -e build
pixi run build-cython-pugixml-arrow

# Option 2: manual (requires Cython, pyarrow, numpy, C++ compiler)
python setup_cython_parser.py build_ext --inplace
```

Verify:
```python
import triplets
engine_name, _ = triplets.parser.get_engine("auto")
print(engine_name)  # "cython_pugixml_arrow" if built
```

## Local Wheel Build

Build a wheel (includes cython extension if build deps are available):

```shell
python setup.py bdist_wheel --dist-dir dist/
```

The wheel will have a `linux_x86_64` (or platform-specific) tag. This is fine for local testing but PyPI requires `manylinux` tags for Linux wheels.

## Testing Wheel Repair Locally

### Linux (auditwheel)

PyPI rejects `linux_*` tags — wheels must have `manylinux_*` tags. `auditwheel` applies these tags and optionally bundles shared libraries.

```shell
pip install auditwheel patchelf

# Build the wheel
python setup.py bdist_wheel --dist-dir dist/

# Inspect dependencies
auditwheel show dist/triplets-*.whl

# Repair (apply manylinux tag, exclude arrow libs provided by pyarrow at runtime)
LD_LIBRARY_PATH=$(python -c 'import pyarrow; print(pyarrow.get_library_dirs()[0])') \
  auditwheel repair -w dist/repaired dist/triplets-*.whl \
  --exclude 'libarrow_python.so*' --exclude 'libarrow.so*'

# Check the result
ls dist/repaired/  # should show manylinux_*_x86_64.whl
```

### macOS (delocate)

macOS wheels with `macosx_*` tags are accepted by PyPI without repair. Delocate is skipped in CI.

### Windows (delvewheel)

Windows wheels with `win_amd64` tags are accepted by PyPI. `delvewheel` runs in CI to handle DLL dependencies, excluding arrow DLLs:

```shell
delvewheel repair -w dist/repaired dist/triplets-*.whl \
  --no-dll arrow_python.dll --no-dll arrow.dll
```

Cannot test locally on Linux — Windows builds are tested via CI only.

## Local cibuildwheel (Full CI Simulation)

Test the full cibuildwheel pipeline locally (requires Docker for Linux builds):

```shell
pip install cibuildwheel

# Build one Python version on Linux (fast test)
CIBW_BUILD="cp313-manylinux_x86_64" \
CIBW_REPAIR_WHEEL_COMMAND_LINUX="LD_LIBRARY_PATH=\$(python -c 'import pyarrow; print(pyarrow.get_library_dirs()[0])') auditwheel repair -w {dest_dir} {wheel} --exclude 'libarrow_python.so*' --exclude 'libarrow.so*'" \
CIBW_TEST_REQUIRES="pytest pyarrow>=14.0" \
CIBW_TEST_COMMAND='pytest {project}/tests/test_parser_backends.py -q --tb=short -k "not realgrid"' \
cibuildwheel --platform linux --output-dir dist/
```

This pulls the manylinux Docker image, builds the wheel inside it, runs auditwheel repair, and runs tests against the installed wheel.

## CI Build Overview

The GitHub Actions workflow (`.github/workflows/build-wheels.yml`) builds wheels using cibuildwheel:

| Platform | Runner | Repair tool | Arrow lib handling |
|----------|--------|-------------|-------------------|
| Linux x86_64 | ubuntu-latest (Docker) | auditwheel | `--exclude 'libarrow*.so*'` |
| Linux aarch64 | ubuntu-latest (QEMU) | auditwheel | `--exclude 'libarrow*.so*'` |
| macOS arm64 | macos-14 | skipped | macosx_* tags accepted by PyPI |
| Windows AMD64 | windows-latest | delvewheel | `--no-dll arrow*.dll` |

CPython 3.11, 3.12, 3.13. Arrow shared libraries are NOT bundled — they're provided by pyarrow at runtime.

## Troubleshooting

### `unsupported platform tag 'linux_aarch64'`
PyPI requires manylinux tags. Run `auditwheel repair` on the wheel (see above).

### `FileNotFoundError: Unable to find library: arrow.dll`
Windows `delvewheel` can't find arrow DLLs. Add `--no-dll arrow.dll --no-dll arrow_python.dll` to exclude them.

### `UnicodeEncodeError: 'charmap' codec can't encode`
Windows cp1252 encoding issue with versioneer. Set `PYTHONUTF8=1` environment variable.

### `Could not find all dependencies` (delocate on macOS)
delocate can't find arrow dylibs. Skip delocate — macOS wheel tags are accepted by PyPI without repair.

### `ModuleNotFoundError: No module named 'pyarrow'` in `CIBW_ENVIRONMENT`
pyarrow isn't installed when `CIBW_ENVIRONMENT` is evaluated. Set `LD_LIBRARY_PATH` inline in `CIBW_REPAIR_WHEEL_COMMAND` instead.

### `ImportError: cannot import name '__version__' from 'triplets._version'`
versioneer generates a different `_version.py` in wheels. Use `get_versions()['version']` in `__init__.py`, not `_version.__version__`.
