import os
import struct
import uuid
from pathlib import Path

import wiredtiger
from avro.ipc import Responder
from avro.protocol import Protocol
from wiredtiger import wiredtiger_open, wiredtiger_strerror

from components.queryprocessor.docalgebra import Insert

from ..component import Component
from ..errors import WiredTigerError
from ..queryprocessor import AbstractAlgebraNode, CreateTable, DeleteTable, from_avro_node


class StorageManagerResponder(Responder):
    def __init__(self, local_protocol, storage_manager: StorageManagerComponent):
        super().__init__(local_protocol)
        self.storage_manager = storage_manager

    def invoke(self, local_message, request):  # type: ignore
        message = local_message.name

        if message == "execute":
            return self.execute(request)

    def execute(self, request):
        inner = request["request"]
        node_avr = inner["plan"]["node"]
        kind = node_avr["kind"]

        node = from_avro_node(data=node_avr)

        return self._dispatch(node=node, kind=kind)

    def _dispatch(self, node: AbstractAlgebraNode, kind: str):
        match kind:
            case "CreateTable":
                return self._handle_create_table(node=node)  # type: ignore

            case "DeleteTable":
                return self._handle_delete_table(node=node)  # type: ignore

            case "Insert":
                return self._handle_insert(node=node)  # type: ignore

    def _handle_create_table(self, node: CreateTable):
        with self.storage_manager.connection.open_session() as session:
            if err := session.begin_transaction():
                raise WiredTigerError(wiredtiger_strerror(err))  # type: ignore
            if err := session.create(f"table:{node.table}", "key_format=uS,value_format=Su"):
                if rollback_err := session.rollback_transaction():
                    raise WiredTigerError(wiredtiger_strerror(rollback_err))  # type: ignore
                raise WiredTigerError(wiredtiger_strerror(err))  # type: ignore

            if commit_err := session.commit_transaction():
                raise WiredTigerError(wiredtiger_strerror(commit_err))  # type: ignore
            return {"value": {}}

    # NOTE: in a more sophisticated implementation would need to perform peridic checkpointing instead of manual checkpointing like here
    def _handle_delete_table(self, node: DeleteTable):
        with self.storage_manager.connection.open_session() as session:
            # NOTE: needed to get rid of EBUSY after inserting values
            if checkpoint_err := session.checkpoint():
                raise WiredTigerError(
                    message=wiredtiger_strerror(checkpoint_err),  # type: ignore
                    source=self.storage_manager.name,
                )
            if err := session.begin_transaction():
                raise WiredTigerError(message=wiredtiger_strerror(err), source=self.storage_manager.name)  # type: ignore
            try:
                if delete_err := session.drop(f"table:{node.table}", "remove_files=true"):
                    if rollback_err := session.rollback_transaction():
                        raise WiredTigerError(
                            message=wiredtiger_strerror(rollback_err),  # type: ignore
                            source=self.storage_manager.name,
                        )
                    raise WiredTigerError(message=wiredtiger_strerror(delete_err), source=self.storage_manager.name)  # type: ignore
            except Exception as e:
                # XXX for some reason not caught by delete err as an int, manually catch and raise
                if "No such file or directory" in str(e):
                    raise WiredTigerError(
                        message=f"Table {node.table} does not exist.", source=self.storage_manager.name
                    )
                else:
                    raise
            if commit_err := session.commit_transaction():
                raise WiredTigerError(message=wiredtiger_strerror(commit_err), source=self.storage_manager.name)  # type: ignore
        return {"value": {}}

    @staticmethod
    def encode_value(val_type: str, value: int | float | str | None) -> bytes:
        if val_type == "null":
            return b""
        if val_type == "string":
            return str(value).encode("utf-8")
        if val_type == "int":
            return str(value).encode("ascii")
        if val_type == "float":
            return struct.pack(">d", value)  # NOTE: >d is big-endian encoding!
        raise TypeError(f"Unsupported type: {type(value).__name__}")

    # BUG: for some reason it seems that resources are not properly released, delete table after insert not possible
    def _handle_insert(self, node: Insert):  # type: ignore
        with self.storage_manager.connection.open_session() as session:
            try:
                with session.open_cursor(f"table:{node.table}") as cursor:
                    return_map = {}
                    for document in node.values:
                        if not document:  # skip empty documents
                            continue
                        document_id = uuid.uuid4()
                        document_id_str = str(document_id)
                        document_id_bytes = document_id.bytes
                        # NOTE: a document is treated as an atomic unit and is either inserted completly or not at all.
                        # A list of documents might be only partially inserted if a crash occurs.
                        # TODO: check if this is a reasonable assumption
                        if err := session.begin_transaction():
                            raise WiredTigerError(message=wiredtiger_strerror(err), source=self.storage_manager.name)  # type: ignore
                        for key, value in document.items():
                            cursor.set_key(document_id_bytes, key)
                            encoded_value = self.encode_value(val_type=value.type, value=value.value)
                            cursor.set_value(value.type, encoded_value)
                            if insert_err := cursor.insert():
                                if rollback_err := session.rollback_transaction():
                                    raise WiredTigerError(
                                        message=wiredtiger_strerror(rollback_err),  # type: ignore
                                        source=self.storage_manager.name,
                                    )
                                raise WiredTigerError(
                                    message=wiredtiger_strerror(insert_err),  # type: ignore
                                    source=self.storage_manager.name,
                                )
                        if commit_err := session.commit_transaction():  # commit the document
                            raise WiredTigerError(
                                message=wiredtiger_strerror(commit_err),  # type: ignore
                                source=self.storage_manager.name,
                            )
                        return_map[document_id_str] = {
                            "type": "docid",
                            "value": document_id_bytes,
                        }  # TODO figure out a better return value than this...
            except Exception as e:
                raise e
            return {"value": return_map}


class StorageManagerComponent(Component):
    def __init__(
        self, name: str, broker_protocol_path: Path, protocol_path: Path, broker_socket: str, component_socket: str
    ) -> None:
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)
        self.new_db = False
        self.db_path = "WT_DOC_HOME"
        self._init_wt_folder()
        self.connection: wiredtiger.Connection = wiredtiger_open(
            self.db_path, "create,log=(enabled),statistics=(all),transaction_sync=(enabled)"
        )

    def _create_responder(self, protocol: Protocol) -> Responder:
        return StorageManagerResponder(local_protocol=protocol, storage_manager=self)
        # The storage manager maintains a single connection  to the wiredtiger db

    def _init_wt_folder(self):
        if not os.path.isdir(self.db_path):
            os.mkdir(self.db_path)
            self.new_db = True
