"""Analyzer error behavior: exact ValueError messages and the silent-None fallthrough."""

import pytest

from components.queryprocessor import QueryAnalyzer
from components.queryprocessor.unibasql_unresolved import (
    DMLOp,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedDQL,
    UnresolvedLiteralExpr,
    UnresolvedTable,
)


def ucol(name: str, qualifier: str | None = None) -> UnresolvedColumn:
    return UnresolvedColumn(name=name, qualifier=qualifier)


def ulit(value: bool | int | float | str | None, type_: str) -> UnresolvedLiteralExpr:
    return UnresolvedLiteralExpr(value=value, type=type_)


def test_unknown_table_in_select() -> None:
    query = UnresolvedDQL(projection="*", table=[UnresolvedTable(name="ghost")])
    with pytest.raises(ValueError, match=r"Table ghost does not exist"):
        QueryAnalyzer().analyze_query(query)


def test_unknown_column_in_projection() -> None:
    query = UnresolvedDQL(projection=[ucol("ghost")], table=[UnresolvedTable(name="employees")])
    with pytest.raises(ValueError, match=r"Column ghost does not exist"):
        QueryAnalyzer().analyze_query(query)


def test_unknown_qualified_column_in_projection() -> None:
    query = UnresolvedDQL(projection=[ucol("ghost", "employees")], table=[UnresolvedTable(name="employees")])
    with pytest.raises(ValueError, match=r"Column employees\.ghost does not exist"):
        QueryAnalyzer().analyze_query(query)


def test_ambiguous_column_in_projection() -> None:
    # Both catalog tables define a column named `name`.
    query = UnresolvedDQL(
        projection=[ucol("name")],
        table=[UnresolvedTable(name="employees"), UnresolvedTable(name="departments")],
    )
    with pytest.raises(ValueError, match=r"Column name is ambiguous"):
        QueryAnalyzer().analyze_query(query)


def test_alias_qualifier_mismatch_in_projection() -> None:
    # `employees.salary` cannot be resolved once the table is renamed to `e`.
    query = UnresolvedDQL(
        projection=[ucol("salary", "employees")],
        table=[UnresolvedTable(name="employees", alias="e")],
    )
    with pytest.raises(ValueError, match=r"Column employees\.salary does not exist"):
        QueryAnalyzer().analyze_query(query)


def test_unknown_column_in_where() -> None:
    query = UnresolvedDQL(
        projection=[ucol("id")],
        table=[UnresolvedTable(name="employees")],
        expr=ucol("ghost"),
    )
    with pytest.raises(ValueError, match=r"Cannot resolve column ghost"):
        QueryAnalyzer().analyze_query(query)


def test_ambiguous_column_in_where() -> None:
    # The projection list narrows `id` to the employees table, but `name` is defined by
    # both tables and therefore cannot be resolved.
    query = UnresolvedDQL(
        projection=[ucol("id", "employees")],
        table=[UnresolvedTable(name="employees"), UnresolvedTable(name="departments")],
        expr=ucol("name"),
    )
    with pytest.raises(ValueError, match=r"Cannot resolve column name"):
        QueryAnalyzer().analyze_query(query)


def test_unknown_table_in_dml() -> None:
    query = UnresolvedDML(op=DMLOp.INSERT, table=UnresolvedTable(name="ghost"), values=[[ulit(1, "int")]])
    with pytest.raises(ValueError, match=r"Table ghost does not exist"):
        QueryAnalyzer().analyze_query(query)


def test_unknown_column_in_setlist() -> None:
    query = UnresolvedDML(
        op=DMLOp.UPDATE,
        table=UnresolvedTable(name="employees"),
        setlist=[(ucol("ghost"), ulit(1, "int"))],
    )
    with pytest.raises(ValueError, match=r"Column ghost does not exist"):
        QueryAnalyzer().analyze_query(query)


def test_analyze_query_rejects_unrecognized_statement() -> None:
    # Regression: analyze_query used to silently return None for statements it cannot lower.
    with pytest.raises(TypeError, match="Cannot analyze statement of type UnresolvedColumn"):
        QueryAnalyzer().analyze_query(UnresolvedColumn(name="id"))
