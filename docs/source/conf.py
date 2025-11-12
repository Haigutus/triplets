import os
import sys

sys.path.insert(0, os.path.abspath('../..'))  # Points to the project root

import triplets

# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'triplets'
copyright = '2025, Kristjan Vilgo'
author = 'Kristjan Vilgo'
release = triplets.__version__

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',      # Automatically document modules and functions
    'sphinx.ext.napoleon',     # Support for NumPy-style docstrings
    'sphinx.ext.viewcode',     # Add links to source code
    'sphinx.ext.githubpages',  # Configures export suitable for github pages
    'sphinx_multiversion',
    'myst_parser'
]

source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

templates_path = ['_templates']
exclude_patterns = [
    "setup.py",
    "versioneer.py"
    "modules.rst"
]



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

napoleon_google_docstring = False
napoleon_numpy_docstring = True