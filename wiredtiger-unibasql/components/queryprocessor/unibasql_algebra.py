from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

import avro.io
import avro.schema
from pydantic import BaseModel, ConfigDict

from .expression import BinOp, ColumnRef, Expr, LiteralExpr, TableRef, UnOp, pretty
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
    custom: dict[str, str] = dict()

    # TODO in the future each component will have their own protocol folder for the moment ref the "global proto folder"
    _ALGEBRA_NODE_SCHEMA: ClassVar[avro.schema.Schema] = avro.schema.parse(
        (Path(__file__).resolve().parents[2] / "protocol" / "algebranode.avsc").read_text(encoding="utf-8")
    )
    _AVRO_NODE_KINDS: ClassVar[dict[str, type]] = {}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # kinds self-register here, depends on pydantic
        kind_field = cls.model_fields.get("kind", None)
        if kind_field is not None and isinstance(kind_field.default, str):
            cls._AVRO_NODE_KINDS[kind_field.default] = cls

    @abstractmethod
    def child_nodes(self) -> list[AbstractAlgebraNode]: ...

    @abstractmethod
    def label(self) -> str: ...

    def print_tree(self, indent=0):
        print("  " * indent + self.label())
        for child in self.child_nodes():
            child.print_tree(indent + 1)

    def to_avro(self) -> dict[str, Any]:
        """Render this tree as the plain dict matching the avro AlgebraNode record."""
        return self._node_to_avro()

    @classmethod
    def from_avro(cls, datum: Mapping[str, Any]) -> AbstractAlgebraNode:
        """Rebuild a tree from a dict matching the avro AlgebraNode record."""
        return cls._node_from_avro(datum)

    def _node_to_avro(self) -> dict[str, Any]:
        """Python algebra tree -> plain dict matching the avro AlgebraNode record."""
        # NOTE: prefill every record field (all have avro defaults) so DatumWriter always finds a value.
        datum: dict[str, Any] = {
            "kind": self.kind,
            "table": None,
            "values": None,
            "columns": None,
            "expr": None,
            "children": None,
            "assignments": None,
            "custom": self.custom,
        }
        match self:
            case TableScan():
                datum["table"] = self._resolved_table_to_avro(self.table)
            case VirtualTableScan():
                datum["values"] = self._values_rows_to_avro(self.values)
            case Projection():
                datum["columns"] = self._columns_to_avro(self.columns)
                datum["children"] = [self.child._node_to_avro()]
            case Selection():
                datum["expr"] = self._expr_to_avro(self.expr)
                datum["children"] = [self.child._node_to_avro()]
            case TupleUpdate():
                datum["assignments"] = self._assignments_to_avro(self.assignments)
                datum["children"] = [self.child._node_to_avro()]
            case Cross() | Union() | Difference():
                datum["children"] = [self.left._node_to_avro(), self.right._node_to_avro()]
            case AlgebraNode():
                if self.table is not None:
                    datum["table"] = self._resolved_table_to_avro(self.table)
                if self.values is not None:
                    datum["values"] = self._values_rows_to_avro(self.values)
                if self.columns is not None:
                    datum["columns"] = self._columns_to_avro(self.columns)
                if self.expr is not None:
                    datum["expr"] = self._expr_to_avro(self.expr)
                if self.children:
                    datum["children"] = [child._node_to_avro() for child in self.children]
                if self.assignments is not None:
                    datum["assignments"] = self._assignments_to_avro(self.assignments)
            case _:
                raise ValueError(f"Cannot serialize algebra node of kind {self.kind!r}")
        return datum

    def _expr_to_avro(self, expr: Expr) -> dict[str, Any]:
        """Python Expr -> avro Expr wrapper record ``{"expr": <typed inner record>}``."""
        match expr:
            case LiteralExpr():
                inner: dict[str, Any] = {
                    "kind": "LiteralExpr",
                    "type": expr.type,
                    "value": expr.value,
                    "custom": expr.custom,
                }
            case BinOp():
                inner = {
                    "kind": "BinOp",
                    "op": expr.op,
                    "left": self._expr_to_avro(expr.left),
                    "right": self._expr_to_avro(expr.right),
                    "custom": expr.custom,
                }
            case UnOp():
                inner = {"kind": "UnOp", "op": expr.op, "expr": self._expr_to_avro(expr.expr), "custom": expr.custom}
            case ColumnRef():
                inner = {"kind": "ColumnRef", "name": expr.name, "qualifier": expr.qualifier, "custom": expr.custom}
            case TableRef():
                inner = {"kind": "TableRef", "name": expr.name, "alias": expr.alias, "custom": expr.custom}
            case _:
                raise ValueError(f"Cannot serialize expression of type {type(expr).__name__}")
        return {"expr": inner}

    def _resolved_column_to_avro(self, column: ResolvedColumn) -> dict[str, Any]:
        return {
            "kind": "ResolvedColumn",
            "name": column.name,
            "type": column.type.name,
            "constraint": column.constraint.name if column.constraint is not None else None,
            "qualifier": column.qualifier,
            "custom": None,
        }

    def _resolved_value_to_avro(self, row_value: ResolvedValue) -> dict[str, Any]:
        return {"kind": "ResolvedValue", "type": row_value.type.name, "value": row_value.value, "custom": None}

    def _resolved_table_to_avro(self, table: ResolvedTable) -> dict[str, Any]:
        return {
            "kind": "ResolvedTable",
            "name": table.name,
            # NOTE: avro spelling is camelCase tableSchema; the python field is table_schema.
            "tableSchema": [self._resolved_column_to_avro(column) for column in table.table_schema],
            "alias": table.alias,
            "custom": None,
        }

    def _values_rows_to_avro(self, values: list[tuple[ResolvedValue, ...]]) -> list[list[dict[str, Any]]]:
        return [[self._resolved_value_to_avro(row_value) for row_value in row] for row in values]

    def _columns_to_avro(self, columns: tuple[ResolvedColumn, ...]) -> list[dict[str, Any]]:
        return [self._resolved_column_to_avro(column) for column in columns]

    def _assignments_to_avro(self, assignments: tuple[tuple[ResolvedColumn, Expr], ...]) -> list[dict[str, Any]]:
        return [
            {"column": self._resolved_column_to_avro(column), "expr": self._expr_to_avro(assignment_expr)}
            for column, assignment_expr in assignments
        ]

    @classmethod
    def _node_from_avro(cls, datum: Mapping[str, Any]) -> AbstractAlgebraNode:
        """Plain dict matching the avro AlgebraNode record -> Python algebra tree."""
        custom = datum.get("custom")
        match datum["kind"]:
            case "TableScan":
                return TableScan(table=cls._resolved_table_from_avro(datum["table"]), custom=custom)
            case "VirtualTableScan":
                return VirtualTableScan(values=cls._values_rows_from_avro(datum["values"]), custom=custom)
            case "Projection":
                return Projection(
                    columns=cls._columns_from_avro(datum["columns"]),
                    child=cls._node_from_avro(datum["children"][0]),
                    custom=custom,
                )
            case "Selection":
                return Selection(
                    expr=cls._expr_from_avro(datum["expr"]),
                    child=cls._node_from_avro(datum["children"][0]),
                    custom=custom,
                )
            case "TupleUpdate":
                return TupleUpdate(
                    assignments=cls._assignments_from_avro(datum["assignments"]),
                    child=cls._node_from_avro(datum["children"][0]),
                    custom=custom,
                )
            case "Cross" | "Union" | "Difference":
                node_class = cls._AVRO_NODE_KINDS[datum["kind"]]
                return node_class(
                    left=cls._node_from_avro(datum["children"][0]),
                    right=cls._node_from_avro(datum["children"][1]),
                    custom=custom,
                )
            case _:
                # NOTE: forward compatibility — a reader that does not know a kind still receives
                # the plan as a generic AlgebraNode instead of failing the whole decode.
                return AlgebraNode(
                    kind=datum["kind"],
                    table=cls._resolved_table_from_avro(datum["table"]) if datum.get("table") is not None else None,
                    values=cls._values_rows_from_avro(datum["values"]) if datum.get("values") is not None else None,
                    columns=cls._columns_from_avro(datum["columns"]) if datum.get("columns") is not None else None,
                    expr=cls._expr_from_avro(datum["expr"]) if datum.get("expr") is not None else None,
                    # the field is non-optional; an empty/null wire list decodes to [].
                    children=[cls._node_from_avro(child) for child in (datum.get("children") or [])],
                    assignments=(
                        cls._assignments_from_avro(datum["assignments"])
                        if datum.get("assignments") is not None
                        else None
                    ),
                    custom=custom,
                )

    @classmethod
    def _expr_from_avro(cls, datum: Mapping[str, Any]) -> Expr:
        """avro Expr wrapper record -> Python Expr (dispatch on the inner record's kind)."""
        inner = datum["expr"]
        match inner["kind"]:
            case "LiteralExpr":
                value = inner["value"]
                # NOTE: ints ride the double branch on the wire (see the LiteralExpr notes in
                # algebranode.avdl); renormalize them to python ints, NULL stays None.
                if inner["type"] == "int" and value is not None:
                    value = int(value)
                return LiteralExpr(type=inner["type"], value=value, custom=inner["custom"])
            case "BinOp":
                return BinOp(
                    op=inner["op"],
                    left=cls._expr_from_avro(inner["left"]),
                    right=cls._expr_from_avro(inner["right"]),
                    custom=inner["custom"],
                )
            case "UnOp":
                return UnOp(op=inner["op"], expr=cls._expr_from_avro(inner["expr"]), custom=inner["custom"])
            case "ColumnRef":
                return ColumnRef(name=inner["name"], qualifier=inner["qualifier"], custom=inner["custom"])
            case "TableRef":
                return TableRef(name=inner["name"], alias=inner["alias"], custom=inner["custom"])
            case _:
                raise ValueError(f"Unknown expression kind {inner['kind']!r}")

    @classmethod
    def _resolved_column_from_avro(cls, datum: Mapping[str, Any]) -> ResolvedColumn:
        return ResolvedColumn(
            name=datum["name"],
            # NOTE: enums commute via their symbol names (avro STRING <-> python DataType.STRING).
            type=DataType[datum["type"]],
            constraint=ColumnConstraint[datum["constraint"]] if datum["constraint"] is not None else None,
            qualifier=datum["qualifier"],
        )

    @classmethod
    def _resolved_value_from_avro(cls, datum: Mapping[str, Any]) -> ResolvedValue:
        value_type = DataType[datum["type"]]
        value = datum["value"]
        # NOTE: ints ride the double branch on the wire (see the LiteralExpr notes in
        # algebranode.avdl); renormalize them to python ints, NULL (unbound column) stays None.
        if value_type is DataType.INT and value is not None:
            value = int(value)
        return ResolvedValue(type=value_type, value=value)

    @classmethod
    def _resolved_table_from_avro(cls, datum: Mapping[str, Any]) -> ResolvedTable:
        return ResolvedTable(
            name=datum["name"],
            # NOTE: avro spelling is camelCase tableSchema; the python field is table_schema.
            table_schema=tuple(cls._resolved_column_from_avro(column) for column in datum["tableSchema"]),
            alias=datum["alias"],
        )

    @classmethod
    def _values_rows_from_avro(cls, values: list) -> list[tuple[ResolvedValue, ...]]:
        return [tuple(cls._resolved_value_from_avro(row_value) for row_value in row) for row in values]

    @classmethod
    def _columns_from_avro(cls, columns: list) -> tuple[ResolvedColumn, ...]:
        return tuple(cls._resolved_column_from_avro(column) for column in columns)

    @classmethod
    def _assignments_from_avro(cls, assignments: list) -> tuple[tuple[ResolvedColumn, Expr], ...]:
        return tuple(
            (cls._resolved_column_from_avro(assignment["column"]), cls._expr_from_avro(assignment["expr"]))
            for assignment in assignments
        )


