"""API request/response schemas.

Kept separate from `app.models` (persistence) and `app.services` (domain) so
the HTTP contract can evolve without either of them importing Pydantic. The
explicit package marker mirrors `app/__init__.py`: it keeps module identity
unambiguous for mypy.
"""