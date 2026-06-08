"""Build script for the cython_pugixml_arrow parser extension.

Usage (local dev):
    python setup_cython_parser.py build_ext --inplace

Also invoked by cibuildwheel for cross-platform wheel builds.
"""

from setuptools import setup, Extension
from Cython.Build import cythonize
import os
import sys
import pyarrow
import numpy

PUGIXML_SRC = os.path.join("vendor", "pugixml", "src")

if not os.path.exists(os.path.join(PUGIXML_SRC, "pugixml.cpp")):
    raise RuntimeError(
        f"pugixml source not found at {PUGIXML_SRC}. "
        "Run: git submodule update --init vendor/pugixml"
    )

pa_include = pyarrow.get_include()
pa_lib_dirs = pyarrow.get_library_dirs()
try:
    np_include = numpy.get_include()
except Exception:
    np_include = None

# Platform-specific compiler flags + libraries (must match setup.py exactly)
if sys.platform == "win32":
    extra_compile_args = ["/O2", "/std:c++20"]
    extra_link_args = []
    runtime_library_dirs = []
    libraries = ["arrow_python", "arrow"]
else:
    extra_compile_args = ["-O3", "-std=c++20", "-fPIC"]
    extra_link_args = ["-Wl,-rpath," + d for d in pa_lib_dirs]
    runtime_library_dirs = pa_lib_dirs
    libraries = ["arrow_python"]

ext = Extension(
    "triplets.parser.cython_pugixml_arrow",
    sources=[
        "triplets/parser/cython_pugixml_arrow.pyx",
        os.path.join(PUGIXML_SRC, "pugixml.cpp"),
    ],
    include_dirs=[PUGIXML_SRC, pa_include] + ([np_include] if np_include else []),
    library_dirs=pa_lib_dirs,
    libraries=libraries,
    language="c++",
    extra_compile_args=extra_compile_args,
    extra_link_args=extra_link_args,
    runtime_library_dirs=runtime_library_dirs,
)

setup(
    name="triplets-parser-cython-pugixml",
    packages=[],
    ext_modules=cythonize(
        [ext],
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
        },
    ),
)
