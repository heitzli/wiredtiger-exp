"""Analyzer tests for DML: INSERT / UPDATE / DELETE lowering to algebra nodes."""

import pytest

from components.queryprocessor import QueryAnalyzer
from components.queryprocessor.expression import BinOp, ColumnRef, LiteralExpr
from components.queryprocessor.unibasql_algebra import (
    Difference,
    ResolvedColumn,
    ResolvedTable,
    ResolvedValue,
    Selection,
    TableScan,
    TupleUpdate,
    Union,
    VirtualTableScan,
)
from components.queryprocessor.unibasql_unresolved import (
    AllowedValue,
    DataType,
    DMLOp,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedLiteralExpr,
    UnresolvedTable,
)


def employees_schema(qualifier: str | None) -> tuple[ResolvedColumn, ...]:
    return (
        ResolvedColumn(name="id", type=DataType.INT, qualifier=qualifier),
        ResolvedColumn(name="name", type=DataType.STRING, qualifier=qualifier),
        ResolvedColumn(name="salary", type=DataType.DOUBLE, qualifier=qualifier),
        ResolvedColumn(name="active", type=DataType.BOOL, qualifier=qualifier),
        ResolvedColumn(name="department", type=DataType.INT, qualifier=qualifier),
    )


def employees_table(alias: str | None = None) -> ResolvedTable:
    # Catalog schema columns carry qualifier = alias or table name (consistency fix).
    return ResolvedTable(name="employees", alias=alias, table_schema=employees_schema(alias or "employees"))


def departments_table() -> ResolvedTable:
    return ResolvedTable(
        name="departments",
        alias=None,
        table_schema=(
            ResolvedColumn(name="id", type=DataType.INT, qualifier="departments"),
            ResolvedColumn(name="name", type=DataType.STRING, qualifier="departments"),
        ),
    )


def employees_col(name: str, type_: DataType) -> ResolvedColumn:
    """An employees column as resolved for explicit projections/assignments (qualifier=table name)."""
    return ResolvedColumn(name=name, qualifier="employees", type=type_)


def ucol(name: str, qualifier: str | None = None) -> UnresolvedColumn:
    return UnresolvedColumn(name=name, qualifier=qualifier)


def ulit(value: AllowedValue, type_: str) -> UnresolvedLiteralExpr:
    return UnresolvedLiteralExpr(value=value, type=type_)


def rv(value: AllowedValue, type_: DataType) -> ResolvedValue:
    return ResolvedValue(type=type_, value=value)


def test_insert_full_row() -> None:
    query = UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="employees"),
        values=[[ulit(1, "int"), ulit("x", "string"), ulit(2.5, "double"), ulit(True, "bool"), ulit(3, "int")]],
    )
    expected = Union(
        left=TableScan(table=employees_table()),
        right=VirtualTableScan(
            values=[
                (
                    rv(1, DataType.INT),
                    rv("x", DataType.STRING),
                    rv(2.5, DataType.DOUBLE),
                    rv(True, DataType.BOOL),
                    rv(3, DataType.INT),
                )
            ]
        ),
        custom={"source": "insert"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_insert_multiple_rows() -> None:
    query = UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="departments"),
        values=[[ulit(1, "int"), ulit("a", "string")], [ulit(2, "int"), ulit("b", "string")]],
    )
    expected = Union(
        left=TableScan(table=departments_table()),
        right=VirtualTableScan(
            values=[
                (rv(1, DataType.INT), rv("a", DataType.STRING)),
                (rv(2, DataType.INT), rv("b", DataType.STRING)),
            ]
        ),
        custom={"source": "insert"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_insert_with_column_list_pads_missing_with_typed_none() -> None:
    # Missing columns are padded with ResolvedValue(type=<column type>, value=None)
    # in catalog order.
    query = UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="employees"),
        columns=[ucol("id"), ucol("name")],
        values=[[ulit(1, "int"), ulit("x", "string")]],
    )
    expected = Union(
        left=TableScan(table=employees_table()),
        right=VirtualTableScan(
            values=[
                (
                    rv(1, DataType.INT),
                    rv("x", DataType.STRING),
                    rv(None, DataType.DOUBLE),  # salary
                    rv(None, DataType.BOOL),  # active
                    rv(None, DataType.INT),  # department
                )
            ]
        ),
        custom={"source": "insert"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_insert_values_placed_by_column_name_not_order() -> None:
    query = UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="employees"),
        columns=[ucol("name"), ucol("id")],
        values=[[ulit("x", "string"), ulit(7, "int")]],
    )
    expected = Union(
        left=TableScan(table=employees_table()),
        right=VirtualTableScan(
            values=[
                (
                    rv(7, DataType.INT),
                    rv("x", DataType.STRING),
                    rv(None, DataType.DOUBLE),
                    rv(None, DataType.BOOL),
                    rv(None, DataType.INT),
                )
            ]
        ),
        custom={"source": "insert"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_insert_tuple_length_mismatch_raises() -> None:
    query = UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="employees"),
        columns=[ucol("id"), ucol("name")],
        values=[[ulit(1, "int")]],
    )
    with pytest.raises(ValueError, match="does not match length of insert tuple"):
        QueryAnalyzer().analyze_query(query)


