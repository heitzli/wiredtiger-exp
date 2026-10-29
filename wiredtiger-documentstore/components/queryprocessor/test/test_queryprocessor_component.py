"""Component-level tests for the query processor: evaluate wiring with a stub storage manager.

The stub is a real Component registered at the broker under the storagemanager
name, so QueryProcessor.call_remote runs the full RPC path, while the storage
handler itself is stubbed out.
"""

import threading
from types import SimpleNamespace

import pytest
from avro.ipc import Responder
from avro.protocol import Protocol

from components.component import Component
from components.conftest import BROKER_SOCKET
from components.errors import DocQLSyntaxError, QueryProcessorError


class StubStorageManager(Component):
    """Registered as 'storagemanager'; captures execute requests and returns canned responses."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests: list[dict] = []
        self.responses: list[dict] = []

    def _create_responder(self, protocol: Protocol) -> Responder:
        stub = self

        class _Responder(Responder):
            def invoke(self, local_message, request):  # type: ignore[override]
                stub.requests.append(request)
                return stub.responses[-1] if stub.responses else {"value": {}}

        return _Responder(protocol)


@pytest.fixture
def query_processor_with_stub(broker, query_processor, project_root, socket_dir):
    stub = StubStorageManager(
        name="storagemanager",
        broker_protocol_path=project_root / "protocol" / "broker.avpr",
        protocol_path=project_root / "protocol" / "storagemanager.avpr",
        broker_socket=BROKER_SOCKET,
        component_socket=str(socket_dir / "stub_storagemanager.sock"),
    )
    threading.Thread(target=stub.run, daemon=True).start()
    try:
        yield SimpleNamespace(query_processor=query_processor, stub=stub)
    finally:
        stub.shutdown()
        stub.close()
        stub.sock.close()


def invoke_evaluate(qp, query: str):
    return qp.responder.invoke(qp.protocol.messages["evaluate"], {"request": {"query": query}})


def test_evaluate_sends_converted_plan_to_storage_manager(query_processor_with_stub):
    qp, stub = query_processor_with_stub.query_processor, query_processor_with_stub.stub

    res = invoke_evaluate(qp, "CREATE TABLE t42;")

    assert res == {"value": {}}
    assert len(stub.requests) == 1
    plan = stub.requests[0]["request"]["plan"]
    assert plan["node"] == {"kind": "CreateTable", "table": "t42", "custom": None}


def test_evaluate_serializes_literal_types_for_insert(query_processor_with_stub):
    ns = query_processor_with_stub
    ns.stub.responses.append({"value": {"doc1": {"type": "docid", "value": b"1234"}}})

    res = invoke_evaluate(ns.query_processor, "INSERT INTO t VALUES ({'a': 1})")

    assert res == {"value": {"doc1": {"type": "docid", "value": b"1234"}}}
    plan = ns.stub.requests[0]["request"]["plan"]
    assert plan["node"]["kind"] == "Insert"
    assert plan["node"]["values"] == [{"a": {"kind": "LiteralExpr", "type": "int", "value": 1}}]


def test_evaluate_propagates_docql_syntax_error(query_processor_with_stub):
    qp = query_processor_with_stub.query_processor

    with pytest.raises(DocQLSyntaxError, match="Syntax error"):
        invoke_evaluate(qp, "this is $not docql")

    # the storage manager must never be called for an unparseable query
    assert query_processor_with_stub.stub.requests == []


def test_evaluate_wraps_unexpected_storage_errors_into_query_processor_error(query_processor, wt_home):
    """No storage manager is registered, so the RPC setup fails and is wrapped."""
    with pytest.raises(QueryProcessorError):
        invoke_evaluate(query_processor, "CREATE TABLE t;")
