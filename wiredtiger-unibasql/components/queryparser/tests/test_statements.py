"""Tests for top-level statement lists and the statement dispatch alternatives."""

from typing import cast

from components.queryparser.unibasql_parser import parse_unibasql
from components.queryparser.unibasql_unresolved import (
    AbstractStatement,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedDQL,
    UnresolvedTable,
    UnresolvedTableDDL,
)


def test_single_statement() -> None:
    result = parse_unibasql("SELECT a FROM t")
    assert result == [UnresolvedDQL(projection=[UnresolvedColumn(name="a")], table=[UnresolvedTable(name="t")])]


def test_trailing_semicolon_is_optional() -> None:
    assert parse_unibasql("SELECT a FROM t;") == parse_unibasql("SELECT a FROM t")


def test_multiple_statements_in_order() -> None:
    sql = "CREATE TABLE t (a INT PRIMARY KEY); SELECT a FROM t; DELETE FROM t"
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    statements = cast(list[AbstractStatement], result)
    assert len(statements) == 3
    assert isinstance(statements[0], UnresolvedTableDDL)
    assert isinstance(statements[1], UnresolvedDQL)
    assert isinstance(statements[2], UnresolvedDML)
    assert statements[0].table.name == "t"
    assert statements[1].table[0].name == "t"
    assert statements[2].table.name == "t"


def test_statements_with_trailing_semicolon() -> None:
    sql = "SELECT a FROM t; DELETE FROM t;"
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    assert len(result) == 2
    assert isinstance(cast(AbstractStatement, result[0]), UnresolvedDQL)
    assert isinstance(cast(AbstractStatement, result[1]), UnresolvedDML)


def test_statement_list_requires_semicolon_separator() -> None:
    # Without a SEMI the second statement must not be silently absorbed into the first.
    result = parse_unibasql("SELECT a, b, c FROM t")
    assert isinstance(result, list)
    statement = cast(UnresolvedDQL, result[0])
    assert len(cast(list, statement.projection)) == 3
