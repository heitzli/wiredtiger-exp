from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from ..errors import WiredTigerError
from ..queryprocessor.expression import Expr
from ..queryprocessor.unibasql_algebra import (
    AbstractAlgebraNode,
    BinOp,
    ColumnRef,
    LiteralExpr,
    Projection,
    Selection,
    TableScan,
    UnOp,
)
from ..queryprocessor.unibasql_unresolved import DataType
from ..remote_stream import RemoteStream
from ..storagemanager import row_codec

if TYPE_CHECKING:
    from ..component import Component


class ExecContext:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.dtypes: list[DataType] = []


class ExecNode(ABC):
    @abstractmethod
    def exec(self) -> Iterator[list[Any]]: ...


class TableScanExecNode(ExecNode):
    def __init__(self, engine: Component, scan: TableScan, custom: dict[str, str] | None, ctx: ExecContext) -> None:
        self.engine = engine
        self.table = scan.table.name
        self.scan = scan
        self.custom = custom
        self.ctx = ctx
        self.stream: RemoteStream | None = None
        self.reply_value: dict[str, Any] = {}

    @property
    def upstream_sid(self) -> str | None:
        return self.stream.stream_id if self.stream is not None else None

    def open(self) -> dict[str, Any]:
        try:
            reply = self.engine.call_remote(
                "storagemanager",
                "execute",
                {"request": {"plan": self.scan.to_avro(), "custom": self.custom}},
            )
        except WiredTigerError:
            raise
        except Exception as e:
            raise WiredTigerError(f"Upstream storagemanager execute failed: {e}") from e
        if reply.get("stream_id") is None:
            raise WiredTigerError("Storagemanager scan did not open a stream")
        self.stream = RemoteStream(self.engine, "storagemanager", reply, self.engine.stream_relay_page)
        self.ctx.names, self.ctx.dtypes = row_codec.parse_header(self.stream.header or "")
        self.reply_value = reply.get("value") or {}
        return reply

    def exec(self) -> Iterator[list[Any]]:
        if self.stream is None:
            raise WiredTigerError("TableScanExecNode used before open()")
        try:
            # the RemoteStream closes itself on exhaustion/GC; unwire into the value domain here
            for wire_row in self.stream:
                yield [row_codec.unwire_cell(dtype, cell) for dtype, cell in zip(self.ctx.dtypes, wire_row)]
        except WiredTigerError:
            raise
        except Exception as e:
            raise WiredTigerError(f"Upstream storagemanager stream failed: {e}") from e


class SelectionExecNode(ExecNode):
    def __init__(self, expr: Expr, child: ExecNode, ctx: ExecContext) -> None:
        self.expr = expr
        self.child = child
        self.ctx = ctx

    def exec(self) -> Iterator[list[Any]]:
        for row in self.child.exec():
            if satisfies(self.expr, dict(zip(self.ctx.names, row))):
                yield row


class ProjectionExecNode(ExecNode):
    def __init__(self, projection: Projection, child: ExecNode, ctx: ExecContext) -> None:
        self.projection = projection
        self.child = child
        self.ctx = ctx

    def exec(self) -> Iterator[list[Any]]:
        for row in self.child.exec():
            yield project_row(row, self.ctx.names, self.projection)


class ExecBuilder:
    """Compiles the plan bottom-up into an ExecTree."""

    def __init__(self, engine: Component, custom: dict[str, str] | None, ctx: ExecContext) -> None:
        self.engine = engine
        self.custom = custom
        self.ctx = ctx

    def compile(self, node: AbstractAlgebraNode) -> ExecTree:
        root = self._build(node)
        scan = root
        while not isinstance(scan, TableScanExecNode):
            scan = scan.child  # type: ignore[attr-directed] — operator nodes chain downward
        return ExecTree(root=root, scan=scan)

    def _build(self, node: AbstractAlgebraNode) -> ExecNode:
        match node:
            case TableScan():
                return TableScanExecNode(engine=self.engine, scan=node, custom=self.custom, ctx=self.ctx)
            case Selection():
                return SelectionExecNode(expr=node.expr, child=self._build(node.child), ctx=self.ctx)
            case Projection():
                return ProjectionExecNode(projection=node, child=self._build(node.child), ctx=self.ctx)
            case _:
                raise WiredTigerError(f"No executor for node kind {type(node).__name__!r} in executionengine")


class ExecTree:
    def __init__(self, root: ExecNode, scan: TableScanExecNode) -> None:
        self.root = root
        self.scan = scan

    def open(self) -> dict[str, Any]:
        return self.scan.open()

    def exec(self) -> Iterator[list[Any]]:
        for row in self.root.exec():
            yield [row_codec.wire_cell(dtype, value) for dtype, value in zip(self.scan.ctx.dtypes, row)]

    def abort(self) -> None:
        # covers the never-started-generator corner; RemoteStream.close is idempotent, so this
        # races harmlessly with the leaf's iteration finalizer
        if self.scan.stream is not None:
            self.scan.stream.close()


def _comparison(op: str, left: object, right: object) -> bool:
    if left is None or right is None:
        return False
    match op:
        case "=" | "==":
            return bool(left == right)
        case "!=" | "<>":
            return bool(left != right)
        case "<":
            return bool(left < right)
        case ">":
            return bool(left > right)
        case "<=":
            return bool(left <= right)
        case ">=":
            return bool(left >= right)
    raise WiredTigerError(f"Unsupported comparison operator {op!r}")


def satisfies(expr: Expr | None, values: dict[str, object]) -> bool:
    # NOTE: NULL = false
    if expr is None:
        return True
    if isinstance(expr, LiteralExpr):
        return expr.value is not None and bool(expr.value)
    if isinstance(expr, ColumnRef):
        return bool(values.get(expr.name))
    if isinstance(expr, UnOp):
        if expr.op == "NOT":
            inner = expr.expr
            if isinstance(inner, BinOp):
                return _predicate_binop(inner, values) is False
            return not satisfies(inner, values)
        raise WiredTigerError(f"Unsupported unary operator {expr.op!r}")
    if isinstance(expr, BinOp):
        return _predicate_binop(expr, values)
    raise WiredTigerError(f"Unsupported expression {type(expr).__name__!r}")


def _predicate_binop(expr: BinOp, values: dict[str, object]) -> bool:
    match expr.op:
        case "AND":
            return satisfies(expr.left, values) and satisfies(expr.right, values)
        case "OR":
            return satisfies(expr.left, values) or satisfies(expr.right, values)
        case op if op in {"=", "==", "!=", "<>", "<", ">", "<=", ">="}:
            return _comparison(op, _cell(expr.left, values), _cell(expr.right, values))
    raise WiredTigerError(f"Unsupported operator {expr.op!r} in selection")


def _cell(operand: Expr, values: dict[str, object]) -> object:
    if isinstance(operand, ColumnRef):
        return values.get(operand.name)
    if isinstance(operand, LiteralExpr):
        return operand.value
    raise WiredTigerError(f"Unsupported operand {type(operand).__name__!r}")


def project_row(row: list[Any], names: list[str], projection: Projection | None) -> list[Any]:
    if projection is None:
        return row
    wanted = {col.name for col in projection.columns}
    if wanted != set(names):
        raise WiredTigerError("Column-subset projection requires tuple-header renegotiation (deferred)")
    return list(row)
