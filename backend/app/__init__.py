"""KapraOS backend application package.

The explicit package marker (rather than relying on implicit namespace
packages) keeps the module identity unambiguous for tools like mypy, which
otherwise resolve ``app/models/sale.py`` under two different names and refuse
to check the project at all.
"""