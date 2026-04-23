"""Build script for Cython extension modules.

Usage:
    python setup_cython.py build_ext --inplace
"""
from setuptools import setup, Extension
from Cython.Build import cythonize
import os
import pyarrow

# Path to pugixml source (git submodule at vendor/pugixml, v1.15, MIT license)
PUGIXML_SRC = os.path.join(os.path.dirname(__file__), "vendor", "pugixml", "src")

assert os.path.exists(os.path.join(PUGIXML_SRC, "pugixml.cpp")), \
    f"pugixml source not found at {PUGIXML_SRC} — run: git submodule update --init vendor/pugixml"

# PyArrow include/lib paths
pa_include = pyarrow.get_include()
pa_lib_dirs = pyarrow.get_library_dirs()

import lxml
lxml_includes = lxml.get_include()

extensions = [
    # List-based extraction (no Arrow dependency)
    Extension(
        "triplets.rdf_extract_cython",
        sources=[
            "triplets/rdf_extract_cython.pyx",
            os.path.join(PUGIXML_SRC, "pugixml.cpp"),
        ],
        include_dirs=[PUGIXML_SRC],
        language="c++",
        extra_compile_args=["-O3", "-std=c++11"],
    ),
    # lxml + Arrow extraction (libxml2 C API + Arrow StringBuilders)
    Extension(
        "triplets.rdf_extract_lxml_arrow",
        sources=[
            "triplets/rdf_extract_lxml_arrow.pyx",
        ],
        include_dirs=lxml_includes + [pa_include],
        library_dirs=pa_lib_dirs,
        libraries=["arrow_python"],
        language="c++",
        extra_compile_args=["-O3", "-std=c++17"],
        extra_link_args=[
            os.path.join(pa_lib_dirs[0], "libarrow.so.2300"),
            # Link lxml's etree.so to resolve its utf8() symbol
            os.path.join(os.path.dirname(lxml_includes[0]), "etree.cpython-313-x86_64-linux-gnu.so"),
        ],
        runtime_library_dirs=pa_lib_dirs + [os.path.dirname(lxml_includes[0])],
    ),
    # pugixml + Arrow extraction (writes to Arrow StringBuilders)
    Extension(
        "triplets.rdf_extract_cython_arrow",
        sources=[
            "triplets/rdf_extract_cython_arrow.pyx",
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
