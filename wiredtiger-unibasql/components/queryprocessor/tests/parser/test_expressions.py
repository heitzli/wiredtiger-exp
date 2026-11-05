"""Tests for expression parsing (comparison operators, operands, precedence).

Note on precedence: ANTLR generates precedence OR=5 > AND=4 > NOT=3 from the
current UnibaSQL.g4, i.e. OR binds tighter than AND, and NOT is the loosest
prefix operator (it wraps every or/and to its right). The tests below pin down
the behavior the parser actually exhibits for this grammar.
"""

from typing import cast

import pytest

from components.queryprocessor.expression import BinOp, Expr, UnOp
from components.queryprocessor.unibasql_parser import parse_unibasql
from components.queryprocessor.unibasql_unresolved import (
    UnresolvedColumn,
    UnresolvedDQL,
    UnresolvedLiteralExpr,
)


def col(name: str) -> UnresolvedColumn:
    return UnresolvedColumn(name=name)


def qual_col(qualifier: str, name: str) -> UnresolvedColumn:
    return UnresolvedColumn(name=name, qualifier=qualifier)


def lit_int(value: int) -> UnresolvedLiteralExpr:
    return UnresolvedLiteralExpr(value=value, type="int")


def parse_where(sql: str) -> Expr:
    """Parse ``sql`` and return the WHERE expression of its single SELECT."""
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    assert len(result) == 1
    statement = cast(UnresolvedDQL, result[0])
    assert statement.expr is not None
    return statement.expr


@pytest.mark.parametrize(
    ("op", "expected_op"),
    [
        ("=", "="),
        ("<>", "<>"),
        ("!=", "!="),
        ("<", "<"),
        ("<=", "<="),
        (">", ">"),
        (">=", ">="),
    ],
)
def test_all_comparison_operators(op: str, expected_op: str) -> None:
    sql = f"SELECT a FROM t WHERE x {op} 1"
    assert parse_where(sql) == BinOp(op=expected_op, left=col("x"), right=lit_int(1))


def test_comparison_with_value_operands() -> None:
    assert parse_where("SELECT a FROM t WHERE 1 = 2") == BinOp(op="=", left=lit_int(1), right=lit_int(2))


def test_comparison_with_column_operands() -> None:
    assert parse_where("SELECT a FROM t WHERE u.a = t.b") == BinOp(
        op="=", left=qual_col("u", "a"), right=qual_col("t", "b")
    )


def test_and_expression() -> None:
    assert parse_where("SELECT a FROM t WHERE x = 1 AND y = 2") == BinOp(
        op="AND",
        left=BinOp(op="=", left=col("x"), right=lit_int(1)),
        right=BinOp(op="=", left=col("y"), right=lit_int(2)),
    )


def test_or_expression() -> None:
    assert parse_where("SELECT a FROM t WHERE x = 1 OR y = 2") == BinOp(
        op="OR",
        left=BinOp(op="=", left=col("x"), right=lit_int(1)),
        right=BinOp(op="=", left=col("y"), right=lit_int(2)),
    )


def test_not_expression_wraps_comparison() -> None:
    assert parse_where("SELECT a FROM t WHERE NOT x = 1") == UnOp(
        op="NOT", expr=BinOp(op="=", left=col("x"), right=lit_int(1))
    )


def test_paren_expression_is_transparent() -> None:
    # The parens only structure the parse; the AST holds the inner expression.
    assert parse_where("SELECT a FROM t WHERE (x = 1)") == BinOp(op="=", left=col("x"), right=lit_int(1))


def test_paren_grouping_changes_shape() -> None:
    assert parse_where("SELECT a FROM t WHERE (x = 1 OR y = 2) AND z = 3") == BinOp(
        op="AND",
        left=BinOp(
            op="OR",
            left=BinOp(op="=", left=col("x"), right=lit_int(1)),
            right=BinOp(op="=", left=col("y"), right=lit_int(2)),
        ),
        right=BinOp(op="=", left=col("z"), right=lit_int(3)),
    )


def test_or_binds_tighter_than_and() -> None:
    # Grammar-specific: `x OR y AND z` parses as `(x OR y) AND z`.
    assert parse_where("SELECT a FROM t WHERE x = 1 OR y = 2 AND z = 3") == BinOp(
        op="AND",
        left=BinOp(
            op="OR",
            left=BinOp(op="=", left=col("x"), right=lit_int(1)),
            right=BinOp(op="=", left=col("y"), right=lit_int(2)),
        ),
        right=BinOp(op="=", left=col("z"), right=lit_int(3)),
    )


def test_or_group_absorbed_into_and_right_operand() -> None:
    # Grammar-specific: `x AND y OR z` parses as `x AND (y OR z)`.
    assert parse_where("SELECT a FROM t WHERE x = 1 AND y = 2 OR z = 3") == BinOp(
        op="AND",
        left=BinOp(op="=", left=col("x"), right=lit_int(1)),
        right=BinOp(
            op="OR",
            left=BinOp(op="=", left=col("y"), right=lit_int(2)),
            right=BinOp(op="=", left=col("z"), right=lit_int(3)),
        ),
    )


def test_not_swallows_trailing_or() -> None:
    # Grammar-specific: NOT is the loosest operator and wraps the OR to its right.
    assert parse_where("SELECT a FROM t WHERE NOT x = 1 OR y = 2") == UnOp(
        op="NOT",
        expr=BinOp(
            op="OR",
            left=BinOp(op="=", left=col("x"), right=lit_int(1)),
            right=BinOp(op="=", left=col("y"), right=lit_int(2)),
        ),
    )


def test_not_in_and_operand() -> None:
    # Grammar-specific: the NOT operand extends over `y = 2 OR z = 3`.
    assert parse_where("SELECT a FROM t WHERE x = 1 AND NOT y = 2 OR z = 3") == BinOp(
        op="AND",
        left=BinOp(op="=", left=col("x"), right=lit_int(1)),
        right=UnOp(
            op="NOT",
            expr=BinOp(
                op="OR",
                left=BinOp(op="=", left=col("y"), right=lit_int(2)),
                right=BinOp(op="=", left=col("z"), right=lit_int(3)),
            ),
        ),
    )


def test_same_operator_chain_is_left_associative() -> None:
    expected: Expr = BinOp(
        op="OR",
        left=BinOp(
            op="OR",
            left=BinOp(op="=", left=col("x"), right=lit_int(1)),
            right=BinOp(op="=", left=col("y"), right=lit_int(2)),
        ),
        right=BinOp(op="=", left=col("z"), right=lit_int(3)),
    )
    assert parse_where("SELECT a FROM t WHERE x = 1 OR y = 2 OR z = 3") == expected


def test_and_chain_is_left_associative() -> None:
    expected: Expr = BinOp(
        op="AND",
        left=BinOp(
            op="AND",
            left=BinOp(op="=", left=col("x"), right=lit_int(1)),
            right=BinOp(op="=", left=col("y"), right=lit_int(2)),
        ),
        right=BinOp(op="=", left=col("z"), right=lit_int(3)),
    )
    assert parse_where("SELECT a FROM t WHERE x = 1 AND y = 2 AND z = 3") == expected


def test_lowercase_boolean_operands_in_comparison() -> None:
    assert parse_where("SELECT a FROM t WHERE flag = true") == BinOp(
        op="=", left=col("flag"), right=UnresolvedLiteralExpr(value=True, type="bool")
    )
