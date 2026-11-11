from pathlib import Path
from typing import Any

import avro.ipc
from avro.protocol import Protocol

from components.queryprocessor.unibasql_algebra import AbstractAlgebraNode
from components.queryprocessor.unibasql_ddl_nodes import AbstractDDLNode
from components.queryprocessor.unibasql_tcl_nodes import AbstractTCLNode, TxEndNode, TxStartNode

from ..component import Component
from ..errors import ComponentError, QueryProcessorError
from .unibasql_analyzer import CatalogRpcClient, QueryAnalyzer
from .unibasql_parser import parse_unibasql


class QueryProcessorResponder(avro.ipc.Responder):
    def __init__(self, local_protocol: Protocol, qp: QueryProcessor) -> None:
        super().__init__(local_protocol)
        self.qp = qp

    def invoke(self, local_message: Any, request: dict[str, Any]) -> Any:  # type: ignore[override]
        if local_message.name == "evaluate":
            return self.evaluate(request)

    def evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        inner = request["request"]
        query = inner["query"]
        custom = inner["custom"]
        # NOTE assume single queries for the moment (parser may hand back a list)
        parsed_query = parse_unibasql(query)
        if isinstance(parsed_query, list):
            parsed_query = parsed_query[0]
        analyzed_query = QueryAnalyzer(
            bypass_catalog=self.qp.bypass_catalog,
            catalog_client=CatalogRpcClient(component=self.qp),
        ).analyze_query(parsed_query)
        try:
            match analyzed_query:
                case AbstractDDLNode():
                    res = self.qp.call_remote(
                        destination="storagemanager",
                        rpc_name="execute_ddl",
                        params={"request": {"plan": analyzed_query.to_avro(), "custom": custom}},
                    )
                    reply: dict = {"value": res["value"]}
                case AbstractAlgebraNode():
                    # NOTE optimize passes through QO, then the EE executes the plan and streams, QP mirrors the stream via the BASE relay
                    opt = self.qp.call_remote(
                        destination="queryoptimizer",
                        rpc_name="optimize",
                        params={"request": {"plan": analyzed_query.to_avro(), "custom": custom}},
                    )
                    ee_reply = self.qp.call_remote(
                        destination="executionengine",
                        rpc_name="execute",
                        params={"request": {"plan": opt["plan"], "custom": opt.get("custom") or custom}},
                    )
                    if ee_reply.get("stream_id") is not None:
                        # the header in value["columns"] passes through untouched (streaming.avdl)
                        qp_sid = self.qp.open_stream_relay("executionengine", ee_reply)
                        return {"value": ee_reply["value"], "custom": ee_reply.get("custom"), "stream_id": qp_sid}
                    # one-shot algebra reply, no stream
                    reply = {"value": ee_reply["value"], "custom": ee_reply.get("custom"), "stream_id": None}
                case AbstractTCLNode():
                    if isinstance(analyzed_query, TxStartNode):
                        self.qp.call_remote(
                            destination="catalog", rpc_name="create_snapshot", params={"tx_id": analyzed_query.tx_id}
                        )
                    if isinstance(analyzed_query, TxEndNode):
                        if tx_id := (custom or {}).get("tx_id"):
                            self.qp.call_remote(
                                destination="catalog", rpc_name="release_snapshot", params={"tx_id": tx_id}
                            )
                    res = self.qp.call_remote(
                        destination="storagemanager",
                        rpc_name="execute_tcl",
                        params={"request": {"plan": analyzed_query.to_avro(), "custom": custom}},
                    )
                    reply = {"value": res["value"]}
                case _:
                    raise QueryProcessorError(f"No evaluator for node {type(analyzed_query).__name__!r}")

        except ComponentError:
            # Typed pass-through
            raise
        except Exception as e:
            raise QueryProcessorError(str(e), source=self.qp.name) from e
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
        self.bypass_catalog = bypass_catalog
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)

    def _create_responder(self, protocol: Protocol) -> avro.ipc.Responder:
        return QueryProcessorResponder(local_protocol=protocol, qp=self)
