from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .expression import Expr, LiteralExpr, pretty

# NOTE: in this prototype no validation is performed on queries


@dataclass(frozen=True)
class Attribute(BaseModel):
    name: str


class AbstractAlgebraNode(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def children(self) -> list[AbstractAlgebraNode]: ...

    @abstractmethod
    def label(self) -> str: ...

    def print_tree(self, indent=0):
        print("  " * indent + self.label())
        for child in self.children():
            child.print_tree(indent + 1)


# +++ optional AlgebraNodes
# NOTE: check proper algebras how they treat this kind of "nodes", currently simple to just add different types of nodes
class CreateTable(AbstractAlgebraNode):
    kind: Literal["CreateTable"] = "CreateTable"
    table: str

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"CreateTable(name={self.table}"


class DeleteTable(AbstractAlgebraNode):
    kind: Literal["DeleteTable"] = "DeleteTable"
    table: str

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"DeleteTable(name={self.table}"


class Delete(AbstractAlgebraNode):
    kind: Literal["Delete"] = "Delete"
    table: str
    expr: Expr | None = None

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        if self.expr is not None:
            expr = pretty(self.expr)
            return f"Delete(table={self.table}, expr={expr})"
        return f"Delete(table={self.table})"


class Insert(AbstractAlgebraNode):
    kind: Literal["Insert"] = "Insert"
    table: str
    values: list[dict[str, LiteralExpr]]

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        # FIXME dont print all values
        return f"Insert(table={self.table},values={self.values})"


class Update(AbstractAlgebraNode):
    kind: Literal["Update"] = "Update"
    table: str
    kv: list[tuple[str, LiteralExpr]]
    expr: Expr | None = None

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        if self.expr is not None:
            expr = pretty(self.expr)
            return f"Update(table={self.table}, expr={expr}, kv={self.kv})"
        return f"Update(table={self.table}, kv={self.kv})"


# ---


class TableScan(AbstractAlgebraNode):
    kind: Literal["TableScan"] = "TableScan"
    table: str

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"TableScan({self.table})"


class Projection(AbstractAlgebraNode):
    kind: Literal["Projection"] = "Projection"
    attributes: tuple[Attribute, ...]
    child: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.child]

    def label(self) -> str:
        projection_str = ", ".join(f"{col.name}" for col in self.attributes)
        return f"π[{projection_str}]"


class Selection(AbstractAlgebraNode):
    kind: Literal["Selection"] = "Selection"
    expr: Expr
    child: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.child]

    def label(self) -> str:
        return f"σ[{pretty(self.expr)}]"