def test_insert_null_literal_takes_target_column_type() -> None:
    # Regression: NULL literals are typed "null" by the parser and used to crash the
    # DataType(...) conversion; a NULL now resolves to the target column's type.
    query = UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="employees"),
        columns=[ucol("id")],
        values=[[ulit(None, "null")]],
    )
    expected = Union(
        left=TableScan(table=employees_table()),
        right=VirtualTableScan(
            values=[
                (
                    rv(None, DataType.INT),  # id (inserted NULL)
                    rv(None, DataType.STRING),  # name (padded)
                    rv(None, DataType.DOUBLE),  # salary (padded)
                    rv(None, DataType.BOOL),  # active (padded)
                    rv(None, DataType.INT),  # department (padded)
                )
            ]
        ),
        custom={"source": "insert"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_insert_null_in_full_row_takes_target_column_type() -> None:
    query = UnresolvedDML(
        op=DMLOp.INSERT,
        table=UnresolvedTable(name="employees"),
        values=[[ulit(1, "int"), ulit(None, "null"), ulit(2.5, "double"), ulit(True, "bool"), ulit(3, "int")]],
    )
    expected = Union(
        left=TableScan(table=employees_table()),
        right=VirtualTableScan(
            values=[
                (
                    rv(1, DataType.INT),
                    rv(None, DataType.STRING),  # NULL name takes the column's type
                    rv(2.5, DataType.DOUBLE),
                    rv(True, DataType.BOOL),
                    rv(3, DataType.INT),
                )
            ]
        ),
        custom={"source": "insert"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_update_with_where() -> None:
    query = UnresolvedDML(
        op=DMLOp.UPDATE,
        table=UnresolvedTable(name="employees"),
        setlist=[(ucol("salary"), ulit(1.5, "double"))],
        expr=BinOp(op="=", left=ucol("id"), right=ulit(3, "int")),
    )
    scan = TableScan(table=employees_table())
    selected = Selection(
        expr=BinOp(op="=", left=ColumnRef(name="id", qualifier="employees"), right=LiteralExpr(type="int", value=3)),
        child=scan,
    )
    expected = Union(
        left=Difference(left=scan, right=selected),
        right=TupleUpdate(
            assignments=((employees_col("salary", DataType.DOUBLE), LiteralExpr(type="double", value=1.5)),),
            child=selected,
        ),
        custom={"source": "update"},
    )
    result = QueryAnalyzer().analyze_query(query)
    assert result == expected
    # The scan/selection nodes are shared by reference between the two union branches.
    assert isinstance(result, Union)
    assert result.left.right is result.right.child
    assert result.left.right.child is result.left.left


def test_update_multiple_assignments_preserve_order() -> None:
    query = UnresolvedDML(
        op=DMLOp.UPDATE,
        table=UnresolvedTable(name="employees"),
        setlist=[(ucol("salary"), ulit(9.9, "double")), (ucol("active"), ulit(False, "bool"))],
    )
    scan = TableScan(table=employees_table())
    expected = Union(
        left=Difference(left=scan, right=scan),
        right=TupleUpdate(
            assignments=(
                (employees_col("salary", DataType.DOUBLE), LiteralExpr(type="double", value=9.9)),
                (employees_col("active", DataType.BOOL), LiteralExpr(type="bool", value=False)),
            ),
            child=scan,
        ),
        custom={"source": "update"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_update_without_where() -> None:
    # Without WHERE the right side of the difference is the scan itself, so the update
    # applies to every tuple.
    query = UnresolvedDML(
        op=DMLOp.UPDATE,
        table=UnresolvedTable(name="employees"),
        setlist=[(ucol("salary"), ulit(1.0, "double"))],
    )
    scan = TableScan(table=employees_table())
    expected = Union(
        left=Difference(left=scan, right=scan),
        right=TupleUpdate(
            assignments=((employees_col("salary", DataType.DOUBLE), LiteralExpr(type="double", value=1.0)),),
            child=scan,
        ),
        custom={"source": "update"},
    )
    result = QueryAnalyzer().analyze_query(query)
    assert result == expected
    assert isinstance(result, Union)
    # Without WHERE the "remaining" and "updated" sides share the very same scan node.
    assert result.left.left is result.left.right is result.right.child
    # The TupleUpdate literal kind now matches the class name.
    assert result.right.kind == "TupleUpdate"


def test_delete_with_where() -> None:
    query = UnresolvedDML(
        op=DMLOp.DELETE,
        table=UnresolvedTable(name="employees"),
        expr=BinOp(op="=", left=ucol("active"), right=ulit(False, "bool")),
    )
    expected = Difference(
        left=TableScan(table=employees_table()),
        right=Selection(
            expr=BinOp(
                op="=",
                left=ColumnRef(name="active", qualifier="employees"),
                right=LiteralExpr(type="bool", value=False),
            ),
            child=TableScan(table=employees_table()),
        ),
        custom={"source": "delete"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_delete_without_where() -> None:
    query = UnresolvedDML(op=DMLOp.DELETE, table=UnresolvedTable(name="employees"))
    expected = Difference(
        left=TableScan(table=employees_table()),
        right=TableScan(table=employees_table()),
        custom={"source": "delete"},
    )
    assert QueryAnalyzer().analyze_query(query) == expected
