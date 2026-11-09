"""Tests for literal ``value`` parsing across all grammar productions."""

from typing import cast

import pytest

from components.queryprocessor.expression import BinOp
from components.queryprocessor.unibasql_parser import parse_unibasql
from components.queryprocessor.unibasql_unresolved import (
    AllowedValue,
    DataType,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedDQL,
    UnresolvedLiteralExpr,
)


def parse_insert_value(sql: str) -> UnresolvedLiteralExpr:
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    assert len(result) == 1
    statement = cast(UnresolvedDML, result[0])
    assert isinstance(statement, UnresolvedDML)
    assert statement.values is not None
    assert len(statement.values) == 1
    assert len(statement.values[0]) == 1
    return statement.values[0][0]


@pytest.mark.parametrize(
    ("raw", "value", "value_type"),
    [
        ("'foo'", "foo", "string"),
        ("''", "", "string"),
        ("'it''s a test'", "it's a test", "string"),
        ("42", 42, "int"),
        ("-7", -7, "int"),
        ("-0", 0, "int"),
        ("3.14", 3.14, "double"),
        ("-1.5", -1.5, "double"),
        ("TRUE", True, "bool"),
        ("true", True, "bool"),
        ("FALSE", False, "bool"),
        ("false", False, "bool"),
        ("NULL", None, DataType.NULL),
        ("null", None, DataType.NULL),
    ],
)
def test_insert_literals(raw: str, value: AllowedValue, value_type: str) -> None:
    sql = f"INSERT INTO t VALUES ({raw})"
    assert parse_insert_value(sql) == UnresolvedLiteralExpr(value=value, type=value_type)


def test_insert_value_row_with_every_literal_kind() -> None:
    result = parse_unibasql("INSERT INTO t VALUES (42, -3.5, 'x', TRUE, FALSE, NULL)")
    assert isinstance(result, list)
    assert len(result) == 1
    statement = cast(UnresolvedDML, result[0])
    assert statement.values == [
        [
            UnresolvedLiteralExpr(value=42, type="int"),
            UnresolvedLiteralExpr(value=-3.5, type="double"),
            UnresolvedLiteralExpr(value="x", type="string"),
            UnresolvedLiteralExpr(value=True, type="bool"),
            UnresolvedLiteralExpr(value=False, type="bool"),
            UnresolvedLiteralExpr(value=None, type=DataType.NULL),
        ]
    ]


def test_where_literal_right_operand() -> None:
    # Literals also appear as comparison operands; check one full round trip.
    result = parse_unibasql("SELECT a FROM t WHERE x = -1.5")
    assert isinstance(result, list)
    assert len(result) == 1
    statement = cast(UnresolvedDQL, result[0])
    assert isinstance(statement, UnresolvedDQL)
    assert statement.expr == BinOp(
        op="=", left=UnresolvedColumn(name="x"), right=UnresolvedLiteralExpr(value=-1.5, type="double")
    )
