import os
import sys

sys.path.insert(0, os.path.abspath('../..'))

import triplets

# -- Project information -------------------------------------------------------

project = 'triplets'
copyright = '2025, Kristjan Vilgo'
author = 'Kristjan Vilgo'
release = triplets.__version__

# -- General configuration -----------------------------------------------------

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.githubpages',
    'myst_parser',
]

source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

templates_path = ['_templates']
exclude_patterns = []

# -- Napoleon (NumPy-style docstrings) ----------------------------------------

napoleon_google_docstring = False
napoleon_numpy_docstring = True

# -- Autodoc -------------------------------------------------------------------

autodoc_member_order = 'bysource'
autodoc_default_options = {
    'members': True,
    'undoc-members': False,
    'show-inheritance': True,
}

# -- HTML output (furo theme) -------------------------------------------------

html_theme = 'furo'
html_static_path = ['_static']

html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}

# -- MyST (Markdown support) --------------------------------------------------

myst_heading_anchors = 3
