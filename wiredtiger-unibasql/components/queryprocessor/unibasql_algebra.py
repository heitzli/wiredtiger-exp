from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .expression import Expr, pretty
from .unibasql_unresolved import AllowedValue, ColumnConstraint, DataType


class ResolvedValue(BaseModel):
    kind: Literal["ResolvedValue"] = "ResolvedValue"
    type: DataType
    value: AllowedValue

    def __str__(self) -> str:
        return f"{self.value}"

    def __repr__(self) -> str:
        return f"{self.value}"


class ResolvedTable(BaseModel):
    kind: Literal["ResolvedTable"] = "ResolvedTable"
    name: str
    table_schema: tuple[ResolvedColumn, ...]
    alias: str | None = None

    @property
    def qualifier(self) -> str:
        return self.alias or self.name


class ResolvedColumn(BaseModel):
    kind: Literal["ResolvedColumn"] = "ResolvedColumn"
    name: str
    type: DataType
    constraint: ColumnConstraint | None = None
    qualifier: str | None = None


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


class AlgebraNode(AbstractAlgebraNode):
    """
    A generic class that can be used to implement any of the existing and potential new algebra nodes.
    """

    kind: str
    table: ResolvedTable | None = None
    values: list[tuple[ResolvedValue, ...]] | None = None
    columns: tuple[ResolvedColumn, ...] | None = None
    expr: Expr | None = None
    left: AbstractAlgebraNode | None = None
    right: AbstractAlgebraNode | None = None
    assignments: tuple[tuple[ResolvedColumn, Expr], ...] | None = None

    def children(self) -> list[AbstractAlgebraNode]:
        children = []
        if self.left:
            children.append(self.left)
        if self.right:
            children.append(self.right)

        return children

    def label(self) -> str:
        return f"{self.kind}"


class TableScan(AbstractAlgebraNode):
    kind: Literal["TableScan"] = "TableScan"
    table: ResolvedTable

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        if self.table.alias:
            return f"TableScan({self.table.name} AS {self.table.alias})"
        return f"TableScan({self.table.name})"


class VirtualTableScan(AbstractAlgebraNode):
    kind: Literal["VirtualTableScan"] = "VirtualTableScan"
    values: list[tuple[ResolvedValue, ...]]

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"VirtualTableScan({len(self.values)} insert tuples {self.values[0]})"


class Projection(AbstractAlgebraNode):
    kind: Literal["Projection"] = "Projection"
    columns: tuple[ResolvedColumn, ...]  # NOTE: star is expanded to all columns present in table
    child: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.child]

    def label(self) -> str:
        projection_str = ", ".join(f"{col.qualifier}.{col.name}" for col in self.columns)
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
        return "CROSS"


class Union(AbstractAlgebraNode):
    kind: Literal["Union"] = "Union"
    left: AbstractAlgebraNode
    right: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.left, self.right]

    def label(self) -> str:
        return "UNION"


class Difference(AbstractAlgebraNode):
    kind: Literal["Difference"] = "Difference"
    left: AbstractAlgebraNode
    right: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.left, self.right]

    def label(self) -> str:
        return "DIFFERENCE"


# NOTE TupleUpdate transforms each input tuple by evaluating assignment expressions
# and replacing specified attribute values while preserving all other
# attributes and the relation's cardinality. It provides a reusable mechanism
# for UPDATE ops and can later support computed attributes, data
# transformations, normalization, and other tuple-wise ops without
# introducing dedicated operators for each use case.
# Seperated from more general ExtendedProjection
# NOTE this is very relational specific and might not generalize well over different datamodels
class TupleUpdate(AbstractAlgebraNode):
    kind: Literal["TupleUpdate"] = "TupleUpdate"

    assignments: tuple[tuple[ResolvedColumn, Expr], ...]
    child: AbstractAlgebraNode

    def children(self) -> list[AbstractAlgebraNode]:
        return [self.child]

    def label(self) -> str:
        assignments = ", ".join(f"{column.name} := {pretty(expr)}" for column, expr in self.assignments)
        return f"TupleUpdate({assignments})"
