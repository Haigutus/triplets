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
html_css_files = ['custom.css']

html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
    "top_of_page_buttons": ["view"],
}

html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
        "version-selector.html",
    ],
}

# Shorter TOC depth for cleaner right sidebar
toc_object_entries_show_parents = "hide"

# -- MyST (Markdown support) --------------------------------------------------

myst_heading_anchors = 3

# Suppress common warnings
suppress_warnings = [
    "myst.header",          # non-consecutive heading levels in README/migration docs
    "ref.duplicate",        # duplicate object descriptions (rdf_parser re-exports)
]
