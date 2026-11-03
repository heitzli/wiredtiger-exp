from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .expression import Expr, pretty

# NOTE: in this prototype no validation is performed on queries


class AbstractAlgebraNode(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    custom: dict[str, str] | None = None

    @abstractmethod
    def children(self) -> list[AbstractAlgebraNode]: ...

    @abstractmethod
    def label(self) -> str: ...

    def print_tree(self, indent=0):
        print("  " * indent + self.label())
        for child in self.children():
            child.print_tree(indent + 1)


class TableScan(AbstractAlgebraNode):
    kind: Literal["TableScan"] = "TableScan"
    table: str

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"TableScan({self.table})"


class Projection(AbstractAlgebraNode):
    kind: Literal["Projection"] = "Projection"
    attributes: tuple[ResolvedColumn, ...]
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


class Cross(AbstractAlgebraNode):
    kind: Literal["Cross"] = "Cross"
    left: AbstractAlgebraNode
    right: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.left, self.right]

    def label(self) -> str:
        return "X"


class Union(AbstractAlgebraNode):
    kind: Literal["Union"] = "Union"
    left: AbstractAlgebraNode
    right: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.left, self.right]

    def label(self) -> str:
        return "U"


class Difference(AbstractAlgebraNode):
    kind: Literal["Difference"] = "Difference"
    left: AbstractAlgebraNode
    right: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.left, self.right]

    def label(self) -> str:
        return "-"
