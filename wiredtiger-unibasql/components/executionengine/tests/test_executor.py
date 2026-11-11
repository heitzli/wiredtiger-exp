"""EE exec-tree unit tests (fake engine — no WiredTiger/sockets). NULL comparisons -> False;
unsupported/foreign shapes raise typed.
"""

import logging

import pytest

from components.errors import WiredTigerError
from components.executionengine import executor
from components.executionengine.executionengine_component import ExecutionEngine
from components.queryprocessor.expression import BinOp, ColumnRef, LiteralExpr, UnOp
from components.queryprocessor.unibasql_algebra import (
    Projection,
    ResolvedColumn,
    ResolvedTable,
    Selection,
    TableScan,
)
from components.queryprocessor.unibasql_unresolved import ColumnConstraint, DataType

TABLE_SCHEMA = (
    ResolvedColumn(name="id", type=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY, qualifier="t"),
    ResolvedColumn(name="name", type=DataType.STRING, qualifier="t"),
    ResolvedColumn(name="salary", type=DataType.DOUBLE, qualifier="t"),
    ResolvedColumn(name="active", type=DataType.BOOL, qualifier="t"),
)
HEADER = "id:INT,name:STRING,salary:DOUBLE,active:BOOL"


def _scan() -> TableScan:
    return TableScan(table=ResolvedTable(name="t", table_schema=TABLE_SCHEMA))


def _ref(name: str) -> ColumnRef:
    return ColumnRef(name=name, qualifier="t")


def _lit(value) -> LiteralExpr:
    return LiteralExpr(type="AUTO", value=value)


def _engine(fake_call):
    """Component instance without __init__: only the exec-tree collaborators (call_remote/log)."""
    eng = object.__new__(ExecutionEngine)
    eng.log = logging.getLogger("test-ee")
    eng.log.setLevel(logging.CRITICAL)
    fake_call.calls = []
    orig = fake_call

    def recorded(dst, rpc, params):
        fake_call.calls.append((rpc, params))
        return orig(dst, rpc, params)

    recorded.calls = fake_call.calls  # same list object — observable through the bound wrapper too
    eng.call_remote = recorded
    from components.stream_registry import StreamRegistry

    eng.registry = StreamRegistry()
    eng.stream_page_cap = 4096
    eng.stream_relay_page = 256
    eng.stream_ttl_seconds = 60.0
    return eng


def _stream_fake(pages: list[dict]):
    def fake(dst, rpc, params):
        if rpc == "execute":
            return {"value": {"columns": HEADER}, "stream_id": "sm-1"}
        return pages.pop(0)

    return fake


# --- builder --------------------------------------------------------------------------------------------


def test_builder_compiles_operator_chain_bottom_up():
    proj = Projection(columns=TABLE_SCHEMA, child=_scan())
    plan = Selection(expr=BinOp(op="=", left=_ref("id"), right=_lit(1)), child=proj)
    fake = _engine(_stream_fake([]))
    tree = executor.ExecBuilder(engine=fake, custom={}, ctx=executor.ExecContext()).compile(plan)
    assert isinstance(tree.root, executor.SelectionExecNode)
    assert isinstance(tree.root.child, executor.ProjectionExecNode)
    assert isinstance(tree.root.child.child, executor.TableScanExecNode)
    assert tree.scan.upstream_sid is None  # opened separately, on purpose


def test_builder_rejects_foreign_kinds_typed():
    with pytest.raises(WiredTigerError, match="No executor for node kind"):
        executor.ExecBuilder(engine=_engine(lambda *a: {}), custom={}, ctx=executor.ExecContext()).compile(
            BinOp(op="+", left=_lit(1), right=_lit(2))
        )


# --- leaf: remote scan ----------------------------------------------------------------------------------


def test_scan_leaf_pages_unwire_rows_and_close_on_exhaustion():
    source = _stream_fake([{"rows": [[1, "a", 2.5, 1]], "done": False}, {"rows": [], "done": True}])
    node = executor.TableScanExecNode(engine=_engine(source), scan=_scan(), custom={}, ctx=executor.ExecContext())
    node.open()
    # value domain: BOOL unwired 1 -> True; the SM stream gets closed once on exhaustion
    assert list(node.exec()) == [[1, "a", 2.5, True]]
    assert [(rpc, params.get("stream_id")) for rpc, params in source.calls if rpc != "execute"] == [
        ("stream_next", "sm-1"),
        ("stream_next", "sm-1"),
        ("stream_close", "sm-1"),
    ]


def test_scan_leaf_raises_when_used_before_open():
    node = executor.TableScanExecNode(
        engine=_engine(lambda *a: {}), scan=_scan(), custom={}, ctx=executor.ExecContext()
    )
    with pytest.raises(WiredTigerError, match="before open"):
        list(node.exec())


def test_scan_leaf_requires_a_stream_from_the_sm():
    def fake(dst, rpc, params):
        return {"value": {"status": "ok"}, "stream_id": None}

    node = executor.TableScanExecNode(engine=_engine(fake), scan=_scan(), custom={}, ctx=executor.ExecContext())
    with pytest.raises(WiredTigerError, match="did not open a stream"):
        node.open()


