import os
import uuid
from pathlib import Path

import wiredtiger
from avro.ipc import Responder
from avro.protocol import Protocol
from wiredtiger import wiredtiger_open, wiredtiger_strerror

from components.queryprocessor.docalgebra import Insert

from ..component import Component
from ..errors import WiredTigerError
from ..queryprocessor import (
    AbstractAlgebraNode,
    CreateTable,
    Delete,
    DeleteTable,
    Projection,
    Update,
    dict_satisfies,
    from_avro_node,
)
from .execution_node import ExecBuilder, ProjectionExecNode
from .value_utils import decode_value, encode_value


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

            case "Delete":
                return self._handle_delete(node=node)  # type: ignore

            case "Update":
                return self._handle_update(node=node)  # type: ignore

            case "Projection":
                # NOTE, TableScan and Selection are always wrapped by a Projection
                return self._handle_projection(node=node)  # type: ignore

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

    def _handle_insert(self, node: Insert):  # type: ignore
        with self.storage_manager.connection.open_session() as session:
            try:
                with session.open_cursor(f"table:{node.table}") as cursor:
                    return_map = {}

                    # CHANGE: entire insert is treated as atomic
                    if err := session.begin_transaction():
                        raise WiredTigerError(message=wiredtiger_strerror(err), source=self.storage_manager.name)  # type: ignore
                    for document in node.values:
                        if not document:  # skip empty documents
                            continue
                        document_id = uuid.uuid4()
                        document_id_str = str(document_id)
                        document_id_bytes = document_id.bytes

                        for key, value in document.items():
                            cursor.set_key(document_id_bytes, key)
                            encoded_value = encode_value(val_type=value.type, value=value.value)
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
                        if reset_err := cursor.reset():
                            raise WiredTigerError(
                                message=wiredtiger_strerror(reset_err),  # type: ignore
                                source=self.storage_manager.name,
                            )
                        return_map[document_id_str] = {
                            "type": "docid",
                            "value": document_id_bytes,
                        }  # TODO figure out a better return value than this...

                    if commit_err := session.commit_transaction():  # commit the whole insert atomically
                        raise WiredTigerError(
                            message=wiredtiger_strerror(commit_err),  # type: ignore
                            source=self.storage_manager.name,
                        )
            except Exception as e:
                raise e
            return {"value": return_map}

    def _handle_delete(self, node: Delete):
        # NOTE: in order to keep things simple we always cycle through all records, even if expr is _id = unique id
        # since could be part of a more complex expression
        return_map = {}
        with self.storage_manager.connection.open_session() as session:
            try:
                with session.open_cursor(f"table:{node.table}") as cursor:
                    if start_tx_err := session.begin_transaction():
                        raise WiredTigerError(
                            message=wiredtiger_strerror(start_tx_err),  # type: ignore
                            source=self.storage_manager.name,
                        )

                    current_doc_id: bytes | None = None
                    current_doc_id_str = ""
                    dict_doc = {}
                    to_delete: list[tuple[bytes, str]] = []
                    while cursor.next() == 0:
                        key, attribute = cursor.get_key()

                        if current_doc_id is not None and current_doc_id != key:
                            # document boundary: the previous document is complete,
                            # so evaluate it before starting the next one
                            # NOTE: if expression is none delete all entries of table
                            if dict_satisfies(expr=node.expr, values=dict_doc):
                                to_delete.append((current_doc_id, current_doc_id_str))

                            dict_doc = {}
                            current_doc_id_str = ""

                        current_doc_id = key
                        if "_id" not in dict_doc:
                            current_doc_id_str = str(uuid.UUID(bytes=key))
                            dict_doc["_id"] = current_doc_id_str

                        val_type, value_bytes = cursor.get_value()
                        value = decode_value(val_type=val_type, data=value_bytes)

                        dict_doc[attribute] = value

                    # a document is only evaluated when the next one starts, so the
                    # last document in scan order has to be evaluated here as well
                    if current_doc_id is not None and dict_satisfies(expr=node.expr, values=dict_doc):
                        to_delete.append((current_doc_id, current_doc_id_str))

                    if reset_err := cursor.reset():
                        raise WiredTigerError(
                            message=wiredtiger_strerror(reset_err),  # type: ignore
                            source=self.storage_manager.name,
                        )

                    for doc_id_bytes, doc_id_str in to_delete:
                        try:
                            self._delete_doc(cursor=cursor, doc_id=doc_id_bytes)
                            return_map[doc_id_str] = {
                                "type": "docid",
                                "value": doc_id_bytes,
                            }
                        except Exception as e:
                            if rollback_err := session.rollback_transaction():
                                raise WiredTigerError(
                                    message=wiredtiger_strerror(rollback_err),  # type: ignore
                                    source=self.storage_manager.name,
                                )
                            raise e

                    if commit_err := session.commit_transaction():
                        raise WiredTigerError(
                            message=wiredtiger_strerror(commit_err),  # type: ignore
                            source=self.storage_manager.name,
                        )
                    if reset_err := cursor.reset():
                        raise WiredTigerError(
                            message=wiredtiger_strerror(reset_err),  # type: ignore
                            source=self.storage_manager.name,
                        )

            except Exception as e:
                raise e
            return {"value": return_map}

    def _delete_doc(self, cursor: wiredtiger.Cursor, doc_id: bytes) -> None:
        try:
            cursor.set_key(doc_id, "")
            ret = cursor.search_near()
            # (docid, "") sorts before every (docid, attribute) row of the
            # document, so search_near rarely matches exactly (ret == 0); only
            # ret < 0 means the cursor is on the last row of the previous
            # document and we have to step onto our own first row.
            if ret < 0 and cursor.next() != 0:
                return

            # search_near may position before or after the requested key.
            key_doc_id, _ = cursor.get_key()
            if key_doc_id != doc_id:
                return

            # remove all rows belonging to this document
            while True:
                key_doc_id, _ = cursor.get_key()
                if key_doc_id != doc_id:
                    break
                cursor.remove()
                if cursor.next() != 0:
                    break
        finally:
            cursor.reset()

    def _handle_update(self, node: Update):
        return_map = {}
        # IDEA: similarly to delete first retrieve the candidates and then perform the updates
        with self.storage_manager.connection.open_session() as session:
            try:
                with session.open_cursor(f"table:{node.table}") as cursor:
                    if start_tx_err := session.begin_transaction():
                        raise WiredTigerError(
                            message=wiredtiger_strerror(start_tx_err),  # type: ignore
                            source=self.storage_manager.name,
                        )

                    # NOTE: cursor.next() on an ALREADY EXHAUSTED cursor (after it
                    # returned WT_NOTFOUND once) RESTARTS the scan and returns 0 again in
                    # this WT build. A scan loop must therefore never call next() after it
                    # hit the end.
                    current_doc_id: bytes | None = None
                    current_doc_id_str = ""
                    dict_doc = {}
                    to_update: list[
                        tuple[bytes, str, str, str, bytes]
                    ] = []  # byte_id, str_id, attr_name, type ,byte_value

                    while cursor.next() == 0:
                        key, attribute = cursor.get_key()
                        # document boundary: the doc id changed, so the previous document
                        # is complete and can be evaluated BEFORE the boundary state is
                        # overwritten for the new document (row 1 of the new doc must not
                        # claim the identity before the old doc was flushed)
                        # NOTE: if expression is none every document matches
                        if current_doc_id is not None and current_doc_id != key:
                            if dict_satisfies(expr=node.expr, values=dict_doc):
                                for k, v in node.kv:
                                    val = encode_value(val_type=v.type, value=v.value)
                                    to_update.append((current_doc_id, current_doc_id_str, k, v.type, val))

                            # start assembling the next document; the new identity is
                            # claimed below in the same iteration
                            dict_doc = {}
                            current_doc_id_str = ""

                        # update the boundary state to the doc of the current row
                        current_doc_id = key
                        # first row seen of a document: claim its identity and expose _id
                        # (dict_doc was just cleared at the boundary, or this is row 1)
                        if "_id" not in dict_doc:
                            current_doc_id_str = str(uuid.UUID(bytes=key))
                            dict_doc["_id"] = current_doc_id_str

                        val_type, value_bytes = cursor.get_value()
                        value = decode_value(val_type=val_type, data=value_bytes)

                        dict_doc[attribute] = value

                    if current_doc_id is not None and dict_satisfies(expr=node.expr, values=dict_doc):
                        for k, v in node.kv:
                            val = encode_value(val_type=v.type, value=v.value)
                            to_update.append((current_doc_id, current_doc_id_str, k, v.type, val))

                    if reset_err := cursor.reset():
                        raise WiredTigerError(
                            message=wiredtiger_strerror(reset_err),  # type: ignore
                            source=self.storage_manager.name,
                        )

                    for doc_id_bytes, doc_id_str, attr_name, val_type, byte_val in to_update:
                        try:
                            self._update_doc(
                                cursor=cursor,
                                doc_id=doc_id_bytes,
                                attr_name=attr_name,
                                value_type=val_type,
                                value=byte_val,
                            )
                            return_map[f"{doc_id_str}-{attr_name}"] = {"type": val_type, "value": byte_val}
                        except Exception as e:
                            if rollback_err := session.rollback_transaction():
                                raise WiredTigerError(
                                    message=wiredtiger_strerror(rollback_err),  # type: ignore
                                    source=self.storage_manager.name,
                                )
                            raise e

                    # CHANGED/FIX: commit was MISSING entirely (the delete handler has it).
                    # All _update_doc inserts ran inside begin_transaction(); returning
                    # without commit made the session close roll the transaction back,
                    # so the reply reported success while the changes never persisted.
                    if commit_err := session.commit_transaction():
                        raise WiredTigerError(
                            message=wiredtiger_strerror(commit_err),  # type: ignore
                            source=self.storage_manager.name,
                        )
            except Exception as e:
                raise e

            return {"value": return_map}

    def _update_doc(self, cursor: wiredtiger.Cursor, doc_id: bytes, attr_name: str, value_type: str, value: bytes):
        try:
            cursor.set_key(doc_id, attr_name)
            cursor.set_value(value_type, value)
            if insert_err := cursor.insert():
                raise WiredTigerError(
                    message=wiredtiger_strerror(insert_err),  # type: ignore
                    source=self.storage_manager.name,
                )

        finally:
            if reset_err := cursor.reset():
                raise WiredTigerError(
                    message=wiredtiger_strerror(reset_err),  # type: ignore
                    source=self.storage_manager.name,
                )

    def _handle_projection(self, node: Projection):
        exec_node: ProjectionExecNode = ExecBuilder().build(algebra_node=node)  # type: ignore
        # NOTE: not ideal but rather reconstruct types instead of touching the expression eval:)
        py_tags = {str: "string", int: "int", float: "float", type(None): "null"}
        documents = {}
        for doc in exec_node.execute(wt_conn=self.storage_manager.connection):
            docid = doc.get("_id")
            documents[docid] = {
                attribute: {
                    "type": py_tags[type(value)],
                    "value": encode_value(val_type=py_tags[type(value)], value=value),
                }
                for attribute, value in doc.items()
            }
        return {"value": {}, "documents": documents}


class StorageManagerComponent(Component):
    def __init__(
        self,
        name: str,
        broker_protocol_path: Path,
        protocol_path: Path,
        broker_socket: str,
        component_socket: str,
        wiredtiger_home: str = "WT_DOC_HOME",
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
        if not os.path.isdir(self.db_path):
            os.mkdir(self.db_path)
            self.new_db = True
