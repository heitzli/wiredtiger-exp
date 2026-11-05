"""Tests for DQL (SELECT) statements: projections, table refs and WHERE clauses."""

from components.queryprocessor.expression import BinOp
from components.queryprocessor.unibasql_parser import parse_unibasql
from components.queryprocessor.unibasql_unresolved import (
    UnresolvedColumn,
    UnresolvedDQL,
    UnresolvedLiteralExpr,
    UnresolvedTable,
)


def parse_single_select(sql: str) -> UnresolvedDQL:
    """Parse ``sql`` and assert it yields exactly one unresolved SELECT statement."""
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    assert len(result) == 1
    statement = result[0]
    assert isinstance(statement, UnresolvedDQL)
    return statement


def test_select_star_single_table() -> None:
    assert parse_single_select("SELECT * FROM users") == UnresolvedDQL(
        projection="*", table=[UnresolvedTable(name="users")]
    )


def test_select_lowercase_keywords() -> None:
    # The grammar is declared caseInsensitive, so keyword case must not matter.
    assert parse_single_select("select * from users") == UnresolvedDQL(
        projection="*", table=[UnresolvedTable(name="users")]
    )


def test_select_mixed_case_keywords() -> None:
    assert parse_single_select("SeLeCt a FrOm t") == UnresolvedDQL(
        projection=[UnresolvedColumn(name="a")], table=[UnresolvedTable(name="t")]
    )


def test_select_without_where() -> None:
    # Regression: `if ctx.whereClause` used to test a bound method (always truthy) and
    # crashed with an AttributeError on WHERE-less selects.
    assert parse_single_select("SELECT a FROM t") == UnresolvedDQL(
        projection=[UnresolvedColumn(name="a")], table=[UnresolvedTable(name="t")]
    )


def test_underscore_identifier() -> None:
    # IDs match [a-z_][a-z_0-9]* (case-insensitively): leading underscore and digits allowed.
    assert parse_single_select("SELECT _c_1 FROM t") == UnresolvedDQL(
        projection=[UnresolvedColumn(name="_c_1")], table=[UnresolvedTable(name="t")]
    )


def test_select_single_column() -> None:
    assert parse_single_select("SELECT name FROM users") == UnresolvedDQL(
        projection=[UnresolvedColumn(name="name")], table=[UnresolvedTable(name="users")]
    )


def test_select_multiple_columns_with_qualifier() -> None:
    assert parse_single_select("SELECT name, u.age FROM users u") == UnresolvedDQL(
        projection=[UnresolvedColumn(name="name"), UnresolvedColumn(name="age", qualifier="u")],
        table=[UnresolvedTable(name="users", alias="u")],
    )


def test_table_ref_alias_with_as() -> None:
    assert parse_single_select("SELECT a FROM t AS x") == UnresolvedDQL(
        projection=[UnresolvedColumn(name="a")], table=[UnresolvedTable(name="t", alias="x")]
    )


def test_table_ref_alias_without_as() -> None:
    assert parse_single_select("SELECT a FROM t x") == UnresolvedDQL(
        projection=[UnresolvedColumn(name="a")], table=[UnresolvedTable(name="t", alias="x")]
    )


def test_select_multiple_tables() -> None:
    assert parse_single_select("SELECT a FROM t1, t2 AS b, t3") == UnresolvedDQL(
        projection=[UnresolvedColumn(name="a")],
        table=[
            UnresolvedTable(name="t1"),
            UnresolvedTable(name="t2", alias="b"),
            UnresolvedTable(name="t3"),
        ],
    )


def test_select_qualified_projection_and_where() -> None:
    sql = "SELECT u.id, u.name FROM users AS u WHERE u.id = 1"
    assert parse_single_select(sql) == UnresolvedDQL(
        projection=[UnresolvedColumn(name="id", qualifier="u"), UnresolvedColumn(name="name", qualifier="u")],
        table=[UnresolvedTable(name="users", alias="u")],
        expr=BinOp(
            op="=",
            left=UnresolvedColumn(name="id", qualifier="u"),
            right=UnresolvedLiteralExpr(value=1, type="int"),
        ),
    )
