import json
from pathlib import Path

from avro.ipc import Responder
from avro.protocol import Protocol

from ..component import Component
from ..errors import CatalogError


class CatalogResponder(Responder):
    """Serves the catalog protocol; NOTE requests arrive with FLAT parameters (request["table"], ...)
    since the catalog RPCs declare bare params, not a request record like the SM protocol does.
    """

    def __init__(self, local_protocol, catalog: CatalogComponent) -> None:
        super().__init__(local_protocol)
        self.catalog = catalog

    def invoke(self, local_message, request):  # type: ignore
        """Dispatch catalog RPCs (get/set/exists/entries/delete/snapshot lifecycle) to their operations. TODO: reconsider whether this many messages are needed in the catalog protocol."""
        message = local_message.name

        if message == "get":
            return self.catalog.get(table=request["table"], key=request["key"])

        if message == "set":
            return self.catalog.set(table=request["table"], key=request["key"], value=request["value"])

        if message == "exists":
            return self.catalog.exists(name=request["name"])

        if message == "entries":
            return self.catalog.entries(name=request["name"])

        if message == "delete":
            return self.catalog.delete(name=request["name"])

        if message == "create_snapshot":
            return self.catalog.create_snapshot(tx_id=request["tx_id"])

        if message == "snapshot_entries":
            return self.catalog.snapshot_entries(tx_id=request["tx_id"], name=request["name"])

        if message == "release_snapshot":
            return self.catalog.release_snapshot(tx_id=request["tx_id"])


class CatalogComponent(Component):
    """Very simple in-memory catalog: whole-catalog dict plus per-transaction snapshot copies, dumped to JSON after mutations."""

    def __init__(
        self,
        name: str,
        broker_protocol_path: Path,
        protocol_path: Path,
        broker_socket: str,
        component_socket: str,
        dump_path: str = "catalog_dump.json",
    ) -> None:
        """Wire the component and initialize an empty in-memory catalog plus per-tx snapshot store (always starts empty; last writer wins)."""
        self.dump_path = dump_path
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)
        self.catalog: dict[str, dict[str, dict[str, str]]] = {}
        self.snapshots: dict[str, dict[str, dict[str, dict[str, str]]]] = {}

    # --- in-memory operations (shared by responder invocation and direct in-process use) ---------------

    def get(self, table: str, key: str) -> dict[str, dict[str, str]]:
        """Key lookup: returns the whole value stored under catalog[table][key] (e.g. a table's
        "schema" map: {col: {attr: val, ...}}); {} when unknown."""
        if not table or not key:
            raise CatalogError("Empty table or key")
        # NOTE missing table/key yields an empty map (missing-as-empty, mirrors bypass semantics).
        return {k: dict(v) for k, v in self.catalog.get(table, {}).get(key, {}).items()}

    def set(self, table: str, key: str, value: dict) -> dict[str, str]:
        """Sets catalog[table][key] = value wholesale (one write per bucket entry, e.g. a table's
        whole "schema" or an index's "index" meta); overwrites silently so repeated writes in debug
        replays don't raise here (the analyzer owns such checks)."""
        if not table or not key:
            raise CatalogError("Empty table or key")
        if not value:
            raise CatalogError("Empty value map")
        self.catalog.setdefault(table, {})[key] = {
            str(subkey): {str(attr): str(val) for attr, val in members.items()} for subkey, members in value.items()
        }
        self._dump()
        return {}

    def exists(self, name: str) -> bool:
        """Top-level name check: True when `name` has a bucket, i.e. is a known table or index name.
        NOTE tables and index names share this namespace, mirroring the analyzer's/mock's conflation
        of the two for collision detection."""
        if not name:
            raise CatalogError("Empty name")
        return name in self.catalog

    def entries(self, name: str) -> dict[str, dict[str, dict[str, str]]]:
        """Whole-bucket dump: catalog[name] as {subkey: {col: {attr: val, ...}}} (three map levels —
        mirrors the wire type map<map<map<string>>>); {} for unknown names."""
        if not name:
            raise CatalogError("Empty name")
        return {key: dict(members) for key, members in self.catalog.get(name, {}).items()}

    def delete(self, name: str) -> dict[str, str]:
        """Removes the bucket catalog[name] wholesale; idempotent (unknown names are fine, mirrors
        the SM's unchecked session.drop)."""
        if not name:
            raise CatalogError("Empty name")
        self.catalog.pop(name, None)
        self._dump()
        return {}

    # --- per-transaction snapshots ---------------------------------------------------------------------

    def create_snapshot(self, tx_id: str) -> dict[str, str]:
        """Deep-copies the current catalog under snapshots[tx_id]; overwrites silently on repeat
        BEGIN with the same tx_id (debug-replay friendly)."""
        if not tx_id:
            raise CatalogError("Empty tx_id")
        self.snapshots[tx_id] = {
            name: {key: dict(members) for key, members in bucket.items()} for name, bucket in self.catalog.items()
        }
        return {}

    def snapshot_entries(self, tx_id: str, name: str) -> dict[str, dict[str, dict[str, str]]]:
        """Bucket lookup INSIDE the snapshot of tx (isolated view, same three-level shape as
        entries); {} for names the snapshot doesn't know; unknown tx snapshot raises."""
        if not tx_id or not name:
            raise CatalogError("Empty tx_id or name")
        snapshot = self.snapshots.get(tx_id)
        if snapshot is None:
            raise CatalogError(f"No snapshot for transaction {tx_id}")
        return {key: dict(members) for key, members in snapshot.get(name, {}).items()}

    def release_snapshot(self, tx_id: str) -> dict[str, str]:
        """Drops snapshots[tx_id]; idempotent (also used on ROLLBACK — nothing is written back yet,
        see the debug-mode caveats on the SM's in-txn catalog updates)."""
        if not tx_id:
            raise CatalogError("Empty tx_id")
        self.snapshots.pop(tx_id, None)
        return {}

    # --- component plumbing ---------------------------------------------------------------------------

    def _dump(self) -> None:
        """Plain JSON dump of the in-memory dict for inspection only (nothing reads it back; startup is always empty by design)."""
        with open(self.dump_path, "w", encoding="utf-8") as fh:
            json.dump(self.catalog, fh, indent=2, sort_keys=True)

    def _create_responder(self, protocol: Protocol) -> Responder:
        """Wire this component into its Avro responder."""
        return CatalogResponder(local_protocol=protocol, catalog=self)
