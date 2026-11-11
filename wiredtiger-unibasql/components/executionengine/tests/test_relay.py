"""EE component-logic tests (no sockets): exec-tree compile + remote-leaf contract; base stream
framing coverage lives in components/tests/test_component_stream_layer.py.
"""

import logging

import pytest

from components.errors import WiredTigerError
from components.executionengine.executionengine_component import ExecutionEngine
from components.queryprocessor.expression import BinOp, ColumnRef, LiteralExpr
from components.queryprocessor.unibasql_algebra import (
    ResolvedColumn,
    ResolvedTable,
    Selection,
    TableScan,
    Union,
)
from components.queryprocessor.unibasql_unresolved import ColumnConstraint, DataType

TABLE_SCHEMA = (
    ResolvedColumn(name="id", type=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY, qualifier="t"),
    ResolvedColumn(name="name", type=DataType.STRING, qualifier="t"),
)


def _scan_datum() -> dict:
    return TableScan(table=ResolvedTable(name="t", table_schema=TABLE_SCHEMA)).to_avro()


PLAN_REQUEST = {"plan": _scan_datum(), "custom": {}}


def _engine(fake_call) -> ExecutionEngine:
    """Component instance without __init__; only relay collaborators attached. The scripted
    peer gets a `calls` attribute so tests can assert on the exact wire interactions."""
    eng = object.__new__(ExecutionEngine)
    eng.log = logging.getLogger("test-ee")
    eng.log.setLevel(logging.CRITICAL)

    fake_call.calls = []
    orig = fake_call

    def recorded(dst, rpc, params):
        fake_call.calls.append((rpc, params))
        return orig(dst, rpc, params)

    eng.call_remote = recorded
    from components.stream_registry import StreamRegistry

    eng.registry = StreamRegistry()
    eng.stream_page_cap = 4096
    eng.stream_relay_page = 256
    eng.stream_ttl_seconds = 60.0
    return eng


def test_stream_reply_adopts_header_and_registers_the_tree_root():
    pages = [{"rows": [[0]], "done": False}, {"rows": [[1]], "done": True}]

    def fake(dst, rpc, params):
        if rpc == "execute":
            return {"value": {"columns": "c:INT"}, "stream_id": "sm-1"}
        return pages.pop(0)

    eng = _engine(fake)
    reply = eng.execute_plan(PLAN_REQUEST)
    # init-reply header passes through untouched; upstream sid consumed invisibly
    assert reply["value"] == {"columns": "c:INT"}
    assert reply["stream_id"] != "sm-1"
    assert len(eng.registry) == 1

    page = eng._serve_stream_next({"stream_id": reply["stream_id"], "max_items": 1})
    assert (page["rows"], page["done"]) == ([[0]], False)
    page = eng._serve_stream_next({"stream_id": reply["stream_id"], "max_items": 5})
    assert page["done"] is True
    assert fake.calls[-1] == ("stream_close", {"stream_id": "sm-1"})  # cascade


def test_wrapped_plan_executes_operators_over_the_paged_stream():
    # Selection replay lives in the EE exec tree — the SM never sees the operator
    plan = Selection(
        expr=BinOp(op="=", left=ColumnRef(name="id", qualifier="t"), right=LiteralExpr(type="AUTO", value=1)),
        child=TableScan(table=ResolvedTable(name="t", table_schema=TABLE_SCHEMA)),
    ).to_avro()
    pages = [{"rows": [[0], [1]], "done": False}, {"rows": [[2]], "done": True}]

    def fake(dst, rpc, params):
        if rpc == "execute":
            return {"value": {"columns": "id:INT"}, "stream_id": "sm-1"}
        return pages.pop(0)

    eng = _engine(fake)
    reply = eng.execute_plan({"plan": plan, "custom": {}})
    # quota 1 is satisfied by filtering page 1 ([0,1] -> [1]); page 2 is NOT yet pulled (lazy)
    page = eng._serve_stream_next({"stream_id": reply["stream_id"], "max_items": 1})
    assert (page["rows"], page["done"]) == ([[1]], False)
    page = eng._serve_stream_next({"stream_id": reply["stream_id"], "max_items": 10})
    assert page["done"] is True

    # the upstream saw the bare scan, not the wrapped plan
    initiate = next(p for rpc, p in fake.calls if rpc == "execute")
    assert initiate["request"]["plan"]["kind"] == "TableScan"


def test_scan_without_stream_id_is_a_typed_error():
    def fake(dst, rpc, params):
        return {"value": {"status": "ok"}, "stream_id": None}

    eng = _engine(fake)
    with pytest.raises(WiredTigerError, match="did not open a stream"):
        eng.execute_plan(PLAN_REQUEST)
    assert len(eng.registry) == 0  # nothing registered half-open


def test_upstream_execute_failure_morphs_into_own_wiredtigererror():
    def fake(dst, rpc, params):
        raise RuntimeError("RemoteError (storagemanager): boom")

    eng = _engine(fake)
    with pytest.raises(WiredTigerError, match="Upstream storagemanager execute failed: .*boom") as excinfo:
        eng.execute_plan(PLAN_REQUEST)
    # chained for diagnosis, but the SURFACE type is the EE's own declared one
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_foreign_family_forwarded_verbatim_without_compile():
    # insert-style plans (Union of scans) reach the SM verbatim — compile() takes scans/operators only
    scan = TableScan(table=ResolvedTable(name="t", table_schema=TABLE_SCHEMA))
    plan_request = {"plan": Union(left=scan, right=scan).to_avro(), "custom": {"tx_id": "t1"}}

    def fake(dst, rpc, params):
        return {"value": {"status": "inserted"}, "stream_id": None}

    eng = _engine(fake)
    assert eng.execute_plan(plan_request) == {"value": {"status": "inserted"}, "stream_id": None}
    assert fake.calls[-1][1] == {"request": plan_request}  # wire payload preserved verbatim
