"""Build script for the embedded qlever SPARQL engine extension (optional).

The extension links qlever's official embedding facade (src/libqlever) — the
purpose-built boundary for using the engine without the HTTP server. The
supported build path is the pixi `qlever` environment, which provides the
whole C++ toolchain and dependencies (boost, icu, openssl, zstd, jemalloc)
pinned from conda-forge:

    git clone --recursive https://github.com/ad-freiburg/qlever ../qlever
    pixi run -e qlever build-qlever-lib     # one-time qlever compile (PIC)
    pixi run -e qlever build-qlever         # this extension

Manual invocation works too — point QLEVER_SRC_DIR / QLEVER_BUILD_DIR at a
qlever checkout compiled with -DCMAKE_POSITION_INDEPENDENT_CODE=ON. Without
the extension, triplets.sparql simply keeps using the rdflib engine
(auto-detection by import).
"""
import glob
import os

from setuptools import setup, Extension
from Cython.Build import cythonize

QLEVER_SRC = os.path.expanduser(os.environ.get("QLEVER_SRC_DIR", os.path.join("..", "qlever")))
QLEVER_BUILD = os.path.expanduser(os.environ.get("QLEVER_BUILD_DIR", os.path.join(QLEVER_SRC, "build-pic")))

if not os.path.exists(os.path.join(QLEVER_SRC, "src", "libqlever", "Qlever.h")):
    raise RuntimeError(
        f"qlever sources not found at {QLEVER_SRC} (need src/libqlever/Qlever.h). "
        "Clone https://github.com/ad-freiburg/qlever and set QLEVER_SRC_DIR.")

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
    os.path.join(QLEVER_BUILD, "_deps", "spatialjoin-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "googletest-src", "googletest", "include"),
]
library_dirs = [lib_dir]
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
                   *[f"-l{lib}" for lib in system_libs]]

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
