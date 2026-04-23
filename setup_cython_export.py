"""Build script for Cython export extension modules.

Usage:
    python setup_cython_export.py build_ext --inplace
"""
from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np
import pyarrow
import os

# Path to pugixml source (git submodule at vendor/pugixml, v1.15, MIT license)
PUGIXML_SRC = os.path.join(os.path.dirname(__file__), "vendor", "pugixml", "src")

pa_include = pyarrow.get_include()
pa_lib_dirs = pyarrow.get_library_dirs()

extensions = [
    # Raw C++ string builder (no XML library)
    Extension(
        "triplets.cimxml_export_cython",
        sources=["triplets/cimxml_export_cython.pyx"],
        include_dirs=[np.get_include()],
        language="c++",
        extra_compile_args=["-O3", "-std=c++17"],
    ),
    # pugixml DOM builder (reads numpy object arrays)
    Extension(
        "triplets.cimxml_export_pugixml",
        sources=[
            "triplets/cimxml_export_pugixml.pyx",
            os.path.join(PUGIXML_SRC, "pugixml.cpp"),
        ],
        include_dirs=[PUGIXML_SRC, np.get_include()],
        language="c++",
        extra_compile_args=["-O3", "-std=c++17"],
    ),
    # Arrow → pugixml (zero-copy Arrow string reads)
    Extension(
        "triplets.cimxml_export_arrow_pugixml",
        sources=[
            "triplets/cimxml_export_arrow_pugixml.pyx",
            os.path.join(PUGIXML_SRC, "pugixml.cpp"),
        ],
        include_dirs=[PUGIXML_SRC, pa_include],
        library_dirs=pa_lib_dirs,
        libraries=["arrow_python"],
        language="c++",
        extra_compile_args=["-O3", "-std=c++17"],
        extra_link_args=[
            os.path.join(pa_lib_dirs[0], "libarrow.so.2300"),
        ],
        runtime_library_dirs=pa_lib_dirs,
    ),
]

setup(
    packages=[],
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
        },
    ),
)
