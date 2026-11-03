"""Tests for set-operation statements (UNION / EXCEPT between SELECTs)."""

from typing import cast

from components.queryparser.expression import BinOp
from components.queryparser.unibasql_parser import parse_unibasql
from components.queryparser.unibasql_unresolved import (
    AbstractStatement,
    SetOperation,
    UnresolvedColumn,
    UnresolvedDQL,
    UnresolvedLiteralExpr,
    UnresolvedSetOperation,
    UnresolvedTable,
)


def dql(table: str, column: str) -> UnresolvedDQL:
    return UnresolvedDQL(
        projection=[UnresolvedColumn(name=column)],
        table=[UnresolvedTable(name=table)],
    )


def dql_where(table: str, column: str, where_column: str, value: int) -> UnresolvedDQL:
    return UnresolvedDQL(
        projection=[UnresolvedColumn(name=column)],
        table=[UnresolvedTable(name=table)],
        expr=BinOp(
            op="=", left=UnresolvedColumn(name=where_column), right=UnresolvedLiteralExpr(value=value, type="int")
        ),
    )


def parse_set_operation(sql: str) -> UnresolvedSetOperation:
    # A set operation is parsed through a single-element statement list.
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    assert len(result) == 1
    statement = cast(AbstractStatement, result[0])
    assert isinstance(statement, UnresolvedSetOperation)
    return statement


def test_union_uppercase() -> None:
    sql = "SELECT a FROM t1 UNION SELECT b FROM t2"
    assert parse_set_operation(sql) == UnresolvedSetOperation(
        op=SetOperation.UNION, left=dql("t1", "a"), right=dql("t2", "b")
    )


def test_union_lowercase() -> None:
    # Regression: the visitor compared operator text case-sensitively against
    # "UNION"/"EXCEPT" although the grammar is case-insensitive, so lower-case
    # input silently collapsed to just the first SELECT.
    assert parse_set_operation("select a from t1 union select b from t2") == UnresolvedSetOperation(
        op=SetOperation.UNION, left=dql("t1", "a"), right=dql("t2", "b")
    )


def test_union_mixed_case() -> None:
    assert parse_set_operation("SELECT a FROM t1 UniOn SELECT b FROM t2") == UnresolvedSetOperation(
        op=SetOperation.UNION, left=dql("t1", "a"), right=dql("t2", "b")
    )


def test_except_uppercase() -> None:
    assert parse_set_operation("SELECT a FROM t1 EXCEPT SELECT b FROM t2") == UnresolvedSetOperation(
        op=SetOperation.EXCEPT, left=dql("t1", "a"), right=dql("t2", "b")
    )


def test_except_lowercase() -> None:
    assert parse_set_operation("select a from t1 except select b from t2") == UnresolvedSetOperation(
        op=SetOperation.EXCEPT, left=dql("t1", "a"), right=dql("t2", "b")
    )


def test_chained_union_is_left_associative() -> None:
    sql = "SELECT a FROM t1 UNION SELECT b FROM t2 UNION SELECT c FROM t3"
    assert parse_set_operation(sql) == UnresolvedSetOperation(
        op=SetOperation.UNION,
        left=UnresolvedSetOperation(op=SetOperation.UNION, left=dql("t1", "a"), right=dql("t2", "b")),
        right=dql("t3", "c"),
    )


def test_chained_union_except_is_left_associative() -> None:
    sql = "SELECT a FROM t1 UNION SELECT b FROM t2 EXCEPT SELECT c FROM t3"
    assert parse_set_operation(sql) == UnresolvedSetOperation(
        op=SetOperation.EXCEPT,
        left=UnresolvedSetOperation(op=SetOperation.UNION, left=dql("t1", "a"), right=dql("t2", "b")),
        right=dql("t3", "c"),
    )


def test_set_operation_with_where_clauses() -> None:
    sql = "SELECT a FROM t1 WHERE x = 1 EXCEPT SELECT b FROM t2 WHERE y = 2"
    assert parse_set_operation(sql) == UnresolvedSetOperation(
        op=SetOperation.EXCEPT,
        left=dql_where("t1", "a", "x", 1),
        right=dql_where("t2", "b", "y", 2),
    )
