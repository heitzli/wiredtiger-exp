from __future__ import annotations

import json
import os
import shutil
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import wiredtiger
from avro.ipc import Responder
from avro.protocol import Protocol
from wiredtiger import Cursor, Session, wiredtiger_open

from components.queryprocessor.unibasql_tcl_nodes import TxEndNode, TxStartNode

from ..component import Component
from ..errors import WiredTigerError
from ..queryprocessor.unibasql_algebra import (
    AbstractAlgebraNode,
    TableScan,
    VirtualTableScan,
)
from ..queryprocessor.unibasql_ddl_nodes import IndexDDL, TableDDL
from ..queryprocessor.unibasql_unresolved import ColumnConstraint, DataType, DDLOp, TxResult
from . import row_codec
from .wt_utils import check, wt_transaction

# _SCAN_TX_CONFIG is the SM-local snapshot isolation config for unpinned scans
_SCAN_TX_CONFIG = "isolation=snapshot"  # repeatable-read snapshot for unpinned scans


class StorageManagerResponder(Responder):
    def __init__(self, local_protocol: Protocol, storage_manager: StorageManagerComponent) -> None:
        super().__init__(local_protocol)
        self.storage_manager = storage_manager
        self.type_lookup = {
            DataType.BOOL: "t",
            DataType.STRING: "S",
            DataType.INT: "q",
            DataType.DOUBLE: "u",
        }  # q=int_64 doubles are stored as 8byte iee floats
        self.transactions: dict[str, Session] = {}

    def invoke(self, local_message: Any, request: dict[str, Any]) -> Any:  # type: ignore[override]
        message = local_message.name
        # TODO check if separation is needed here or sufficient in execute / dispatch
        if message == "execute":
            return self.execute(request)

        if message == "execute_ddl" or message == "execute_tcl":
            return self.execute(request=request)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        inner = request["request"]
        node = inner["plan"]
        kind = node["kind"]
        custom = inner["custom"]

        try:
            return self._dispatch(node=node, kind=kind, custom=custom)
        except Exception:
            self.storage_manager.log.error("execute failed\n" + traceback.format_exc())
            raise

    def _dispatch(self, node: dict[str, Any], kind: str, custom: dict[str, str]) -> dict[str, Any]:
        match kind:
            case "TxStartNode":
                tx_start: TxStartNode = TxStartNode.from_avro(node)
                return self._handle_tx_start(node=tx_start, tx_id=tx_start.tx_id)
            case "TxEndNode":
                tx_end: TxEndNode = TxEndNode.from_avro(node)
                return self._handle_tx_end(node=tx_end, tx_id=custom.get("tx_id"))
            case "TableDDL":
                table_ddl = IndexDDL.from_avro(node)
                if isinstance(table_ddl, TableDDL):
                    if table_ddl.op == DDLOp.CREATE:
                        return self._handle_create_table(node=table_ddl, tx_id=custom.get("tx_id"))
                    return self._handle_drop_table(node=table_ddl, tx_id=custom.get("tx_id"))

            case "IndexDDL":
                index_ddl = IndexDDL.from_avro(node)
                if isinstance(index_ddl, IndexDDL):
                    if index_ddl.op == DDLOp.CREATE:
                        return self._handle_create_index(node=index_ddl, tx_id=custom.get("tx_id"))
                    return self._handle_drop_index(node=index_ddl, tx_id=custom.get("tx_id"))

            # SM streams RAW tuples only; Selection/Projection replay in the EE executor
            case "TableScan":
                return self._handle_table_scan(node=node, tx_id=custom.get("tx_id"))

            case "Union":
                # TODO: union can also be normal union, insert, delete or update... need to check custom
                return self._handle_insert(node=node, tx_id=custom.get("tx_id"))

        raise ValueError(
            f"No executor for node kind {kind!r} in storagemanager (algebra execution not implemented yet)"
        )

    def _table_schema(self, table: str) -> list[tuple[str, DataType, str | None, int]]:
        """Logical column layout straight from the catalog: [(name, type, constraint, position)]."""
        schema_raw = self.storage_manager.call_remote(
            destination="catalog", rpc_name="get", params={"table": table, "key": "schema"}
        )
        if not schema_raw:
            raise WiredTigerError(f"No schema for table {table!r} in catalog")
        cols = []
        for name, attrs in schema_raw.items():
            dtype = DataType(attrs["type"])
            # catalog stores the constraint as str(enum) ("ColumnConstraint.PRIMARY_KEY") or ""
            # catalog stores the constraint as str(enum); StrEnum renders LOWERCASE ("primary_key")
            constraint_raw = (attrs.get("constraint") or "").lower()
            constraint = next((c for c in ColumnConstraint if constraint_raw.endswith(c.value)), None)
            cols.append((name, dtype, constraint, int(attrs["position"])))
        cols.sort(key=lambda c: c[3])
        return cols

    @staticmethod
    def _value_dtype(rv: Any) -> DataType:
        """Effective cell type of an analyzer value: NULL-typed (padding) counts as NULL."""
        return DataType.NULL if rv.type is DataType.NULL or rv.value is None else rv.type

    @staticmethod
    def _raw_scan_tuples(
        cursor: Cursor, n_pk: int, n_val: int, schema: list[tuple[str, DataType, str | None, int]]
    ) -> Iterator[list[Any]]:
        pk_types = [c[1] for c in schema if c[2] is ColumnConstraint.PRIMARY_KEY]
        val_types = [c[1] for c in schema if c[2] is not ColumnConstraint.PRIMARY_KEY]
        while cursor.next() == 0:
            k = cursor.get_key()
            # NOTE pywt returns COMPOSITE keys as list
            key = tuple(k) if isinstance(k, (tuple, list)) else (k,)
            value = cursor.get_value()
            nullmap_flags = row_codec.decode_nullmap(value[0], n_val)
            cells = [
                row_codec.wire_cell(pk_types[i], row_codec.decode_cell(pk_types[i], key[i]))
                for i in range(min(len(key), n_pk))
            ]
            cells.extend(
                None
                if nullmap_flags[i]
                else row_codec.wire_cell(val_types[i], row_codec.decode_cell(val_types[i], value[i + 1]))
                for i in range(n_val)
            )
            yield cells

    def _handle_insert(self, node: dict[str, Any], tx_id: str | None = None) -> dict[str, Any]:
        """Probe-grade INSERT executor (NOT optimized): extracts the VirtualTableScan of an
        analyzed insert-union and writes each padded row into the table (PK cells -> key,
        nullmap + value cells -> value)."""
        union = AbstractAlgebraNode.from_avro(node)
        # analyzed insert shape: left = target TableScan, right = VirtualTableScan with the tuples
        left, right = getattr(union, "left", None), getattr(union, "right", None)
        virtual = next((c for c in (right, left) if isinstance(c, VirtualTableScan)), None)
        if virtual is None:
            raise WiredTigerError("INSERT plan carries no VirtualTableScan branch")
        table = left.table.name

        schema = self._table_schema(table)
        pk_cols = [c for c in schema if c[2] is ColumnConstraint.PRIMARY_KEY]
        val_cols = [c for c in schema if c[2] is not ColumnConstraint.PRIMARY_KEY]
        if len(virtual.values[0]) != len(schema):
            raise WiredTigerError("Insert tuple width does not match table schema")

        session, owns_tx = self._scan_session(tx_id)
        cursor = session.open_cursor(f"table:{table}")
        cursor_closed = False
        try:
            for row in virtual.values:
                # analyzer pads rows to full schema width, in schema-position order
                if any(self._value_dtype(row[c[3]]) is DataType.NULL for c in pk_cols):
                    raise WiredTigerError(f"NULL primary key value in insert into {table!r}")
                pk_cells = [row_codec.encode_cell(c[1], row[c[3]].value) for c in pk_cols]
                null_flags = [self._value_dtype(row[c[3]]) is DataType.NULL for c in val_cols]
                # NOTE cells heterogeneous BY FORMAT LETTER (see row_codec); physical value order =
                # declaration order after the _nullmap (matches _handle_create_table); the nullmap
                # is the authoritative NULL marker
                val_cells = [
                    row_codec.blank_cell(c[1]) if null_flags[i] else row_codec.encode_cell(c[1], row[c[3]].value)
                    for i, c in enumerate(val_cols)
                ]
                cursor.set_key(*pk_cells)
                cursor.set_value(row_codec.encode_nullmap(null_flags), *val_cells)
                cursor.insert()
            cursor.close()
            cursor_closed = True
        except BaseException:
            # raise the ORIGINAL error; never let cleanup mask it (SWIG double-close traps)
            if not cursor_closed:
                try:
                    cursor.close()
                except Exception:  # noqa: BLE001 — cursor may already be torn down with the session
                    pass
            if owns_tx:
                self._dispose_scan_session(session)
            raise
        if owns_tx:
            # autocommit path: inserts ran as implicit transactions; persist on the way out
            check(session.checkpoint())
            session.close()
        return {"value": {}}

    def _scan_session(self, tx_id: str | None) -> tuple[Session, bool]:
        """Session for data ops: the transaction's session (caller never commits) or a fresh
        autocommit session. Second element = owns (True => caller manages commit/close)."""
        if tx_id and (tx_session := self.transactions.get(tx_id)):
            return tx_session, False
        return self.storage_manager.connection.open_session(), True

    def _dispose_scan_session(self, session: Session) -> None:
        try:
            session.rollback_transaction()
        except Exception:  # noqa: BLE001 — disposal is best effort
            pass
        session.close()

    def _handle_table_scan(self, node: dict[str, Any], tx_id: str | None = None) -> dict[str, Any]:
        """Registers a raw tuple-scan stream (cursor + iterator); operators replay in the EE."""
        scan = AbstractAlgebraNode.from_avro(node)
        if not isinstance(scan, TableScan):
            raise WiredTigerError(f"No executor for node kind {type(scan).__name__!r} in storagemanager")
        table = scan.table.name
        schema = self._table_schema(table)
        pk_cols = [c for c in schema if c[2] is ColumnConstraint.PRIMARY_KEY]
        val_cols = [c for c in schema if c[2] is not ColumnConstraint.PRIMARY_KEY]
        # physical tuple order: PK cells first, then value columns; with composite/non-contiguous
        # PKs this DIFFERS from catalog declaration order — the header must describe the TUPLE
        names = [c[0] for c in pk_cols] + [c[0] for c in val_cols]
        types = [c[1] for c in pk_cols] + [c[1] for c in val_cols]

        if tx_id and (tx_session := self.transactions.get(tx_id)):
            # inside a transaction: SHARE the tx session; stream disposal must never close it,
            # lifecycle stays with TxEndNode — only the cursor is owned by the stream
            session, owns_snapshot = tx_session, False
        else:
            session = self.storage_manager.connection.open_session()
            check(session.begin_transaction(_SCAN_TX_CONFIG))
            owns_snapshot = True

        cursor = session.open_cursor(f"table:{table}")

        def scan_rows() -> Iterator[list[Any]]:
            yield from self._raw_scan_tuples(cursor, len(pk_cols), len(val_cols), schema)

        def dispose_scan() -> None:
            # EXACTLY-ONCE via StreamHandle.dispose(); for a private snapshot session this
            # aborts the read txn and closes both cursor and session; a shared tx session
            # keeps living (only the cursor goes away)
            try:
                cursor.close()
            except Exception:  # noqa: BLE001
                pass
            if owns_snapshot:
                try:
                    session.rollback_transaction()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    session.close()
                except Exception:  # noqa: BLE001
                    pass

        stream_id = self.storage_manager.register_stream(scan_rows(), resource=None, closer=dispose_scan)
        self.storage_manager._stream_tx[stream_id] = tx_id or ""
        self.storage_manager.log.info("scan stream opened", extra={"stream_id": stream_id, "table": table})
        # header rides the init reply (map<string> value): CSV "name:TAG" in TUPLE order (PKs
        # first!), the type authority for every downstream consumer of the stream
        header = ",".join(f"{name}:{row_codec.type_tag(dtype)}" for name, dtype in zip(names, types))
        return {"value": {"columns": header}, "custom": None, "stream_id": stream_id}

    def _handle_tx_start(self, node: TxStartNode, tx_id: str) -> dict[str, Any]:
        if tx_id in self.transactions.keys():
            raise ValueError(f"Transaction with same ID {node.tx_id} running")
        session = self.storage_manager.connection.open_session()
        check(session.begin_transaction())
        # NOTE if add mult workers add concurrency guard here
        self.transactions[node.tx_id] = session
        return {"value": {"tx_id": tx_id}}

    def _handle_tx_end(self, node: TxEndNode, tx_id: str) -> dict[str, Any]:
        # TxID must exist
        if not tx_id:
            raise ValueError("No Transaction id provided")
        if tx_session := self.transactions.get(tx_id):
            match node.tx_result:
                case TxResult.COMMIT:
                    check(tx_session.commit_transaction())
                    check(tx_session.checkpoint())
                    # TODO check if this is the right time to close the session
                    check(tx_session.close())
                case TxResult.ROLLBACK:
                    check(tx_session.rollback_transaction())
                    check(tx_session.checkpoint())
                    check(tx_session.close())
            # NOTE if add mult workers add concurrency guard here
            self.transactions.pop(tx_id)
            # NOTE any scan streams sharing this tx session die with it (force-release leaked cursors)
            for stream_id, owner in list(self.storage_manager._stream_tx.items()):
                if owner == tx_id:
                    # NOTE discard_stream pops the lease via the component's _on_stream_gone hook
                    self.storage_manager.discard_stream(stream_id)
            return {"value": {}}
        raise ValueError(f"No transaction with ID {tx_id}")

    # +++ catalog synchronization -----------------------------------------------------------------------

    def _catalog_set(self, name: str, key: str, value: dict[str, dict[str, str]]) -> None:
        try:
            self.storage_manager.call_remote(
                destination="catalog", rpc_name="set", params={"table": name, "key": key, "value": value}
            )
        except Exception as e:
            raise WiredTigerError(f"Failed to update catalog entry {name}.{key}: {e}") from e

    def _catalog_delete(self, name: str) -> None:
        try:
            self.storage_manager.call_remote(destination="catalog", rpc_name="delete", params={"name": name})
        except Exception as e:
            raise WiredTigerError(f"Failed to delete catalog entry {name}: {e}") from e

    def _sync_catalog_create_table(self, node: TableDDL) -> None:
        # NOTE the WHOLE schema goes into one entry: catalog[table]["schema"] = {col: {props}};
        # "position" preserves the SQL definition order because the avro map return does not
        # guarantee ordering (WT's physical PK-first order is a separate concern, see the reorder
        # NOTE in _handle_create_table).
        self._catalog_set(
            name=node.table.name,
            key="schema",
            value={
                col.name: {
                    "type": str(col.type),
                    "constraint": str(col.constraint) if col.constraint else "",
                    "position": str(pos),
                }
                for pos, col in enumerate(node.table.table_schema)
            },
        )

    def _sync_catalog_create_index(self, node: IndexDDL) -> None:
        idx_cols = ",".join(c.name for c in node.columns) if node.columns else ""
        # NOTE the bucket carries its meta under the reserved "index" key; the presence of that key
        # is what tells an index bucket apart from a table bucket in the analyzer's checks.
        self._catalog_set(
            name=node.idx_name,
            key="index",
            value={"meta": {"kind": "index", "table": node.table.name, "columns": idx_cols}},
        )

    # ---

    def _handle_create_index(self, node: IndexDDL, tx_id: str | None = None) -> dict[str, Any]:
        # NOTE session.create() is not transactional!
        table_name = node.table.name
        idx_name = node.idx_name
        # NOTE: columns are guaranteed to exists through parser/analyzer
        idx_cols = [c.name for c in node.columns] if node.columns else []
        idx_cols_str = ",".join(c for c in idx_cols)

        if tx_id:
            if tx_session := self.transactions.get(tx_id):
                check(tx_session.create(f"index:{table_name}:{idx_name}", f"columns=({idx_cols_str}),exclusive=true"))
                # NOTE catalog is updated even on the in-txn path (session.create is not transactional anyway)
                self._sync_catalog_create_index(node=node)
                return {"value": {}}

        # NOTE fallback is a single tx, maybe should add error logging here or something similar
        with self.storage_manager.connection.open_session() as session:
            with wt_transaction(session=session):
                check(session.create(f"index:{table_name}:{idx_name}", f"columns=({idx_cols_str})"))

        self._sync_catalog_create_index(node=node)
        return {"value": {}}

    def _handle_drop_index(self, node: IndexDDL, tx_id: str | None = None) -> dict[str, Any]:
        if tx_id:
            if tx_session := self.transactions.get(tx_id):
                check(tx_session.checkpoint())
                check(tx_session.drop(f"index:{node.table.name}:{node.idx_name}", "remove_files=true"))
                self._catalog_delete(name=node.idx_name)
                return {"value": {}}
        with self.storage_manager.connection.open_session() as session:
            check(session.checkpoint())
            with wt_transaction(session):
                check(session.drop(f"index:{node.table.name}:{node.idx_name}", "remove_files=true"))
            self._catalog_delete(name=node.idx_name)
            return {"value": {}}

    def _handle_drop_table(self, node: TableDDL, tx_id: str | None = None) -> dict[str, Any]:
        if tx_id:
            if tx_session := self.transactions.get(tx_id):
                check(tx_session.checkpoint())
                check(tx_session.drop(f"table:{node.table.name}", "remove_files=true"))
                # NOTE only the table bucket is removed; index meta buckets referencing this table
                # become stale here (accepted in debug mode for now).
                self._catalog_delete(name=node.table.name)
                return {"value": {}}
        with self.storage_manager.connection.open_session() as session:
            check(session.checkpoint())
            with wt_transaction(session):
                check(session.drop(f"table:{node.table.name}", "remove_files=true"))
                # NOTE only the table bucket is removed; index meta buckets referencing this table
                # become stale here (accepted in debug mode for now).
                self._catalog_delete(name=node.table.name)
                return {"value": {}}

    def _handle_create_table(self, node: TableDDL, tx_id: str | None = None) -> dict[str, Any]:
        # NOTE there might be a reorder when keys not in beginning of definition! look if need to handle or store that way in catalog!
        # NOTE directly sent from qp to sm, bypass qopt
        # TODO: send updates directly to "catalog"
        # NOTE: secondary indexes are not used automatically, primary index is used automatically
        # NOTE session.create() is not transactional!
        key_cols: list[tuple[str, str]] = []
        cols: list[tuple[str, str]] = []
        table = node.table

        # bitmap_size = (num_columns + 7) // 8
        for col in table.table_schema:
            if col.constraint == ColumnConstraint.PRIMARY_KEY:
                key_cols.append((col.name, self.type_lookup[col.type]))
            else:
                cols.append((col.name, self.type_lookup[col.type]))

        config = {
            "key_format": "".join(k[1] for k in key_cols),
            # u is for the NULL-bitmap to keep track which values are set or NULL
            "value_format": "u" + "".join(c[1] for c in cols),
            "columns": tuple(k[0] for k in key_cols) + ("_nullmap",) + tuple(c[0] for c in cols),
            "exclusive": True,
        }
        if tx_id:
            if tx_session := self.transactions.get(tx_id):
                check(tx_session.create(f"table:{node.table.name}", json.dumps(config)))
                self._sync_catalog_create_table(node=node)
                return {"value": {}}
        # NOTE primary key is implicitly indexed by WT
        with self.storage_manager.connection.open_session() as session:
            with wt_transaction(session):
                check(session.create(f"table:{node.table.name}", json.dumps(config)))
            self._sync_catalog_create_table(node=node)
            return {"value": {}}

    # ---


