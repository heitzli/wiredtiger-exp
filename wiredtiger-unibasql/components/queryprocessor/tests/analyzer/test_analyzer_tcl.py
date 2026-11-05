"""Analyzer tests for TCL: transaction statements lowering to TCL nodes."""

import pytest

from components.queryprocessor import QueryAnalyzer
from components.queryprocessor.expression import BinOp, ColumnRef, LiteralExpr
from components.queryprocessor.unibasql_algebra import (
    Difference,
    Projection,
    ResolvedColumn,
    ResolvedTable,
    Selection,
    TableScan,
)
from components.queryprocessor.unibasql_tcl_nodes import TransactionNode, TxEndNode, TxStartNode
from components.queryprocessor.unibasql_unresolved import (
    DataType,
    DMLOp,
    EndTransaction,
    StartTransaction,
    Transaction,
    TxResult,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedDQL,
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


def test_start_transaction() -> None:
    assert QueryAnalyzer().analyze_query(StartTransaction(tx_id="t1")) == TxStartNode(tx_id="t1")


def test_end_transaction() -> None:
    assert QueryAnalyzer().analyze_query(EndTransaction(tx_result=TxResult.COMMIT)) == TxEndNode(
        tx_result=TxResult.COMMIT
    )


def test_end_transaction_rollback() -> None:
    assert QueryAnalyzer().analyze_query(EndTransaction(tx_result=TxResult.ROLLBACK)) == TxEndNode(
        tx_result=TxResult.ROLLBACK
    )


@pytest.mark.parametrize("tx_result", [TxResult.COMMIT, TxResult.ROLLBACK])
def test_transaction_analyzes_statements_recursively(tx_result: TxResult) -> None:
    query = Transaction(
        tx_start=StartTransaction(tx_id="t1"),
        tx_end=EndTransaction(tx_result=tx_result),
        statements=[
            UnresolvedDQL(projection=[UnresolvedColumn(name="id")], table=[UnresolvedTable(name="employees")]),
            UnresolvedDML(
                op=DMLOp.DELETE,
                table=UnresolvedTable(name="employees"),
                expr=BinOp(op="=", left=UnresolvedColumn(name="id"), right=UnresolvedLiteralExpr(value=7, type="int")),
            ),
        ],
    )
    scan = TableScan(table=employees_table())
    expected = TransactionNode(
        tx_start=TxStartNode(tx_id="t1"),
        tx_end=TxEndNode(tx_result=tx_result),
        statements=[
            Projection(
                columns=(ResolvedColumn(name="id", qualifier="employees", type=DataType.INT),),
                child=scan,
            ),
            Difference(
                left=scan,
                right=Selection(
                    expr=BinOp(
                        op="=",
                        left=ColumnRef(name="id", qualifier="employees"),
                        right=LiteralExpr(type="int", value=7),
                    ),
                    child=TableScan(table=employees_table()),
                ),
                custom={"source": "delete"},
            ),
        ],
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_transaction_without_statements() -> None:
    query = Transaction(
        tx_start=StartTransaction(tx_id="t2"),
        tx_end=EndTransaction(tx_result=TxResult.ROLLBACK),
        statements=[],
    )
    expected = TransactionNode(
        tx_start=TxStartNode(tx_id="t2"),
        tx_end=TxEndNode(tx_result=TxResult.ROLLBACK),
        statements=[],
    )
    assert QueryAnalyzer().analyze_query(query) == expected


def test_transaction_error_inside_statement_propagates() -> None:
    query = Transaction(
        tx_start=StartTransaction(tx_id="t3"),
        tx_end=EndTransaction(tx_result=TxResult.COMMIT),
        statements=[UnresolvedDQL(projection="*", table=[UnresolvedTable(name="ghost")])],
    )
    with pytest.raises(ValueError, match=r"Table ghost does not exist"):
        QueryAnalyzer().analyze_query(query)
