"""Tests for lexer/parser error handling via the raising error listener."""

import pytest

from components.errors import UnibaSQLSyntaxError
from components.queryprocessor.unibasql_parser import parse_unibasql


@pytest.mark.parametrize(
    "sql",
    [
        "",  # empty input: query rule requires one alternative
        "foo bar",  # unknown tokens
        "SELECT",  # incomplete select
        "SELECT a FROM",  # missing table list
        "SELECT a FROM t WHERE",  # missing expression
        "(x = 1",  # unbalanced parens
        "CREATE TABLE (x INT PRIMARY KEY)",  # missing table name
        "CREATE TABLE t ()",  # empty table definition
        "SELECT * FROM",  # STAR without FROM body
        "UPDATE t",  # missing SET
        "SELECT a FROM t WHERE x = 1.",  # NUMBER requires digits after the dot
        "BEGIN TRANSACTION x; COMMIT;",  # statement list requires at least one statement
    ],
)
def test_syntax_errors(sql: str) -> None:
    with pytest.raises(UnibaSQLSyntaxError):
        parse_unibasql(sql)


def test_lexer_error() -> None:
    # '"' is not part of any lexer rule, so token recognition fails.
    with pytest.raises(UnibaSQLSyntaxError):
        parse_unibasql('SELECT a FROM t WHERE x = "y"')


def test_error_message_contains_location_parser() -> None:
    with pytest.raises(UnibaSQLSyntaxError) as excinfo:
        parse_unibasql("SELECT")
    assert excinfo.value.message.startswith("Syntax error at line 1, column ")


def test_error_message_contains_location_lexer() -> None:
    with pytest.raises(UnibaSQLSyntaxError) as excinfo:
        parse_unibasql('SELECT a FROM t WHERE x = "y"')
    assert excinfo.value.message.startswith("Syntax error at line 1, column ")


def test_error_message_reports_line_number() -> None:
    with pytest.raises(UnibaSQLSyntaxError) as excinfo:
        parse_unibasql("SELECT a FROM t;\nSELECT b FROM u;\nflurb")
    assert excinfo.value.message.startswith("Syntax error at line 3, column ")
