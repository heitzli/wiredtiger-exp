"""Tests for transaction statements: BEGIN TRANSACTION, COMMIT and ROLLBACK."""

import uuid
from typing import cast

from components.queryprocessor.unibasql_parser import parse_unibasql
from components.queryprocessor.unibasql_unresolved import (
    ColumnConstraint,
    DataType,
    DDLOp,
    DMLOp,
    EndTransaction,
    StartTransaction,
    TableDefTriple,
    Transaction,
    TxResult,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedDQL,
    UnresolvedLiteralExpr,
    UnresolvedTable,
    UnresolvedTableDDL,
)


def test_begin_transaction_unnamed_generates_uuid() -> None:
    result = parse_unibasql("BEGIN TRANSACTION;")
    assert isinstance(result, StartTransaction)
    # No name was given, so a UUID must have been generated.
    assert str(uuid.UUID(result.tx_id)) == result.tx_id


def test_begin_transaction_unnamed_without_semicolon() -> None:
    result = parse_unibasql("BEGIN TRANSACTION")
    assert isinstance(result, StartTransaction)
    assert str(uuid.UUID(result.tx_id)) == result.tx_id


def test_begin_transaction_named() -> None:
    result = parse_unibasql("BEGIN TRANSACTION tx1;")
    assert result == StartTransaction(tx_id="tx1")


def test_begin_transaction_named_lowercase() -> None:
    result = parse_unibasql("begin transaction tx1")
    assert isinstance(result, StartTransaction)
    assert result.tx_id == "tx1"


def test_commit() -> None:
    assert parse_unibasql("COMMIT;") == EndTransaction(tx_result=TxResult.COMMIT)


def test_commit_without_semicolon() -> None:
    assert parse_unibasql("COMMIT") == EndTransaction(tx_result=TxResult.COMMIT)


def test_commit_lowercase() -> None:
    assert parse_unibasql("commit;") == EndTransaction(tx_result=TxResult.COMMIT)


def test_rollback() -> None:
    assert parse_unibasql("ROLLBACK;") == EndTransaction(tx_result=TxResult.ROLLBACK)


def test_rollback_without_semicolon() -> None:
    assert parse_unibasql("ROLLBACK") == EndTransaction(tx_result=TxResult.ROLLBACK)


def test_rollback_lowercase() -> None:
    assert parse_unibasql("rollback;") == EndTransaction(tx_result=TxResult.ROLLBACK)


def test_full_transaction_with_mixed_statement_types() -> None:
    sql = "BEGIN TRANSACTION tx1; CREATE TABLE users (id INT PRIMARY KEY); INSERT INTO users VALUES (1); SELECT a FROM x; ROLLBACK;"
    result = parse_unibasql(sql)
    assert isinstance(result, Transaction)
    assert result == Transaction(
        tx_start=StartTransaction(tx_id="tx1"),
        tx_end=EndTransaction(tx_result=TxResult.ROLLBACK),
        statements=[
            UnresolvedTableDDL(
                op=DDLOp.CREATE,
                table=UnresolvedTable(name="users"),
                table_schema=[
                    TableDefTriple(
                        column=UnresolvedColumn(name="id"),
                        datatype=DataType.INT,
                        constraint=ColumnConstraint.PRIMARY_KEY,
                    )
                ],
            ),
            UnresolvedDML(
                op=DMLOp.INSERT,
                table=UnresolvedTable(name="users"),
                values=[[UnresolvedLiteralExpr(value=1, type="int")]],
            ),
            UnresolvedDQL(
                projection=[UnresolvedColumn(name="a")],
                table=[UnresolvedTable(name="x")],
            ),
        ],
    )


def test_full_transaction_commit_with_trailing_semicolon() -> None:
    sql = "BEGIN TRANSACTION t1; SELECT a FROM x; COMMIT;"
    result = parse_unibasql(sql)
    assert isinstance(result, Transaction)
    assert cast(Transaction, result).tx_start.tx_id == "t1"
    assert cast(Transaction, result).tx_end == EndTransaction(tx_result=TxResult.COMMIT)
    assert len(cast(Transaction, result).statements) == 1
