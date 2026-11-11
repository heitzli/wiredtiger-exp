from pathlib import Path

from avro.ipc import Responder
from avro.protocol import Protocol

from ..component import Component
from ..remote_stream import RemoteStream


class ClientResponder(Responder):
    def __init__(self, local_protocol):
        super().__init__(local_protocol)

    def invoke(self, local_message, request):  # type: ignore
        raise RuntimeError(f"Client serves no RPC; got {local_message.name!r}")


class Client(Component):
    def __init__(
        self,
        name: str,
        broker_protocol_path: Path,
        protocol_path: Path,
        broker_socket: str,
        component_socket: str,
    ) -> None:
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)
        self.tx_id: str = ""

    def _create_responder(self, protocol: Protocol) -> Responder:
        return ClientResponder(local_protocol=protocol)

    def _query(self, query: str, *, max_items: int = 256) -> RemoteStream | dict:
        """SQL -> QP evaluate. Streamed replies come back as a RemoteStream (header rides the
        init reply); one-shot replies (DDL/TCL) come back as the bare dict."""
        res = self.call_remote(
            destination="queryprocessor",
            rpc_name="evaluate",
            params={"request": {"query": query, "custom": {"tx_id": self.tx_id}}},
        )
        if res.get("stream_id") is not None:
            return RemoteStream(self, "queryprocessor", res, max_items)
        return res

    def stream_next(self, stream_id: str, max_items: int = 256) -> dict:
        return self.call_remote("queryprocessor", "stream_next", {"stream_id": stream_id, "max_items": max_items})

    def stream_close(self, stream_id: str) -> bool:
        return self.call_remote("queryprocessor", "stream_close", {"stream_id": stream_id})

    def begin_transaction(self, tx_id: str | None = None):
        q = f"BEGIN TRANSACTION {tx_id};" if tx_id else "BEGIN TRANSACTION;"
        res = self._query(query=q)
        val = res["value"]  # TCL is one-shot: the reply is always the bare dict here
        self.tx_id = val.get("tx_id")
        return res

    def commit(self):
        if self.tx_id == "":
            raise ValueError("Need to start tx before commit")

        return self._query(query="COMMIT;")

    def rollback(self):
        if self.tx_id == "":
            raise ValueError("Need to start tx before rollback")
        return self._query(query="ROLLBACK;")