def test_scan_leaf_init_failure_morphs_into_own_error_type():
    def fake(dst, rpc, params):
        raise RuntimeError("RemoteError (storagemanager): boom")

    node = executor.TableScanExecNode(engine=_engine(fake), scan=_scan(), custom={}, ctx=executor.ExecContext())
    with pytest.raises(WiredTigerError, match="Upstream storagemanager execute failed: .*boom") as excinfo:
        node.open()
    assert isinstance(excinfo.value.__cause__, RuntimeError)


# --- operators over the tree ----------------------------------------------------------------------------


def test_selection_filters_via_header_names_over_paged_stream():
    plan = Selection(expr=BinOp(op="=", left=_ref("active"), right=_lit(True)), child=_scan())
    pages = [
        {"rows": [[0, "emp0", 0.0, 1], [1, "emp1", 1.5, 0]], "done": False},
        {"rows": [[2, "emp2", 3.0, 1]], "done": True},
    ]
    tree = executor.ExecBuilder(engine=_engine(_stream_fake(pages)), custom={}, ctx=executor.ExecContext()).compile(
        plan
    )
    tree.open()
    assert list(tree.exec()) == [[0, "emp0", 0.0, 1], [2, "emp2", 3.0, 1]]  # wire domain re-established


def test_selection_null_semantics_and_two_selections_stack():
    pred = BinOp(op=">", left=_ref("salary"), right=_lit(10.0))
    plan = Selection(
        expr=BinOp(op="AND", left=pred, right=UnOp(op="NOT", expr=BinOp(op="=", left=_ref("id"), right=_lit(2)))),
        child=_scan(),
    )
    pages = [{"rows": [[1, "a", None, 1], [2, "b", 10.5, 1], [3, "c", 11.0, 0]], "done": True}]  # NULL salary drops
    tree = executor.ExecBuilder(engine=_engine(_stream_fake(pages)), custom={}, ctx=executor.ExecContext()).compile(
        plan
    )
    tree.open()
    assert list(tree.exec()) == [[3, "c", 11.0, 0]]


def test_projection_full_tuple_is_identity():
    plan = Projection(columns=TABLE_SCHEMA, child=_scan())
    pages = [{"rows": [[7, "x", 1.0, 0]], "done": True}]
    tree = executor.ExecBuilder(engine=_engine(_stream_fake(pages)), custom={}, ctx=executor.ExecContext()).compile(
        plan
    )
    tree.open()
    assert list(tree.exec()) == [[7, "x", 1.0, 0]]


def test_projection_subset_is_rejected_loudly_until_headers_negotiate():
    plan = Projection(columns=TABLE_SCHEMA[:2], child=_scan())
    pages = [{"rows": [[1, "a", 2.0, 1]], "done": True}]
    tree = executor.ExecBuilder(engine=_engine(_stream_fake(pages)), custom={}, ctx=executor.ExecContext()).compile(
        plan
    )
    tree.open()
    with pytest.raises(WiredTigerError, match="renegotiation"):
        list(tree.exec())


# --- lifecycle ------------------------------------------------------------------------------------------


def test_early_disposal_of_started_tree_cascades_to_the_sm_exactly_once():
    plan = Selection(expr=BinOp(op="=", left=_ref("id"), right=_lit(1)), child=_scan())
    pages = [{"rows": [[0], [1]], "done": False}, {"rows": [[2]], "done": True}]
    source = _stream_fake(pages)
    eng = _engine(source)
    tree = executor.ExecBuilder(engine=eng, custom={}, ctx=executor.ExecContext()).compile(plan)
    tree.open()
    sid = eng.register_stream(tree.exec(), closer=tree.abort)

    eng.registry.next(stream_id=sid, max_items=1)  # starts the generator; page 1 pulled
    eng._serve_stream_close({"stream_id": sid})  # registry: generator close (finally) + closer (no-op now)
    assert [(rpc, params.get("stream_id")) for rpc, params in source.calls if rpc == "stream_close"] == [
        ("stream_close", "sm-1")
    ]


def test_abort_covers_never_started_registration():
    plan = Selection(expr=BinOp(op="=", left=_ref("id"), right=_lit(1)), child=_scan())
    source = _stream_fake([])
    eng = _engine(source)
    tree = executor.ExecBuilder(engine=eng, custom={}, ctx=executor.ExecContext()).compile(plan)
    tree.open()
    sid = eng.register_stream(tree.exec(), closer=tree.abort)

    # a generator that was never next()-ed runs no finally on close() — the closer must cascade
    eng._serve_stream_close({"stream_id": sid})
    assert [(rpc, params.get("stream_id")) for rpc, params in source.calls if rpc == "stream_close"] == [
        ("stream_close", "sm-1")
    ]


def test_lazy_pull_quota_one_does_not_fetch_page_two():
    plan = Selection(expr=BinOp(op="=", left=_ref("id"), right=_lit(1)), child=_scan())
    pages = [{"rows": [[0], [1]], "done": False}, {"rows": [[2]], "done": True}]
    source = _stream_fake(pages)
    eng = _engine(source)
    tree = executor.ExecBuilder(engine=eng, custom={}, ctx=executor.ExecContext()).compile(plan)
    tree.open()
    sid = eng.register_stream(tree.exec())
    rows, done = eng.registry.next(stream_id=sid, max_items=1)
    assert (rows, done) == ([[1]], False)
    assert sum(1 for rpc, _ in source.calls if rpc == "stream_next") == 1
