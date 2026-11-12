from abc import ABC
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict

AllowedValue: TypeAlias = str | int | float | bool | None


class Expr(BaseModel, ABC):
    """Abstract base of the expression IR (frozen AST node types)."""

    model_config = ConfigDict(frozen=True)
    custom: dict[str, str] | None = None


def pretty(expr: Expr) -> str:
    """Render an expression tree as parenthesized infix text."""
    if isinstance(expr, ColumnRef):
        return str(expr)

    if isinstance(expr, LiteralExpr):
        return repr(expr.value)

    if isinstance(expr, BinOp):
        return f"({pretty(expr.left)} {expr.op} {pretty(expr.right)})"

    if isinstance(expr, UnOp):
        return f"({expr.op} {pretty(expr.expr)})"

    raise TypeError(f"Unknown expression type: {type(expr).__name__}")


class LiteralExpr(Expr):
    """A typed literal value."""

    kind: Literal["LiteralExpr"] = "LiteralExpr"
    type: str
    value: AllowedValue


class BinOp(Expr):
    """Binary operation over two subexpressions."""

    kind: Literal["BinOp"] = "BinOp"
    op: str
    left: Expr
    right: Expr


class UnOp(Expr):
    """Unary operation over one subexpression."""

    kind: Literal["UnOp"] = "UnOp"
    op: str
    expr: Expr


class ColumnRef(Expr):
    """Reference to a column, optionally qualified by a table/alias."""

    kind: Literal["ColumnRef"] = "ColumnRef"
    name: str
    qualifier: str | None = None

    def __str__(self) -> str:
        if self.qualifier:
            return f"{self.qualifier}.{self.name}"
        return self.name


class TableRef(Expr):
    """Reference to a table, optionally aliased."""

    kind: Literal["TableRef"] = "TableRef"
    name: str
    alias: str | None = None
