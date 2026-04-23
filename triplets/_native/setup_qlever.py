"""Build script for the qlever Cython extension.

Requires:
    - libqlever built from source (cmake)
    - System deps: boost, icu, openssl, zstd, jemalloc
    - Cython

Usage:
    python setup_qlever.py build_ext --inplace

Or set QLEVER_BUILD_DIR to point to the qlever build directory:
    QLEVER_BUILD_DIR=/path/to/qlever/build python setup_qlever.py build_ext --inplace
"""

import os
import glob
from setuptools import setup, Extension
from Cython.Build import cythonize

# Paths
NATIVE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(NATIVE_DIR))
QLEVER_SRC = os.environ.get("QLEVER_SRC_DIR",
    os.path.join(REPO_ROOT, "vendor", "qlever"))
QLEVER_BUILD = os.environ.get("QLEVER_BUILD_DIR",
    os.path.join(QLEVER_SRC, "build"))

# Collect all static libraries from qlever build
lib_dir = os.path.join(QLEVER_BUILD, "lib")
static_libs = glob.glob(os.path.join(lib_dir, "*.a"))

# qlever source include paths
include_dirs = [
    NATIVE_DIR,                          # for qlever_wrapper.h
    os.path.join(QLEVER_SRC, "src"),     # qlever source headers
    os.path.join(QLEVER_BUILD, "_deps", "nlohmann-json-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "abseil-src"),
    os.path.join(QLEVER_BUILD, "_deps", "ctre-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "re2-src"),
    os.path.join(QLEVER_BUILD, "_deps", "s2-src", "src"),
    os.path.join(QLEVER_BUILD, "_deps", "antlr-src", "runtime", "Cpp", "runtime", "src"),
    os.path.join(QLEVER_BUILD, "_deps", "range-v3-src", "include"),
    os.path.join(QLEVER_BUILD, "_deps", "fsst-src"),
    os.path.join(QLEVER_BUILD, "_deps", "spatialjoin-src", "include"),
]

# System libraries that qlever needs
system_libs = [
    "boost_iostreams", "boost_program_options", "boost_url", "boost_container",
    "icuuc", "icui18n",
    "ssl", "crypto",
    "zstd",
    "jemalloc",
    "pthread", "z", "bz2",
]

ext = Extension(
    "triplets._native.qlever_sparql",
    sources=[
        os.path.join(NATIVE_DIR, "qlever_sparql.pyx"),
        os.path.join(NATIVE_DIR, "qlever_wrapper.cpp"),
    ],
    include_dirs=include_dirs,
    library_dirs=[lib_dir],
    extra_objects=static_libs,  # link all qlever static libs
    libraries=system_libs,
    language="c++",
    extra_compile_args=["-std=c++20", "-O3", "-fcoroutines"],
    extra_link_args=["-std=c++20"],
)

setup(
    name="triplets-qlever",
    ext_modules=cythonize([ext], language_level=3),
)
