"""Build script for the embedded qlever SPARQL engine extension (optional).

The extension links qlever's official embedding facade (src/libqlever) — the
purpose-built boundary for using the engine without the HTTP server. The
supported build path mirrors the pugixml pattern (pinned vendor submodule +
pixi-provided toolchain): the `qlever` pixi environment carries cmake, ninja,
compilers and the C++ deps (boost, icu, openssl, zstd) pinned from conda-forge:

    git submodule update --init --checkout vendor/qlever   # once (update=none)
    pixi run -e qlever build-qlever-lib     # one-time qlever compile (PIC)
    pixi run -e qlever build-qlever         # this extension

QLEVER_SRC_DIR / QLEVER_BUILD_DIR override the vendor submodule (e.g. to an
external checkout). Without the extension, triplets.sparql simply keeps using
the rdflib engine (auto-detection by import).
"""
import glob
import os

from setuptools import setup, Extension
from Cython.Build import cythonize

import pyarrow

from setup_qlever_lib import resolve_qlever_dirs

QLEVER_SRC, QLEVER_BUILD = resolve_qlever_dirs()

# Arrow wiring (same recipe as setup_cython_parser.py): the wrapper decodes
# query results straight into Arrow buffers, the pyx wraps them zero-copy.
pa_include = pyarrow.get_include()
pa_lib_dirs = pyarrow.get_library_dirs()
try:
    import numpy
    np_include = numpy.get_include()
except Exception:
    np_include = None

lib_dir = os.path.join(QLEVER_BUILD, "lib")
static_libs = sorted(glob.glob(os.path.join(lib_dir, "*.a")))
if not static_libs:
    raise RuntimeError(f"no static libraries in {lib_dir} — build qlever first "
                       "(pixi run -e qlever build-qlever-lib).")

include_dirs = [
    os.path.join("triplets", "sparql"),                # _qlever_wrapper.h
    os.path.join(QLEVER_SRC, "src"),
    os.path.join(QLEVER_BUILD, "_deps", "nlohmann-json-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "abseil-src"),
    os.path.join(QLEVER_BUILD, "_deps", "ctre-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "re2-src"),
    os.path.join(QLEVER_BUILD, "_deps", "s2-src", "src"),
    os.path.join(QLEVER_BUILD, "_deps", "antlr-src", "runtime", "Cpp", "runtime", "src"),
    os.path.join(QLEVER_BUILD, "_deps", "range-v3-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "fsst-src"),
    os.path.join(QLEVER_BUILD, "_deps", "uriparser-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "spatialjoin-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "googletest-src", "googletest", "include"),
    pa_include,
] + ([np_include] if np_include else [])
library_dirs = [lib_dir] + pa_lib_dirs
# NOTE: no jemalloc here on purpose. qlever links it only as a global malloc
# replacement (no jemalloc-API symbols in the archives) — inside a Python
# extension that interposition mixes allocators with the interpreter and
# aborts at shutdown (free(): invalid size). glibc malloc is used instead.
system_libs = [
    "boost_iostreams", "boost_program_options", "boost_url", "boost_container",
    "icuuc", "icui18n",
    "ssl", "crypto",
    "zstd",
    "pthread", "z", "bz2",
]
# --start/end-group: the qlever archives reference each other in both directions
# (engine ↔ index ↔ util ↔ s2), so the linker must iterate them. The shared
# system libs go AFTER the group — with --as-needed (conda LDFLAGS) a shared
# library listed before the objects that reference it would be discarded.
extra_link_args = ["-std=c++20",
                   "-Wl,--start-group", *static_libs, "-Wl,--end-group",
                   *[f"-l{lib}" for lib in system_libs],
                   "-larrow_python", "-larrow",
                   *[f"-Wl,-rpath,{d}" for d in pa_lib_dirs]]

# pixi/conda environment: headers, libraries and a runtime rpath so the built
# extension finds the environment's shared libraries from any interpreter
conda_prefix = os.environ.get("CONDA_PREFIX")
if conda_prefix:
    include_dirs.append(os.path.join(conda_prefix, "include"))
    library_dirs.append(os.path.join(conda_prefix, "lib"))
    extra_link_args.append(f"-Wl,-rpath,{os.path.join(conda_prefix, 'lib')}")

extension = Extension(
    "triplets.sparql._qlever",
    sources=[
        os.path.join("triplets", "sparql", "_qlever.pyx"),
        os.path.join("triplets", "sparql", "_qlever_wrapper.cpp"),
        os.path.join("triplets", "sparql", "_qlever_arrow_parser.cpp"),
    ],
    include_dirs=include_dirs,
    library_dirs=library_dirs,
    language="c++",
    extra_compile_args=["-std=c++20", "-O3", "-fPIC", "-fcoroutines"],
    extra_link_args=extra_link_args,
)

setup(
    name="triplets-qlever",
    packages=[],
    ext_modules=cythonize([extension], language_level=3),
)
