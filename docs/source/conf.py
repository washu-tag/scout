import pathlib
import sys

sys.path.insert(0, pathlib.Path(__file__).parents[2].resolve().as_posix())

project = "Scout"
copyright = "2026, Washington University in St. Louis"
author = "TAG@WashU"

release = "0.1"
version = "0.1"

extensions = [
    "sphinx.ext.duration",
    "sphinx.ext.doctest",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinxcontrib.openapi",
    "myst_parser",
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "sphinx": ("https://www.sphinx-doc.org/en/master/", None),
}
intersphinx_disabled_domains = ["std"]

templates_path = ["_templates"]

html_title = "Scout Documentation"
html_permalinks_icon = "<span>#</span>"
html_theme = "sphinxawesome_theme"

epub_show_urls = "footnote"
