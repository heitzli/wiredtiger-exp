import os
from pathlib import Path

import wiredtiger
from avro.ipc import Responder
from avro.protocol import Protocol
from wiredtiger import wiredtiger_open, wiredtiger_strerror

from ..component import Component
from ..errors import WiredTigerError
from ..queryprocessor.algebra import AbstractAlgebraNode, DELETENode, GETNode, PUTNode


class StorageManagerResponder(Responder):
    def __init__(self, local_protocol, storage_manager: StorageManagerComponent):
        super().__init__(local_protocol)
        self.storage_manager = storage_manager

    def invoke(self, local_message, request):
        message = local_message.name

        if message == "execute":
            return self.execute(request)

    def execute(self, request):
        inner = request["request"]
        node_avr = inner["plan"]["node"]
        kind = node_avr["kind"]

        match kind:
            case "PUTNode":
                node = PUTNode(**node_avr)
                return self._execute_plan(node=node)
            case "GETNode":
                node = GETNode(**node_avr)
                return self._execute_plan(node=node)
            case "DELETENode":
                node = DELETENode(**node_avr)
                return self._execute_plan(node=node)
            case _:
                raise ValueError("Unknown AlgebraNode")

    def _execute_plan(self, node: AbstractAlgebraNode) -> dict[str, str]:
        with self.storage_manager.connection.open_session() as session:
            with session.open_cursor("table:T", None, None) as cursor:
                if isinstance(node, PUTNode):
                    cursor.set_key(node.key)
                    cursor.set_value(str(node.value))
                    if err := cursor.insert():
                        raise WiredTigerError(wiredtiger_strerror(err))
                    return {"value": str(node.value)}
                if isinstance(node, GETNode):
                    cursor.set_key(node.key)
                    if err := cursor.search():
                        raise WiredTigerError(wiredtiger_strerror(err))
                    res = cursor.get_value()
                    return {"value": str(res)}
                if isinstance(node, DELETENode):
                    cursor.set_key(node.key)
                    if err := cursor.remove():
                        raise WiredTigerError(wiredtiger_strerror(err))
                    return {"value": ""}
                else:
                    raise ValueError("Unknown AlgebraNode")


class StorageManagerComponent(Component):
    def __init__(
        self, name: str, broker_protocol_path: Path, protocol_path: Path, broker_socket: str, component_socket: str
    ) -> None:
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)
        self.new_db = False
        self.db_path = "/tmp/WT_HOME"
        self._init_wt_folder()
        self.connection: wiredtiger.Connection = wiredtiger_open(self.db_path, "create,log=(enabled),statistics=(all)")
        self._init_table()

    def _create_responder(self, protocol: Protocol) -> Responder:
        return StorageManagerResponder(local_protocol=protocol, storage_manager=self)
        # The storage manager maintains a single connection  to the wiredtiger db

    def _init_wt_folder(self):
        if not os.path.isdir(self.db_path):
            os.mkdir(self.db_path)
            self.new_db = True

    def _init_table(self):
        if self.new_db:
            with self.connection.open_session() as session:
                session.create("table:T", "key_format=S,value_format=S")
