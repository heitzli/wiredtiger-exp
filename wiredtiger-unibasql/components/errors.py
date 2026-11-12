"""Wire-mappable exception types shared by every component (the binary errors union only
carries the branch index, so the concrete type travels in-band in the ``kind`` field).

Exceptions carry ``source`` provenance; ``ERROR_REGISTRY`` maps ``kind`` back to the class
(``encode_error_payload`` / ``error_from_datum``).
"""

from __future__ import annotations

from typing import Any


class ComponentError(Exception):
    """Base class of every error that can cross a component boundary."""

    type_name: str = "ComponentError"

    def __init__(self, message: str, source: str | None = None) -> None:
        # Name of the component the error originated in (in-band provenance).
        self.source = source
        self.message = message
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{self.type_name} ({self.source}): {self.message}"

    def __str__(self) -> str:
        return f"{self.type_name} ({self.source}): {self.message}"


class WiredTigerError(ComponentError):
    """Raised by the storage manager when a WiredTiger call fails."""

    type_name = "WiredTigerError"


class QueryProcessorError(ComponentError):
    """Raised by the query processor for unexpected internal failures."""

    type_name = "QueryProcessorError"


class UnibaSQLSyntaxError(ComponentError):
    """Raised by the query processor when a query does not parse."""

    type_name = "UnibaSQLSyntaxError"


class RemoteError(ComponentError):
    """Raised locally for wire errors that are not declared by a protocol."""

    type_name = "RemoteError"


class CatalogError(ComponentError):
    """Raised by the catalog component (missing/duplicate table, bad entry)."""

    type_name = "CatalogError"


ERROR_REGISTRY: dict[str, type[ComponentError]] = {
    cls.type_name: cls for cls in (WiredTigerError, QueryProcessorError, UnibaSQLSyntaxError, CatalogError)
}


def error_record(exc: Exception, source: str | None = None) -> dict[str, str]:
    """Build the datum for the declared errors union of the failing RPC."""
    if isinstance(exc, ComponentError):
        return {
            "error": exc.message,
            "component": exc.source or source or "",
            "kind": exc.type_name,
        }
    # Undeclared exception: report through the string branch of the errors union.
    return {"error": str(exc), "component": source or "", "kind": "RemoteError"}


def error_from_datum(datum: Any) -> ComponentError:
    """Map a decoded errors-union datum back to a ComponentError instance."""
    if isinstance(datum, str):  # string branch: "system" error, undeclared payload
        return RemoteError(datum)
    kind = str(datum.get("kind") or "")
    cls = ERROR_REGISTRY.get(kind, RemoteError)
    return cls(str(datum.get("error", "")), datum.get("component") or None)
