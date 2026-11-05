"""Analyzer tests for DDL: CREATE/DROP TABLE and CREATE/DROP INDEX lowering."""

import pytest

from components.queryprocessor import QueryAnalyzer
from components.queryprocessor.unibasql_algebra import ResolvedColumn, ResolvedTable
from components.queryprocessor.unibasql_ddl_nodes import IndexDDL, TableDDL
from components.queryprocessor.unibasql_unresolved import (
    ColumnConstraint,
    DataType,
    DDLOp,
    TableDefTriple,
    UnresolvedColumn,
    UnresolvedIndexDDL,
    UnresolvedTable,
    UnresolvedTableDDL,
)


def rc(
    name: str,
    type_: DataType,
    constraint: ColumnConstraint | None = None,
    qualifier: str | None = None,
) -> ResolvedColumn:
    return ResolvedColumn(name=name, type=type_, constraint=constraint, qualifier=qualifier)


def rc_qualified(name: str, type_: DataType) -> ResolvedColumn:
    """A column as resolved for explicit index projections (qualifier=table name)."""
    return ResolvedColumn(name=name, type=type_, qualifier="employees")


def employees_schema() -> tuple[ResolvedColumn, ...]:
    # Catalog schema columns carry qualifier = table name for unaliased tables (consistency fix).
    return (
        rc("id", DataType.INT, qualifier="employees"),
        rc("name", DataType.STRING, qualifier="employees"),
        rc("salary", DataType.DOUBLE, qualifier="employees"),
        rc("active", DataType.BOOL, qualifier="employees"),
        rc("department", DataType.INT, qualifier="employees"),
    )


def test_create_table_resolves_schema_and_constraints() -> None:
    query = UnresolvedTableDDL(
        op=DDLOp.CREATE,
        table=UnresolvedTable(name="users"),
        table_schema=[
            TableDefTriple(
                column=UnresolvedColumn(name="id"), datatype=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY
            ),
            TableDefTriple(column=UnresolvedColumn(name="name"), datatype=DataType.STRING),
        ],
    )
    expected = TableDDL(
        op=DDLOp.CREATE,
        table=ResolvedTable(
            name="users",
            alias=None,
            table_schema=(
                rc("id", DataType.INT, ColumnConstraint.PRIMARY_KEY),
                rc("name", DataType.STRING),
            ),
        ),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_create_table_existing_raises() -> None:
    query = UnresolvedTableDDL(op=DDLOp.CREATE, table=UnresolvedTable(name="employees"))
    with pytest.raises(ValueError, match="Table employees already exists"):
        QueryAnalyzer().analyze_query(query)


def test_drop_table_resolves_with_catalog_schema() -> None:
    query = UnresolvedTableDDL(op=DDLOp.DROP, table=UnresolvedTable(name="departments"))
    expected = TableDDL(
        op=DDLOp.DROP,
        table=ResolvedTable(
            name="departments",
            alias=None,
            table_schema=(
                rc("id", DataType.INT, qualifier="departments"),
                rc("name", DataType.STRING, qualifier="departments"),
            ),
        ),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_drop_unknown_table_raises() -> None:
    query = UnresolvedTableDDL(op=DDLOp.DROP, table=UnresolvedTable(name="ghost"))
    with pytest.raises(ValueError, match=r"Table ghost does not exist"):
        QueryAnalyzer().analyze_query(query)


def test_create_index_resolves_columns() -> None:
    query = UnresolvedIndexDDL(
        op=DDLOp.CREATE,
        idx_name="emp_salary_idx",
        table=UnresolvedTable(name="employees"),
        columns=[UnresolvedColumn(name="salary")],
    )
    expected = IndexDDL(
        op=DDLOp.CREATE,
        idx_name="emp_salary_idx",
        table=ResolvedTable(name="employees", alias=None, table_schema=employees_schema()),
        columns=(rc_qualified("salary", DataType.DOUBLE),),
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_create_index_multiple_columns_preserve_order() -> None:
    query = UnresolvedIndexDDL(
        op=DDLOp.CREATE,
        idx_name="emp_dept_salary_idx",
        table=UnresolvedTable(name="employees"),
        columns=[UnresolvedColumn(name="department"), UnresolvedColumn(name="salary")],
    )
    result = QueryAnalyzer().analyze_query(query)
    assert isinstance(result, IndexDDL)
    assert result.columns == (
        rc_qualified("department", DataType.INT),
        rc_qualified("salary", DataType.DOUBLE),
    )


def test_create_index_name_colliding_with_table_name_raises() -> None:
    # Index names and table names share one mock-catalog namespace, so "departments"
    # collides with the departments table and the error says so explicitly.
    query = UnresolvedIndexDDL(
        op=DDLOp.CREATE,
        idx_name="departments",
        table=UnresolvedTable(name="employees"),
        columns=[UnresolvedColumn(name="salary")],
    )
    with pytest.raises(ValueError, match=r"Index name departments collides with table departments"):
        QueryAnalyzer().analyze_query(query)


def test_create_index_name_colliding_with_existing_index_raises() -> None:
    # An empty schema entry marks a registered index in the mock catalog.
    analyzer = QueryAnalyzer(mock_catalog={"employees": {"id": DataType.INT}, "emp_salary_idx": {}})
    query = UnresolvedIndexDDL(
        op=DDLOp.CREATE,
        idx_name="emp_salary_idx",
        table=UnresolvedTable(name="employees"),
        columns=[UnresolvedColumn(name="id")],
    )
    with pytest.raises(ValueError, match=r"Index emp_salary_idx already exists"):
        analyzer.analyze_query(query)


def test_create_index_unknown_column_raises() -> None:
    query = UnresolvedIndexDDL(
        op=DDLOp.CREATE,
        idx_name="emp_idx",
        table=UnresolvedTable(name="employees"),
        columns=[UnresolvedColumn(name="ghost")],
    )
    with pytest.raises(ValueError, match=r"Column ghost does not exist"):
        QueryAnalyzer().analyze_query(query)


def test_drop_index_positive_with_custom_catalog() -> None:
    # DROP INDEX only checks idx_name membership in the catalog, so the positive case
    # needs a catalog that pre-registers the index name.
    analyzer = QueryAnalyzer(mock_catalog={"users": {"id": DataType.INT, "name": DataType.STRING}, "users_idx": {}})
    query = UnresolvedIndexDDL(op=DDLOp.DROP, idx_name="users_idx", table=UnresolvedTable(name="users"))
    expected = IndexDDL(
        op=DDLOp.DROP,
        idx_name="users_idx",
        table=ResolvedTable(
            name="users",
            alias=None,
            table_schema=(
                rc("id", DataType.INT, qualifier="users"),
                rc("name", DataType.STRING, qualifier="users"),
            ),
        ),
        columns=None,
    )
    assert analyzer.analyze_query(query) == expected


def test_drop_index_missing_raises() -> None:
    query = UnresolvedIndexDDL(op=DDLOp.DROP, idx_name="ghost_idx", table=UnresolvedTable(name="employees"))
    with pytest.raises(ValueError, match=r"Index ghost_idx does not exist"):
        QueryAnalyzer().analyze_query(query)
