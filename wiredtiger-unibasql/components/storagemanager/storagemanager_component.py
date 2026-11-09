import json
import os
import shutil
from pathlib import Path

import wiredtiger
from avro.ipc import Responder
from avro.protocol import Protocol
from wiredtiger import wiredtiger_open

from ..component import Component
from ..queryprocessor.unibasql_ddl_nodes import IndexDDL, TableDDL
from ..queryprocessor.unibasql_unresolved import ColumnConstraint, DataType, DDLOp
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

    def invoke(self, local_message, request):  # type: ignore
        message = local_message.name

        if message == "execute":
            return self.execute(request)

        if message == "execute_ddl":
            return self.execute(request=request)

    def execute(self, request):
        inner = request["request"]
        node = inner["plan"]
        kind = node["kind"]

        return self._dispatch(node=node, kind=kind)

    def _dispatch(self, node, kind: str):
        match kind:
            case "TableDDL":
                py_node = IndexDDL.from_avro(node)
                if isinstance(py_node, TableDDL):
                    if py_node.op == DDLOp.CREATE:
                        return self._handle_create_table(node=py_node)
                    return self._handle_drop_table(node=py_node)

            case "IndexDDL":
                py_node = IndexDDL.from_avro(node)
                if isinstance(py_node, IndexDDL):
                    if py_node.op == DDLOp.CREATE:
                        return self._handle_create_index(node=py_node)
                    return self._handle_drop_index(node=py_node)

        raise ValueError(f"Unhandled DDL kind: {kind}")

    def _handle_create_index(self, node: IndexDDL):
        table_name = node.table.name
        idx_name = node.idx_name
        # NOTE: columns are guaranteed to exists through parser/analyzer
        idx_cols = [c.name for c in node.columns] if node.columns else []
        idx_cols_str = ",".join(c for c in idx_cols)
        with self.storage_manager.connection.open_session() as session:
            with wt_transaction(session=session):
                check(session.create(f"index:{table_name}:{idx_name}", f"columns=({idx_cols_str})"))

            return {"value": {}}

    def _handle_drop_index(self, node: IndexDDL):
        with self.storage_manager.connection.open_session() as session:
            check(session.checkpoint())
            with wt_transaction(session):
                session.drop(f"index:{node.table.name}:{node.idx_name}", "remove_files=true")
            return {"value": {}}

    def _handle_drop_table(self, node: TableDDL):
        with self.storage_manager.connection.open_session() as session:
            check(session.checkpoint())
            with wt_transaction(session):
                session.drop(f"table:{node.table.name}", "remove_files=true")
                return {"value": {}}

    def _handle_create_table(self, node: TableDDL):
        # NOTE there might be a reorder when keys not in beginning of definition! look if need to handle or store that way in catalog!
        # NOTE directly sent from qp to sm, bypass qopt
        # TODO: check before that ddlop is create
        # TODO: need to properly deserialize from generic type for testing not done now
        # TODO: need to get potential associated tx from pool not alway create new. if no tx exist create new
        # TODO: send updates directly to "catalog"
        # NOTE: secondary indexes are not used automatically, primary index is used automatically

        key_cols: list[tuple[str, str]] = []
        cols: list[tuple[str, str]] = []
        table = node.table

        """
        config = json.dumps({
            "key_format" : "r",
            "value_format" : "5sHQ",
            "columns" : ("id", "country", "year", "population"),
            "colgroup.population" : ["population"],
            "index.country_year" : ["country", "year"]
        })
        """
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
        }
        # NOTE primary key is implicitly indexed by WT
        with self.storage_manager.connection.open_session() as session:
            with wt_transaction(session):
                check(session.create(f"table:{node.table.name}", json.dumps(config)))
            return {"value": {}}


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
