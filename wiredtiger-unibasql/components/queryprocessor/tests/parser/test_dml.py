"""Tests for DML statements: INSERT, UPDATE and DELETE."""

from typing import cast

from components.queryprocessor.expression import BinOp
from components.queryprocessor.unibasql_parser import parse_unibasql
from components.queryprocessor.unibasql_unresolved import (
    DMLOp,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedLiteralExpr,
    UnresolvedTable,
)


def parse_single_dml(sql: str) -> UnresolvedDML:
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    assert len(result) == 1
    statement = cast(UnresolvedDML, result[0])
    assert isinstance(statement, UnresolvedDML)
    return statement


def col(name: str) -> UnresolvedColumn:
    return UnresolvedColumn(name=name)


def test_insert_with_columns_and_multiple_rows() -> None:
    sql = "INSERT INTO users (id, name) VALUES (1, 'a'), (2, 'b')"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="users"),
        columns=[col("id"), col("name")],
        values=[
            [UnresolvedLiteralExpr(value=1, type="int"), UnresolvedLiteralExpr(value="a", type="string")],
            [UnresolvedLiteralExpr(value=2, type="int"), UnresolvedLiteralExpr(value="b", type="string")],
        ],
    )


def test_insert_without_column_list() -> None:
    sql = "INSERT INTO users VALUES (1, 'a')"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="users"),
        columns=None,
        values=[[UnresolvedLiteralExpr(value=1, type="int"), UnresolvedLiteralExpr(value="a", type="string")]],
    )


def test_insert_single_value_row() -> None:
    sql = "INSERT INTO users VALUES (1)"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="users"),
        values=[[UnresolvedLiteralExpr(value=1, type="int")]],
    )


def test_insert_negative_and_floating_values() -> None:
    # Regression: the values/setlist fields were annotated with the resolved
    # LiteralExpr while the parser builds UnresolvedLiteralExpr, so pydantic
    # rejected every INSERT at validation time.
    sql = "INSERT INTO users (a, b) VALUES (-3, 4.5)"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="users"),
        columns=[col("a"), col("b")],
        values=[[UnresolvedLiteralExpr(value=-3, type="int"), UnresolvedLiteralExpr(value=4.5, type="double")]],
    )


def test_update_multiple_sets_with_where() -> None:
    sql = "UPDATE users SET name = 'x', age = 30 WHERE id >= 2"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.UPDATE,
        table=UnresolvedTable(name="users"),
        setlist=[
            (col("name"), UnresolvedLiteralExpr(value="x", type="string")),
            (col("age"), UnresolvedLiteralExpr(value=30, type="int")),
        ],
        expr=BinOp(op=">=", left=col("id"), right=UnresolvedLiteralExpr(value=2, type="int")),
    )


def test_update_qualified_set_column() -> None:
    sql = "UPDATE users SET u.age = 1"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.UPDATE,
        table=UnresolvedTable(name="users"),
        setlist=[(UnresolvedColumn(name="age", qualifier="u"), UnresolvedLiteralExpr(value=1, type="int"))],
    )


def test_update_without_where() -> None:
    sql = "UPDATE users SET age = 1"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.UPDATE,
        table=UnresolvedTable(name="users"),
        setlist=[(col("age"), UnresolvedLiteralExpr(value=1, type="int"))],
        expr=None,
    )


def test_delete_with_where() -> None:
    sql = "DELETE FROM users WHERE id <> 1"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.DELETE,
        table=UnresolvedTable(name="users"),
        expr=BinOp(op="<>", left=col("id"), right=UnresolvedLiteralExpr(value=1, type="int")),
    )


def test_delete_without_where() -> None:
    sql = "DELETE FROM users"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.DELETE,
        table=UnresolvedTable(name="users"),
        expr=None,
    )


def test_delete_neq_alternative_operator() -> None:
    sql = "DELETE FROM users WHERE id != 1"
    assert parse_single_dml(sql) == UnresolvedDML(
        op=DMLOp.DELETE,
        table=UnresolvedTable(name="users"),
        expr=BinOp(op="!=", left=col("id"), right=UnresolvedLiteralExpr(value=1, type="int")),
    )
