from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

import avro.io
import avro.schema
from pydantic import BaseModel, ConfigDict

from .unibasql_algebra import ResolvedColumn, ResolvedTable
from .unibasql_unresolved import ColumnConstraint, DataType, DDLOp


class AbstractDDLNode(BaseModel, ABC):
    """Mirrors the algebra node hierarchy (same modeling/traversal), but for DDL statements;
    deliberately kept separate from the relational operators despite the overlap."""

    model_config = ConfigDict(frozen=True)
    custom: dict[str, str] = dict()
    # TODO in the future each component will have their own protocol folder for the moment ref the "global proto folder"
    _DDL_NODE_SCHEMA: ClassVar[avro.schema.Schema] = avro.schema.parse(
        (Path(__file__).resolve().parents[2] / "protocol" / "ddlnode.avsc").read_text(encoding="utf-8")
    )

    @abstractmethod
    def children(self) -> list[AbstractDDLNode]: ...

    @abstractmethod
    def label(self) -> str: ...

    def print_tree(self, indent=0):
        """Print this statement's subtree, one indented label per line."""

        print("  " * indent + self.label())
        for child in self.children():
            child.print_tree(indent + 1)

    def to_avro(self) -> dict[str, Any]:
        """Render this statement as the plain dict matching the avro DDLNode record."""
        return self._node_to_avro()

    @classmethod
    def from_avro(cls, datum: Mapping[str, Any]) -> AbstractDDLNode:
        """Rebuild a statement from a dict matching the avro DDLNode record."""
        return cls._node_from_avro(datum)

    def _node_to_avro(self) -> dict[str, Any]:
        """Python DDL statement -> plain dict matching the avro DDLNode record."""
        # NOTE: prefill every record field (all have avro defaults) so DatumWriter always finds a value.
        datum: dict[str, Any] = {
            "kind": self.kind,
            "table": None,
            "op": None,
            "idx_name": None,
            "columns": None,
            "custom": self.custom,
        }
        match self:
            case TableDDL():
                datum["table"] = self._resolved_table_to_avro(self.table)
                datum["op"] = self.op.name
            case IndexDDL():
                datum["op"] = self.op.name
                datum["idx_name"] = self.idx_name
                datum["table"] = self._resolved_table_to_avro(self.table)
                if self.columns is not None:
                    datum["columns"] = self._columns_to_avro(self.columns)
            case DDLNode():
                if self.table is not None:
                    datum["table"] = self._resolved_table_to_avro(self.table)
                if self.op is not None:
                    datum["op"] = self.op.name
                if self.idx_name is not None:
                    datum["idx_name"] = self.idx_name
                if self.columns is not None:
                    datum["columns"] = self._columns_to_avro(self.columns)
            case _:
                raise ValueError(f"Cannot serialize DDL statement of kind {self.kind!r}")
        return datum

    def _resolved_column_to_avro(self, column: ResolvedColumn) -> dict[str, Any]:
        """ResolvedColumn -> matching Avro record datum."""

        return {
            "kind": "ResolvedColumn",
            "name": column.name,
            "type": column.type.name,
            "constraint": column.constraint.name if column.constraint is not None else None,
            "qualifier": column.qualifier,
            "custom": None,
        }

    def _resolved_table_to_avro(self, table: ResolvedTable) -> dict[str, Any]:
        """ResolvedTable -> matching Avro record datum."""

        return {
            "kind": "ResolvedTable",
            "name": table.name,
            # NOTE: avro spelling is camelCase tableSchema; the python field is table_schema.
            "tableSchema": [self._resolved_column_to_avro(column) for column in table.table_schema],
            "alias": table.alias,
            "custom": None,
        }

    def _columns_to_avro(self, columns: tuple[ResolvedColumn, ...]) -> list[dict[str, Any]]:
        """Sequence of ResolvedColumns -> list of Avro datums."""

        return [self._resolved_column_to_avro(column) for column in columns]

    @classmethod
    def _node_from_avro(cls, datum: Mapping[str, Any]) -> AbstractDDLNode:
        """Plain dict matching the avro DDLNode record -> Python DDL statement."""
        custom = datum.get("custom")
        match datum["kind"]:
            case "TableDDL":
                return TableDDL(
                    op=DDLOp[datum["op"]],
                    table=cls._resolved_table_from_avro(datum["table"]),
                    custom=custom,
                )
            case "IndexDDL":
                return IndexDDL(
                    op=DDLOp[datum["op"]],
                    idx_name=datum["idx_name"],
                    table=cls._resolved_table_from_avro(datum["table"]),
                    columns=cls._columns_from_avro(datum["columns"]) if datum.get("columns") is not None else None,
                    custom=custom,
                )
            case _:
                # NOTE: forward compatibility — a reader that does not know a kind still receives
                # the statement as a generic DDLNode instead of failing the whole decode.
                return DDLNode(
                    kind=datum["kind"],
                    op=DDLOp[datum["op"]] if datum.get("op") is not None else None,
                    table=cls._resolved_table_from_avro(datum["table"]) if datum.get("table") is not None else None,
                    idx_name=datum.get("idx_name"),
                    columns=cls._columns_from_avro(datum["columns"]) if datum.get("columns") is not None else None,
                    custom=custom,
                )

    @classmethod
    def _resolved_column_from_avro(cls, datum: Mapping[str, Any]) -> ResolvedColumn:
        """Avro record datum -> ResolvedColumn."""

        return ResolvedColumn(
            name=datum["name"],
            # NOTE: enums commute via their symbol names (avro STRING <-> python DataType.STRING).
            type=DataType[datum["type"]],
            constraint=ColumnConstraint[datum["constraint"]] if datum["constraint"] is not None else None,
            qualifier=datum["qualifier"],
        )

    @classmethod
    def _resolved_table_from_avro(cls, datum: Mapping[str, Any]) -> ResolvedTable:
        """Avro record datum -> ResolvedTable."""

        return ResolvedTable(
            name=datum["name"],
            # NOTE: avro spelling is camelCase tableSchema; the python field is table_schema.
            table_schema=tuple(cls._resolved_column_from_avro(column) for column in datum["tableSchema"]),
            alias=datum["alias"],
        )

    @classmethod
    def _columns_from_avro(cls, columns: list) -> tuple[ResolvedColumn, ...]:
        """List of Avro datums -> sequence of ResolvedColumns."""

        return tuple(cls._resolved_column_from_avro(column) for column in columns)


class DDLNode(AbstractDDLNode):
    """Generic DDL statement with no fixed shape; also the forward-compatible decode fallback."""

    kind: str
    op: DDLOp | None = None
    table: ResolvedTable | None = None
    idx_name: str | None = None
    columns: tuple[ResolvedColumn, ...] | None = None

    def children(self) -> list[AbstractDDLNode]:
        return []

    def label(self) -> str:
        return f"{self.kind}"


class TableDDL(AbstractDDLNode):
    """Typed CREATE TABLE / DROP TABLE statement."""

    kind: Literal["TableDDL"] = "TableDDL"
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
    """Typed CREATE INDEX / DROP INDEX statement."""

    kind: Literal["IndexDDL"] = "IndexDDL"
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
