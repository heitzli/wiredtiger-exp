"""Analyzer tests for DQL: SELECT lowering to Projection / Selection / Cross.

The unresolved statements are constructed directly (without the parser) so these tests
isolate the analyzer; end-to-end parser -> analyzer coverage lives in
``components/queryparser/tests/test_parser_analyzer.py``.
"""

from components.queryprocessor import QueryAnalyzer
from components.queryprocessor.expression import BinOp, ColumnRef, LiteralExpr, UnOp
from components.queryprocessor.unibasql_algebra import (
    Cross,
    Projection,
    ResolvedColumn,
    ResolvedTable,
    Selection,
    TableScan,
)
from components.queryprocessor.unibasql_unresolved import (
    AllowedValue,
    DataType,
    UnresolvedColumn,
    UnresolvedDQL,
    UnresolvedLiteralExpr,
    UnresolvedTable,
)


def employees_schema(qualifier: str | None) -> tuple[ResolvedColumn, ...]:
    """The employees catalog schema as the analyzer resolves it for the given qualifier."""
    return (
        ResolvedColumn(name="id", type=DataType.INT, qualifier=qualifier),
        ResolvedColumn(name="name", type=DataType.STRING, qualifier=qualifier),
        ResolvedColumn(name="salary", type=DataType.DOUBLE, qualifier=qualifier),
        ResolvedColumn(name="active", type=DataType.BOOL, qualifier=qualifier),
        ResolvedColumn(name="department", type=DataType.INT, qualifier=qualifier),
    )


def departments_schema(qualifier: str | None) -> tuple[ResolvedColumn, ...]:
    return (
        ResolvedColumn(name="id", type=DataType.INT, qualifier=qualifier),
        ResolvedColumn(name="name", type=DataType.STRING, qualifier=qualifier),
    )


def employees_table(alias: str | None = None) -> ResolvedTable:
    # Catalog schema columns carry qualifier = alias or table name (consistency fix).
    return ResolvedTable(name="employees", alias=alias, table_schema=employees_schema(alias or "employees"))


def departments_table(alias: str | None = None) -> ResolvedTable:
    return ResolvedTable(name="departments", alias=alias, table_schema=departments_schema(alias or "departments"))


def ucol(name: str, qualifier: str | None = None) -> UnresolvedColumn:
    return UnresolvedColumn(name=name, qualifier=qualifier)


def ulit(value: AllowedValue, type_: str) -> UnresolvedLiteralExpr:
    return UnresolvedLiteralExpr(value=value, type=type_)


def test_select_star_single_table() -> None:
    query = UnresolvedDQL(projection="*", table=[UnresolvedTable(name="employees")])
    expected = Projection(columns=employees_schema("employees"), child=TableScan(table=employees_table()))
    assert QueryAnalyzer().analyze_query(query) == expected


def test_select_star_with_alias() -> None:
    # Star expansion picks up the alias as qualifier for every expanded column.
    query = UnresolvedDQL(projection="*", table=[UnresolvedTable(name="employees", alias="e")])
    expected = Projection(columns=employees_schema("e"), child=TableScan(table=employees_table("e")))
    assert QueryAnalyzer().analyze_query(query) == expected


