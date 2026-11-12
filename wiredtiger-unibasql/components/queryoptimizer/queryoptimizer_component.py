"""Query optimizer component: for the beginning, a pass-through that just sends algebra on unchanged."""
from pathlib import Path

from avro.ipc import Responder
from avro.protocol import Protocol

from ..component import Component


class QueryOptimizerResponder(Responder):
    """Serves the optimize RPC by returning the request's algebra verbatim (no rewriting yet)."""

    def __init__(self, local_protocol):
        """Store the local protocol for Avro IPC dispatch."""
        super().__init__(local_protocol)

    def invoke(self, local_message, request):  # type: ignore
        """Handle `optimize` by passing the request's plan straight through."""
        message = local_message.name

        if message == "optimize":
            inner = request["request"]
            return inner

        raise ValueError(f"Queryoptimizer does not support rpc {message}")


class QueryOptimizer(Component):
    """Pipeline stage between the query processor and the execution engine; optimization is pass-through today."""

    def __init__(
        self,
        name: str,
        broker_protocol_path: Path,
        protocol_path: Path,
        broker_socket: str,
        component_socket: str,
    ) -> None:
        """Initialize the component shell and a placeholder tx slot."""
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)
        self.tx_id: str = ""

    def _create_responder(self, protocol: Protocol) -> Responder:
        """Wire this component into its Avro responder."""
        return QueryOptimizerResponder(local_protocol=protocol)
