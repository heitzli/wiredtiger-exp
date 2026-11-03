import uuid
from abc import ABC
from enum import StrEnum, auto
from typing import Literal, TypeAlias

from pydantic import Field
from pydantic.dataclasses import dataclass

from .expression import Expr

AllowedValue: TypeAlias = str | int | float | bool | None
Star: TypeAlias = Literal["*"]


# Enums


class DataType(StrEnum):
    STRING = auto()
    BOOL = auto()
    INT = auto()
    DOUBLE = auto()


class ColumnConstraint(StrEnum):
    NOT_NULL = auto()
    PRIMARY_KEY = auto()


class DMLOp(StrEnum):
    INSERT = auto()
    UPDATE = auto()
    DELETE = auto()


class DDLOp(StrEnum):
    CREATE = auto()
    DROP = auto()


class SetOperation(StrEnum):
    UNION = auto()
    EXCEPT = auto()


class TxResult(StrEnum):
    COMMIT = auto()
    ROLLBACK = auto()


# Abstract classes


@dataclass(frozen=True)
class AbstractStatement(ABC):
    """Base class for all SQL statements."""

    pass


@dataclass(frozen=True)
class UnresolvedQuery(AbstractStatement, ABC):
    """Base class for unresolved query expressions."""

    pass


# Unresolved references
#


class UnresolvedLiteralExpr(Expr):
    """Represents a Literal Expression that has not been resolved, mirrors LiteralExpr"""

    type: str
    value: AllowedValue


class UnresolvedColumn(Expr):
    """Represents a column reference that has not been resolved."""

    name: str
    qualifier: str | None = None


class UnresolvedTable(Expr):
    """Represents a table reference that has not been resolved."""

    name: str
    alias: str | None = None


# Unresolved queries (DQL)


@dataclass(frozen=True)
class UnresolvedDQL(UnresolvedQuery):
    """Represents an unresolved SELECT statement."""

    projection: list[UnresolvedColumn] | Star
    table: list[UnresolvedTable]
    expr: Expr | None = None


@dataclass(frozen=True)
class UnresolvedSetOperation(UnresolvedQuery):
    """Represents a UNION or EXCEPT between two unresolved queries."""

    op: SetOperation
    left: UnresolvedQuery
    right: UnresolvedQuery


# Unresolved DML


@dataclass(frozen=True)
class UnresolvedDML(AbstractStatement):
    """Represents an unresolved INSERT, UPDATE or DELETE statement."""

    op: DMLOp
    table: UnresolvedTable
    columns: list[UnresolvedColumn] | None = None
    values: list[list[UnresolvedLiteralExpr]] | None = None
    expr: Expr | None = None
    setlist: list[tuple[UnresolvedColumn, UnresolvedLiteralExpr]] | None = None


# Unresolved DDL


@dataclass(frozen=True)
class TableDefTriple:
    """Represents a column definition in a CREATE TABLE statement."""

    column: UnresolvedColumn
    datatype: DataType
    constraint: ColumnConstraint | None = None


@dataclass(frozen=True)
class UnresolvedIndexDDL(AbstractStatement):
    """Represents an unresolved CREATE INDEX or DROP INDEX statement."""

    op: DDLOp
    idx_name: str
    table: UnresolvedTable
    columns: list[UnresolvedColumn] | None = None


@dataclass(frozen=True)
class UnresolvedTableDDL(AbstractStatement):
    """Represents an unresolved CREATE TABLE or DROP TABLE statement."""

    op: DDLOp
    table: UnresolvedTable
    table_schema: list[TableDefTriple] | None = None


# Transactions


@dataclass(frozen=True)
class StartTransaction:
    """Represents the beginning of a database transaction."""

    tx_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description=("Unique identifier for this transaction. If none is provided, a UUID is generated."),
    )


@dataclass(frozen=True)
class EndTransaction:
    """Represents the end of a database transaction."""

    tx_result: TxResult


@dataclass(frozen=True)
class Transaction:
    """Represents an entire transaction declared as a query.

    Example:
        BEGIN TRANSACTION tx1;
        CREATE TABLE ...;
        INSERT ...;
        COMMIT;
    """

    tx_start: StartTransaction
    tx_end: EndTransaction
    statements: list[AbstractStatement]
