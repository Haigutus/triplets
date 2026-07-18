# Building

> **Single source of truth:** edit this file only. The published docs include it
> from `docs/source/guides/building.md` via MyST `{include}`.

## Local Development Build

The cython extension is compiled from the vendored pugixml source, so fetch that
submodule first (`setup_cython_parser.py` hard-errors without it):

```shell
git submodule update --init vendor/pugixml
```

Then build the cython extension for local development:

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
CIBW_TEST_COMMAND='pytest {project}/tests/test_parser.py {project}/tests/test_compiled_modules.py -q --tb=short -k "not realgrid"' \
cibuildwheel --platform linux --output-dir dist/
```

This pulls the manylinux Docker image, builds the wheel inside it, runs auditwheel repair, and runs tests against the installed wheel.

## CI Build Overview

The GitHub Actions workflow (`.github/workflows/build-wheels.yml`) builds wheels using cibuildwheel:

| Platform | Runner | Repair tool | Arrow lib handling |
|----------|--------|-------------|-------------------|
| Linux x86_64 | ubuntu-latest (Docker) | auditwheel | `--exclude 'libarrow*.so*'` |
| macOS arm64 | macos-14 | skipped | macosx_* tags accepted by PyPI |
| Windows AMD64 | windows-latest | delvewheel | `--no-dll arrow*.dll` |

Only these three are active in `build-wheels.yml`. Linux aarch64 (ubuntu QEMU)
and macOS x86_64 (macos-13) are commented out in the matrix — aarch64 because the
QEMU-emulated build dominates release time; re-enable when arm64 runners are available.

CPython 3.11–3.14. Arrow shared libraries are NOT bundled — they're provided by pyarrow at runtime.

## qlever SPARQL engine (optional, source-only)

The embedded qlever engine (`triplets.sparql._qlever`) ships in **no wheel** — it
is a local source build only; the pip-installable performance path is the
`oxigraph` extra. There is no CI for it — verification is the local test task
below, run before/after any qlever bump.

**How the source is pinned.** `vendor/qlever` is a git submodule pointing at the
fork `https://github.com/Haigutus/qlever.git`, branch `libqlever-parser-injection`
(upstream master + the libqlever patch adding programmatic index building and
SPARQL-protocol dataset clauses). The exact commit is the superproject's recorded
gitlink; `.gitmodules` carries the fork URL and branch. Re-pin to
`ad-freiburg/qlever` once the upstream PR merges.

**Reproduce the build** (fetch the pinned submodule first; the pixi `qlever`
environment pins the whole C++ toolchain — compilers, cmake, boost, icu, openssl,
zstd — via pixi.lock):

```bash
git submodule update --init vendor/qlever      # checkout the pinned fork commit
pixi run -e qlever build-qlever-lib            # one-time qlever static-lib compile (long)
pixi run -e qlever build-qlever                # build triplets.sparql._qlever
pixi run -e qlever test-qlever                 # parity tests vs the rdflib engine
```

An external checkout can override the source location: resolution order is
`$QLEVER_SRC_DIR` → `vendor/qlever` → `../qlever` (see `setup_qlever_lib.py`).
Without the extension nothing changes — the SPARQL engine registry falls back
to oxigraph, then rdflib (see docs/sparql.md).

## Troubleshooting

### `unsupported platform tag 'linux_*'`
PyPI requires manylinux tags. Run `auditwheel repair` on the wheel (see above).

### `FileNotFoundError: Unable to find library: arrow.dll`
Windows `delvewheel` can't find arrow DLLs. Add `--no-dll arrow.dll --no-dll arrow_python.dll` to exclude them.

### Other known gotchas (already handled in CI)
Windows versioneer cp1252 errors — set `PYTHONUTF8=1`. macOS delocate can't find
arrow dylibs — skip it (macosx_* tags need no repair). `pyarrow` not yet installed
when `CIBW_ENVIRONMENT` is evaluated — set `LD_LIBRARY_PATH` inline in the repair command.
