from pathlib import Path

import avro.ipc
from avro.protocol import Protocol

from components.queryprocessor.unibasql_algebra import AbstractAlgebraNode
from components.queryprocessor.unibasql_ddl_nodes import AbstractDDLNode
from components.queryprocessor.unibasql_tcl_nodes import AbstractTCLNode

from ..component import Component
from ..errors import ComponentError, QueryProcessorError
from .unibasql_analyzer import QueryAnalyzer
from .unibasql_parser import parse_unibasql


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
            # NOTE assume single queries for the moment
            parsed_query = parse_unibasql(query)[0]
            analyzed_query = QueryAnalyzer(bypass_catalog=self.qp.bypass_catalog).analyze_query(parsed_query)
            match analyzed_query:
                case AbstractDDLNode():
                    res = self.qp.call_remote(
                        destination="storagemanager",
                        rpc_name="execute_ddl",
                        params={"request": {"plan": analyzed_query.to_avro()}},
                    )
                    result = res["value"]
                case AbstractAlgebraNode():
                    pass
                case AbstractTCLNode():
                    # TODO check if full tx or only begin and end and then either directly send to sm or go through qp
                    pass

        except ComponentError:
            # Typed pass-through
            raise
        except Exception as e:
            raise QueryProcessorError(str(e), source=self.qp.name) from e
        reply = {"value": result}
        if res.get("documents") is not None:
            reply["documents"] = res["documents"]
        return reply


class QueryProcessor(Component):
    def __init__(
        self,
        name: str,
        broker_protocol_path: Path,
        protocol_path: Path,
        broker_socket: str,
        component_socket: str,
        bypass_catalog: bool = False,
    ) -> None:
        # FIX: bypass_catalog debug switch, handed to the analyzer (catalog-enforcing behavior unchanged when False).
        self.bypass_catalog = bypass_catalog
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)

    def _create_responder(self, protocol: Protocol) -> avro.ipc.Responder:
        return QueryProcessorResponder(local_protocol=protocol, qp=self)