class AlgebraNode(AbstractAlgebraNode):
    """
    A generic class that can be used to implement any of the existing and potential new algebra nodes.

    """

    kind: str
    table: ResolvedTable | None = None
    values: list[tuple[ResolvedValue, ...]] | None = None
    columns: tuple[ResolvedColumn, ...] | None = None
    expr: Expr | None = None
    children: list[AbstractAlgebraNode] = []
    assignments: tuple[tuple[ResolvedColumn, Expr], ...] | None = None

    def child_nodes(self) -> list[AbstractAlgebraNode]:
        return self.children

    def label(self) -> str:
        return f"{self.kind}"


class TableScan(AbstractAlgebraNode):
    kind: Literal["TableScan"] = "TableScan"
    table: ResolvedTable

    def child_nodes(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        if self.table.alias:
            return f"TableScan({self.table.name} AS {self.table.alias})"
        return f"TableScan({self.table.name})"


class VirtualTableScan(AbstractAlgebraNode):
    kind: Literal["VirtualTableScan"] = "VirtualTableScan"
    values: list[tuple[ResolvedValue, ...]]

    def child_nodes(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"VirtualTableScan({len(self.values)} insert tuples {self.values[0]})"


class Projection(AbstractAlgebraNode):
    kind: Literal["Projection"] = "Projection"
    columns: tuple[ResolvedColumn, ...]  # NOTE: star is expanded to all columns present in table
    child: AbstractAlgebraNode

    def child_nodes(self) -> list[AbstractAlgebraNode]:
        return [self.child]

    def label(self) -> str:
        projection_str = ", ".join(f"{col.qualifier}.{col.name}" for col in self.columns)
        return f"π[{projection_str}]"


class Selection(AbstractAlgebraNode):
    kind: Literal["Selection"] = "Selection"
    expr: Expr
    child: AbstractAlgebraNode

    def child_nodes(self) -> list[AbstractAlgebraNode]:
        return [self.child]

    def label(self) -> str:
        return f"σ[{pretty(self.expr)}]"


class Cross(AbstractAlgebraNode):
    kind: Literal["Cross"] = "Cross"
    left: AbstractAlgebraNode
    right: AbstractAlgebraNode

    def child_nodes(self) -> list[AbstractAlgebraNode]:
        return [self.left, self.right]

    def label(self) -> str:
        return "CROSS"


class Union(AbstractAlgebraNode):
    kind: Literal["Union"] = "Union"
    left: AbstractAlgebraNode
    right: AbstractAlgebraNode

    def child_nodes(self) -> list[AbstractAlgebraNode]:
        return [self.left, self.right]

    def label(self) -> str:
        return "UNION"


class Difference(AbstractAlgebraNode):
    kind: Literal["Difference"] = "Difference"
    left: AbstractAlgebraNode
    right: AbstractAlgebraNode

    def child_nodes(self) -> list[AbstractAlgebraNode]:
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

    def child_nodes(self) -> list[AbstractAlgebraNode]:
        return [self.child]

    def label(self) -> str:
        assignments = ", ".join(f"{column.name} := {pretty(expr)}" for column, expr in self.assignments)
        return f"TupleUpdate({assignments})"
