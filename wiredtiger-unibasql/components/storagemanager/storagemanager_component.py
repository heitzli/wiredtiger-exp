import json
import os
import shutil
from pathlib import Path

import wiredtiger
from avro.ipc import Responder
from avro.protocol import Protocol
from wiredtiger import Session, wiredtiger_open

from components.queryprocessor.unibasql_tcl_nodes import TxEndNode, TxStartNode

from ..component import Component
from ..errors import WiredTigerError
from ..queryprocessor.unibasql_ddl_nodes import IndexDDL, TableDDL
from ..queryprocessor.unibasql_unresolved import ColumnConstraint, DataType, DDLOp, TxResult
from .wt_utils import check, wt_transaction


class StorageManagerResponder(Responder):
    def __init__(self, local_protocol, storage_manager: StorageManagerComponent):
        super().__init__(local_protocol)
        self.storage_manager = storage_manager
        self.type_lookup = {
            DataType.BOOL: "t",
            DataType.STRING: "S",
            DataType.INT: "q",
            DataType.DOUBLE: "u",
        }  # q=int_64 doubles are stored as 8byte iee floats
        self.transactions: dict[str, Session] = {}

    def invoke(self, local_message, request):  # type: ignore
        message = local_message.name
        # TODO check if separation is needed here or sufficient in execute / dispatch
        if message == "execute":
            return self.execute(request)

        if message == "execute_ddl" or message == "execute_tcl":
            return self.execute(request=request)

    def execute(self, request):
        inner = request["request"]
        node = inner["plan"]
        kind = node["kind"]
        custom = inner["custom"]

        return self._dispatch(node=node, kind=kind, custom=custom)

    def _dispatch(self, node: dict, kind: str, custom: dict[str, str]):
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

        raise ValueError(
            f"No executor for node kind {kind!r} in storagemanager (algebra execution not implemented yet)"
        )

    # +++ Statements that bypass optimizer and are directly called from IR

    def _handle_tx_start(self, node: TxStartNode, tx_id: str):
        if tx_id in self.transactions.keys():
            raise ValueError(f"Transaction with same ID {node.tx_id} running")
        session = self.storage_manager.connection.open_session()
        check(session.begin_transaction())
        # NOTE if add mult workers add concurrency guard here
        self.transactions[node.tx_id] = session
        return {"value": {"tx_id": tx_id}}

    def _handle_tx_end(self, node: TxEndNode, tx_id: str):
        # TODO: the tx id comes from the client properly impl, currently add to custom dict!
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
            return {"value": {}}
        raise ValueError(f"No transaction with ID {tx_id}")

    # --- catalog synchronization -----------------------------------------------------------------------

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

    def _handle_create_index(self, node: IndexDDL, tx_id: str | None = None):
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

    def _handle_drop_index(self, node: IndexDDL, tx_id: str | None = None):
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

    def _handle_drop_table(self, node: TableDDL, tx_id: str | None = None):
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

    def _handle_create_table(self, node: TableDDL, tx_id: str | None = None):
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

    def _create_responder(self, protocol: Protocol) -> Responder:
        return StorageManagerResponder(local_protocol=protocol, storage_manager=self)
        # The storage manager maintains a single connection  to the wiredtiger db

    def _init_wt_folder(self):
        # TODO: for creation debugging, remove when done
        if os.path.isdir(self.db_path):
            shutil.rmtree(self.db_path)
        if not os.path.isdir(self.db_path):
            os.mkdir(self.db_path)
            self.new_db = True