def test_select_star_multiple_tables_concatenates_schemas() -> None:
    query = UnresolvedDQL(
        projection="*",
        table=[UnresolvedTable(name="employees"), UnresolvedTable(name="departments")],
    )
    expected = Projection(
        columns=(*employees_schema("employees"), *departments_schema("departments")),
        child=Cross(left=TableScan(table=employees_table()), right=TableScan(table=departments_table())),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_explicit_columns_get_table_name_qualifier() -> None:
    # Unaliased tables qualify explicitly selected columns with the table name; star
    # expansion produces the same qualifiers (see the consistency test below).
    query = UnresolvedDQL(projection=[ucol("id"), ucol("salary")], table=[UnresolvedTable(name="employees")])
    expected = Projection(
        columns=(
            ResolvedColumn(name="id", qualifier="employees", type=DataType.INT),
            ResolvedColumn(name="salary", qualifier="employees", type=DataType.DOUBLE),
        ),
        child=TableScan(table=employees_table()),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_qualified_columns_with_alias() -> None:
    query = UnresolvedDQL(
        projection=[ucol("salary", "e")],
        table=[UnresolvedTable(name="employees", alias="e")],
    )
    expected = Projection(
        columns=(ResolvedColumn(name="salary", qualifier="e", type=DataType.DOUBLE),),
        child=TableScan(table=employees_table("e")),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_column_from_second_table() -> None:
    query = UnresolvedDQL(projection=[ucol("name")], table=[UnresolvedTable(name="departments")])
    expected = Projection(
        columns=(ResolvedColumn(name="name", qualifier="departments", type=DataType.STRING),),
        child=TableScan(table=departments_table()),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_projection_preserves_column_types() -> None:
    query = UnresolvedDQL(
        projection=[ucol("id"), ucol("name"), ucol("salary"), ucol("active")],
        table=[UnresolvedTable(name="employees")],
    )
    expected = Projection(
        columns=(
            ResolvedColumn(name="id", qualifier="employees", type=DataType.INT),
            ResolvedColumn(name="name", qualifier="employees", type=DataType.STRING),
            ResolvedColumn(name="salary", qualifier="employees", type=DataType.DOUBLE),
            ResolvedColumn(name="active", qualifier="employees", type=DataType.BOOL),
        ),
        child=TableScan(table=employees_table()),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_star_expansion_matches_explicit_column_qualifier() -> None:
    # Regression: star expansion used to yield qualifier=None while explicitly selected
    # columns carried the table name as qualifier; both now agree on the qualifier.
    star = QueryAnalyzer().analyze_query(UnresolvedDQL(projection="*", table=[UnresolvedTable(name="employees")]))
    explicit = QueryAnalyzer().analyze_query(
        UnresolvedDQL(projection=[ucol("id")], table=[UnresolvedTable(name="employees")])
    )
    assert isinstance(star, Projection) and isinstance(explicit, Projection)
    id_column = ResolvedColumn(name="id", qualifier="employees", type=DataType.INT)
    assert star.columns[0] == id_column
    assert explicit.columns[0] == id_column


def test_where_lowered_to_selection_below_projection() -> None:
    query = UnresolvedDQL(
        projection=[ucol("id")],
        table=[UnresolvedTable(name="employees")],
        expr=BinOp(op="=", left=ucol("salary"), right=ulit(1.5, "double")),
    )
    expected = Projection(
        columns=(ResolvedColumn(name="id", qualifier="employees", type=DataType.INT),),
        child=Selection(
            expr=BinOp(
                op="=",
                left=ColumnRef(name="salary", qualifier="employees"),
                right=LiteralExpr(type="double", value=1.5),
            ),
            child=TableScan(table=employees_table()),
        ),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_where_unresolved_column_gets_table_qualifier() -> None:
    # An unqualified WHERE column is resolved against the table and qualified with the
    # table name in the resulting ColumnRef.
    query = UnresolvedDQL(
        projection=[ucol("id")],
        table=[UnresolvedTable(name="employees")],
        expr=ucol("active"),
    )
    expected = Projection(
        columns=(ResolvedColumn(name="id", qualifier="employees", type=DataType.INT),),
        child=Selection(expr=ColumnRef(name="active", qualifier="employees"), child=TableScan(table=employees_table())),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_where_with_alias_qualifier_and_nested_ops() -> None:
    query = UnresolvedDQL(
        projection=[ucol("salary", "e")],
        table=[UnresolvedTable(name="employees", alias="e")],
        expr=BinOp(op="AND", left=ucol("active", "e"), right=UnOp(op="NOT", expr=ucol("active", "e"))),
    )
    expected = Projection(
        columns=(ResolvedColumn(name="salary", qualifier="e", type=DataType.DOUBLE),),
        child=Selection(
            expr=BinOp(
                op="AND",
                left=ColumnRef(name="active", qualifier="e"),
                right=UnOp(op="NOT", expr=ColumnRef(name="active", qualifier="e")),
            ),
            child=TableScan(table=employees_table("e")),
        ),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_where_literal_only() -> None:
    query = UnresolvedDQL(
        projection=[ucol("id")],
        table=[UnresolvedTable(name="employees")],
        expr=ulit(True, "bool"),
    )
    expected = Projection(
        columns=(ResolvedColumn(name="id", qualifier="employees", type=DataType.INT),),
        child=Selection(expr=LiteralExpr(type="bool", value=True), child=TableScan(table=employees_table())),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_cross_two_tables() -> None:
    query = UnresolvedDQL(
        projection=[ucol("salary", "employees")],
        table=[UnresolvedTable(name="employees"), UnresolvedTable(name="departments")],
    )
    expected = Projection(
        columns=(ResolvedColumn(name="salary", qualifier="employees", type=DataType.DOUBLE),),
        child=Cross(left=TableScan(table=employees_table()), right=TableScan(table=departments_table())),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_cross_three_tables_is_left_associative() -> None:
    query = UnresolvedDQL(
        projection=[ucol("name", "departments")],
        table=[
            UnresolvedTable(name="employees"),
            UnresolvedTable(name="departments"),
            UnresolvedTable(name="employees", alias="e"),
        ],
    )
    expected = Projection(
        columns=(ResolvedColumn(name="name", qualifier="departments", type=DataType.STRING),),
        child=Cross(
            left=Cross(left=TableScan(table=employees_table()), right=TableScan(table=departments_table())),
            right=TableScan(table=employees_table("e")),
        ),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_duplicate_projection_items_are_kept() -> None:
    # Current behavior: each projection item is resolved independently, so duplicated
    # columns stay duplicated in the projection attribute list.
    query = UnresolvedDQL(projection=[ucol("id"), ucol("id")], table=[UnresolvedTable(name="employees")])
    id_column = ResolvedColumn(name="id", qualifier="employees", type=DataType.INT)
    expected = Projection(columns=(id_column, id_column), child=TableScan(table=employees_table()))
    assert QueryAnalyzer().analyze_query(query) == expected
