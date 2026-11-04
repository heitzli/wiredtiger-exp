"""Tests for DDL statements: CREATE/DROP TABLE and CREATE/DROP INDEX."""

from typing import cast

import pytest

from components.queryparser.unibasql_parser import parse_unibasql
from components.queryparser.unibasql_unresolved import (
    ColumnConstraint,
    DataType,
    DDLOp,
    TableDefTriple,
    UnresolvedColumn,
    UnresolvedIndexDDL,
    UnresolvedTable,
    UnresolvedTableDDL,
)


def parse_single_ddl(sql: str) -> UnresolvedTableDDL | UnresolvedIndexDDL:
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    assert len(result) == 1
    statement = cast(UnresolvedTableDDL | UnresolvedIndexDDL, result[0])
    assert isinstance(statement, UnresolvedTableDDL | UnresolvedIndexDDL)
    return statement


def test_create_table_full_schema() -> None:
    sql = "CREATE TABLE users (id INT PRIMARY KEY, name STRING NOT NULL, active BOOL, score DOUBLE PRIMARY KEY)"
    assert parse_single_ddl(sql) == UnresolvedTableDDL(
        op=DDLOp.CREATE,
        table=UnresolvedTable(name="users"),
        table_schema=[
            TableDefTriple(
                column=UnresolvedColumn(name="id"), datatype=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY
            ),
            TableDefTriple(
                column=UnresolvedColumn(name="name"), datatype=DataType.STRING, constraint=ColumnConstraint.NOT_NULL
            ),
            TableDefTriple(column=UnresolvedColumn(name="active"), datatype=DataType.BOOL, constraint=None),
            TableDefTriple(
                column=UnresolvedColumn(name="score"), datatype=DataType.DOUBLE, constraint=ColumnConstraint.PRIMARY_KEY
            ),
        ],
    )


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("STRING", DataType.STRING),
        ("INT", DataType.INT),
        ("DOUBLE", DataType.DOUBLE),
        ("BOOL", DataType.BOOL),
        # Regression: datatype lookup used to be case-sensitive and raised a
        # KeyError although the grammar is case-insensitive.
        ("string", DataType.STRING),
        ("int", DataType.INT),
        ("double", DataType.DOUBLE),
        ("bool", DataType.BOOL),
        ("StRiNg", DataType.STRING),
    ],
)
def test_create_table_all_datatypes(sql_type: str, expected: DataType) -> None:
    sql = f"CREATE TABLE t (c {sql_type} PRIMARY KEY)"
    assert parse_single_ddl(sql) == UnresolvedTableDDL(
        op=DDLOp.CREATE,
        table=UnresolvedTable(name="t"),
        table_schema=[
            TableDefTriple(
                column=UnresolvedColumn(name="c"), datatype=expected, constraint=ColumnConstraint.PRIMARY_KEY
            )
        ],
    )


def test_create_table_lowercase_keywords() -> None:
    sql = "create table t (c int primary key, d int not null)"
    assert parse_single_ddl(sql) == UnresolvedTableDDL(
        op=DDLOp.CREATE,
        table=UnresolvedTable(name="t"),
        table_schema=[
            TableDefTriple(
                column=UnresolvedColumn(name="c"), datatype=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY
            ),
            TableDefTriple(
                column=UnresolvedColumn(name="d"), datatype=DataType.INT, constraint=ColumnConstraint.NOT_NULL
            ),
        ],
    )


def test_drop_table() -> None:
    assert parse_single_ddl("DROP TABLE users") == UnresolvedTableDDL(
        op=DDLOp.DROP, table=UnresolvedTable(name="users"), table_schema=None
    )


def test_create_index_single_column() -> None:
    assert parse_single_ddl("CREATE INDEX idx ON users (name)") == UnresolvedIndexDDL(
        op=DDLOp.CREATE,
        idx_name="idx",
        table=UnresolvedTable(name="users"),
        columns=[UnresolvedColumn(name="name")],
    )


def test_create_index_multiple_columns() -> None:
    assert parse_single_ddl("CREATE INDEX idx ON users (name, u.age)") == UnresolvedIndexDDL(
        op=DDLOp.CREATE,
        idx_name="idx",
        table=UnresolvedTable(name="users"),
        columns=[UnresolvedColumn(name="name"), UnresolvedColumn(name="age", qualifier="u")],
    )


def test_drop_index() -> None:
    # Regression: DROP INDEX used to omit the required columns field and failed
    # pydantic validation; it carries no columns by syntax.
    assert parse_single_ddl("DROP INDEX idx ON users") == UnresolvedIndexDDL(
        op=DDLOp.DROP,
        idx_name="idx",
        table=UnresolvedTable(name="users"),
        columns=None,
    )
