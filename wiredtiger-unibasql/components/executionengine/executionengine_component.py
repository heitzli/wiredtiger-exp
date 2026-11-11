from __future__ import annotations

from typing import Any

import avro.ipc
from avro.protocol import Protocol

from components.queryprocessor.unibasql_algebra import AbstractAlgebraNode, Projection, Selection, TableScan

from ..component import Component
from ..errors import WiredTigerError
from .executor import ExecBuilder, ExecContext

# NOTE currently pass through from algebra through optimizer to exec engine


class ExecutionEngineResponder(avro.ipc.Responder):
    """Entry point: execute compiles the ExecTree; all stream FRAMING stays in the Component base."""

    def __init__(self, local_protocol: Protocol, engine: ExecutionEngine) -> None:
        super().__init__(local_protocol)
        self.engine = engine

    def invoke(self, local_message: Any, request: dict[str, Any]) -> Any:  # type: ignore[override]
        if local_message.name == "execute":
            return self.engine.execute_plan(request["request"])
        raise ValueError(f"Unhandled RPC {local_message.name!r} in executionengine")


class ExecutionEngine(Component):
    """Runs compiled ExecTrees in the pull chain: the SM sees only bare TableScans (other families
    forward verbatim); no cursors here — snapshot custody stays with the SM; disposal cascades
    via the tree's generator finally / abort closer."""

    def execute_plan(self, plan_request: dict[str, Any]) -> dict[str, Any]:
        """Compile the plan into an ExecTree and stream its root"""
        # TODO: properly translate also unions, inserts, updates into proper exec nodes
        plan = AbstractAlgebraNode.from_avro(plan_request["plan"])
        if not isinstance(plan, (TableScan, Selection, Projection)):
            # NOTE transitional: non-operator families (insert Union) execute on the SM verbatim
            try:
                reply = self.call_remote("storagemanager", "execute", {"request": plan_request})
            except Exception as e:
                # NOTE morph foreign RemoteErrors into the EE's OWN declared error type
                raise WiredTigerError(f"Upstream storagemanager execute failed: {e}") from e
            if reply.get("stream_id") is None:
                return {**reply, "stream_id": None}
            return {
                "value": reply.get("value") or {},
                "custom": None,
                "stream_id": self.open_stream_relay("storagemanager", reply),
            }

        # NOTE: exec TREE owns the stream
        try:
            tree = ExecBuilder(engine=self, custom=plan_request.get("custom"), ctx=ExecContext()).compile(plan)
            init_reply = tree.open()
        except WiredTigerError:
            raise
        except Exception as e:
            raise WiredTigerError(f"Failed to compile/execute plan in executionengine: {e}") from e
        stream_id = self.register_stream(tree.exec(), closer=tree.abort)
        self.log.info("exec tree stream opened", extra={"stream_id": stream_id, "table": tree.scan.table})
        return {"value": init_reply.get("value") or {}, "custom": None, "stream_id": stream_id}

    def _create_responder(self, protocol: Protocol) -> avro.ipc.Responder:
        return ExecutionEngineResponder(local_protocol=protocol, engine=self)
