from pathlib import Path

from avro.ipc import Responder
from avro.protocol import Protocol

from ..component import Component


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

    def _create_responder(self, protocol: Protocol) -> Responder:
        return ClientResponder(local_protocol=protocol)

    def _query(self, query: str) -> str:
        res = self.call_remote(destination="queryprocessor", rpc_name="evaluate", params={"request": {"query": query}})
        return res
