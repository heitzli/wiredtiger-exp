from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from .unibasql_algebra import ResolvedColumn, ResolvedTable
from .unibasql_unresolved import DDLOp


# NOTE seperate algebra from ddl but effectively the same
class AbstractDDLNode(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    custom: dict[str, str] | None = None

    @abstractmethod
    def children(self) -> list[AbstractDDLNode]: ...

    @abstractmethod
    def label(self) -> str: ...

    def print_tree(self, indent=0):
        print("  " * indent + self.label())
        for child in self.children():
            child.print_tree(indent + 1)


class TableDDL(AbstractDDLNode):
    op: DDLOp
    table: ResolvedTable
    # columns: list[ResolvedTableDefTriple] | None = None

    def children(self) -> list[AbstractDDLNode]:
        return []

    def label(self) -> str:
        if self.op == DDLOp.CREATE:
            return f"CREATE TABLE {self.table.name} {tuple((c.name, str(c.type), str(c.constraint)) for c in self.table.table_schema)}"
        return f"DROP TABLE {self.table.name}"


class IndexDDL(AbstractDDLNode):
    op: DDLOp
    idx_name: str
    table: ResolvedTable
    columns: tuple[ResolvedColumn, ...] | None = None

    def children(self) -> list[AbstractDDLNode]:
        return []

    def label(self) -> str:
        if self.op == DDLOp.CREATE:
            return f"CREATE INDEX {self.idx_name} ON {self.table.name}.{tuple(c.name for c in self.columns)}"  # type: ignore
        return f"DROP INDEX {self.idx_name} FROM {self.table.name}"
