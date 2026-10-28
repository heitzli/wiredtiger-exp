from abc import ABC
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict

AllowedValue: TypeAlias = str | int | float | None


class Expr(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    custom: dict[str, str] | None = None


def pretty(expr: Expr) -> str:
    if isinstance(expr, AttributeRef):
        return str(expr)

    if isinstance(expr, LiteralExpr):
        return repr(expr.value)

    if isinstance(expr, BinOp):
        return f"({pretty(expr.left)} {expr.op} {pretty(expr.right)})"

    if isinstance(expr, UnOp):
        return f"({expr.op} {pretty(expr.expr)})"

    raise TypeError(f"Unknown expression type: {type(expr).__name__}")


class AttributeRef(Expr):
    kind: Literal["AttributeRef"] = "AttributeRef"
    name: str

    def __str__(self) -> str:
        return self.name


class LiteralExpr(Expr):
    kind: Literal["LiteralExpr"] = "LiteralExpr"
    value: AllowedValue


class BinOp(Expr):
    kind: Literal["BinOp"] = "BinOp"
    op: str
    left: Expr
    right: Expr


class UnOp(Expr):
    kind: Literal["UnOp"] = "UnOp"
    op: str
    expr: Expr
