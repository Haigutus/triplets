import os
import sys
from setuptools import setup, find_packages, Extension
import versioneer

# Build cython_pugixml_arrow extension if Cython + pyarrow are available
# (wheels via cibuildwheel, or local dev with pixi/build env)
ext_modules = []

# Use relative paths (setuptools rejects absolute paths in containers)
PUGIXML_SRC = os.path.join("vendor", "pugixml", "src")

if os.path.exists(os.path.join(PUGIXML_SRC, "pugixml.cpp")):
    try:
        from Cython.Build import cythonize
        import pyarrow
        import numpy

        pa_include = pyarrow.get_include()
        pa_lib_dirs = pyarrow.get_library_dirs()
        np_include = numpy.get_include()

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

        # Parser: cython_pugixml_arrow (Arrow → pugixml → triplet DataFrame)
        parser_ext = Extension(
            "triplets.parser.cython_pugixml_arrow",
            sources=[
                "triplets/parser/cython_pugixml_arrow.pyx",
                os.path.join(PUGIXML_SRC, "pugixml.cpp"),
            ],
            include_dirs=[PUGIXML_SRC, pa_include, np_include],
            library_dirs=pa_lib_dirs,
            libraries=libraries,
            language="c++",
            extra_compile_args=extra_compile_args,
            extra_link_args=extra_link_args,
            runtime_library_dirs=runtime_library_dirs,
        )

        extensions = [parser_ext]

        # Export: cimxml_cython_pugixml (Arrow triplets → pugixml DOM → CIM XML)
        cimxml_pyx = "triplets/export/cimxml_cython_pugixml.pyx"
        if os.path.exists(cimxml_pyx):
            export_ext = Extension(
                "triplets.export.cimxml_cython_pugixml",
                sources=[
                    cimxml_pyx,
                    os.path.join(PUGIXML_SRC, "pugixml.cpp"),
                ],
                include_dirs=[PUGIXML_SRC, pa_include, np_include],
                library_dirs=pa_lib_dirs,
                libraries=libraries,
                language="c++",
                extra_compile_args=extra_compile_args,
                extra_link_args=extra_link_args,
                runtime_library_dirs=runtime_library_dirs,
            )
            extensions.append(export_ext)

        ext_modules = cythonize(
            extensions,
            compiler_directives={
                "language_level": "3",
                "boundscheck": False,
                "wraparound": False,
            },
        )
    except ImportError:
        pass  # Cython/pyarrow not available — build pure-python wheel

setup(
    # Most metadata (name, version dynamic, description, license, authors, dependencies,
    # readme) comes from pyproject.toml. We keep setup.py for:
    # - versioneer cmdclass (for sdist etc)
    # - conditional cython ext_modules (complex platform logic + vendor submodule)
    # - package discovery + export_schema data
    cmdclass=versioneer.get_cmdclass(),
    version=versioneer.get_version().split("+")[0],
    packages=find_packages(),
    package_data={
        'triplets.export_schema': ['*.json'],
        'triplets.cgmes_tools': ['static/*'],
    },
    include_package_data=True,
    ext_modules=ext_modules,
    entry_points={
        'console_scripts': [
            'cim-spreadsheet=triplets.cli.cim_spreadsheet:main',
            'cim-diff=triplets.cli.cim_diff:main',
        ],
    },
)
