"""
Thin module that holds the single shared SQLAlchemy mapper registry.

WHY THIS EXISTS
---------------
All application entities (AccountEntity, ManagerEntity, CompInfoEntity, …)
must share ONE sqlalchemy.orm.registry() instance so that cross-package
foreign keys and joined-table inheritance can be resolved at mapper
configuration time.

Previously this was defined inside funlab.core.appbase, which also imports
Flask, flask_login, flask_caching, etc.  Any entity module that imported
`APP_ENTITIES_REGISTRY` therefore dragged in the entire 500+ module Flask
stack — even during unit tests, import-time checks, or early startup.

By isolating the registry here (only sqlalchemy.orm as a dependency) each
entity module has a clean, lightweight import path:

    from funlab.core._entity_registry import APP_ENTITIES_REGISTRY

funlab.core.appbase still re-exports APP_ENTITIES_REGISTRY for backward
compatibility, but its import no longer creates the registry object.

USAGE
-----
Entity modules:
    from funlab.core._entity_registry import APP_ENTITIES_REGISTRY as entities_registry

App bootstrap (appbase.py / scripts):
    # Either of these works — they reference the same singleton object:
    from funlab.core._entity_registry import APP_ENTITIES_REGISTRY
    from funlab.core.appbase import APP_ENTITIES_REGISTRY   # backward compat
"""

from sqlalchemy.orm import registry as _registry

# Single application-wide mapper registry.
# Created once when this module is first imported; all subsequent imports
# receive the same cached instance (standard Python module semantics).
APP_ENTITIES_REGISTRY: _registry = _registry()
