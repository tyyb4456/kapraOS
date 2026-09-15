"""Domain services.

Business operations that span models live here (per `db_arch.md` section 3),
so route handlers and future cross-domain workflows - e.g. a sale that
deducts inventory - call a focused service instead of manipulating tables
directly.

Import submodules explicitly (e.g. `from app.services import inventory`) to
keep this package init free of heavy imports.
"""