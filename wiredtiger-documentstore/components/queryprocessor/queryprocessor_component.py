from pathlib import Path

import avro.ipc
from avro.protocol import Protocol

from ..component import Component
from ..errors import ComponentError, QueryProcessorError
from .avro_utils import to_avro_node
from .queryparser import parse_docql


class QueryProcessorResponder(avro.ipc.Responder):
    def __init__(self, local_protocol, qp: QueryProcessor):
        super().__init__(local_protocol)
        self.qp = qp

    def invoke(self, local_message, request):  # type: ignore
        method = local_message.name

        if method == "evaluate":
            return self.evaluate(request)

    def evaluate(self, request):
        inner = request["request"]
        query = inner["query"]
        try:
            parsed_query = parse_docql(query)
            res = self.qp.call_remote(
                destination="storagemanager",
                rpc_name="execute",
                params={"request": {"plan": to_avro_node(parsed_query)}},
            )
            result = res["value"]
        except ComponentError:
            # Typed pass-through
            raise
        except Exception as e:
            raise QueryProcessorError(str(e), source=self.qp.name) from e
        return {"value": result}


class QueryProcessor(Component):
    def __init__(
        self,
        name: str,
        broker_protocol_path: Path,
        protocol_path: Path,
        broker_socket: str,
        component_socket: str,
    ) -> None:
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)

    def _create_responder(self, protocol: Protocol) -> avro.ipc.Responder:
        return QueryProcessorResponder(local_protocol=protocol, qp=self)