class StorageManagerComponent(Component):
    def __init__(
        self,
        name: str,
        broker_protocol_path: Path,
        protocol_path: Path,
        broker_socket: str,
        component_socket: str,
        wiredtiger_home: str = "WT_UNIBASQL_HOME",
    ) -> None:
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)
        self.new_db = False
        self.db_path = wiredtiger_home
        self._init_wt_folder()
        self.connection: wiredtiger.Connection = wiredtiger_open(
            self.db_path, "create,log=(enabled),statistics=(all),transaction_sync=(enabled)"
        )
        # SM adds tx-lease bookkeeping: stream_id -> owning tx (or ""), TxEnd force-releases leaks
        self._stream_tx: dict[str, str] = {}

    def _on_stream_gone(self, stream_id: str) -> None:
        # base hook: a scan stream ended (exhausted / closed / swept / TxEnd force-release)
        self._stream_tx.pop(stream_id, None)

    def _create_responder(self, protocol: Protocol) -> Responder:
        return StorageManagerResponder(local_protocol=protocol, storage_manager=self)
        # The storage manager maintains a single connection  to the wiredtiger db

    def _init_wt_folder(self) -> None:
        # TODO: for creation debugging, remove when done
        if os.path.isdir(self.db_path):
            shutil.rmtree(self.db_path)
        if not os.path.isdir(self.db_path):
            os.mkdir(self.db_path)
            self.new_db = True
