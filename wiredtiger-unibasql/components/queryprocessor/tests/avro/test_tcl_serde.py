"""Avro serde tests: AbstractTCLNode.to_avro / from_avro round-trips.

The wire shape is the generic TCLNode record from ``protocol/tclnode.avdl``
(compiled to ``protocol/tclnode.avsc`` via ``protocol/compile.sh``). Transaction
nodes are the first composite wire type: their statements nest algebra/DDL/TCL
records through avro unions (``{"AlgebraNode"|"DDLNode"|"TCLNode": ...}``).
"""

from typing import TypeAlias

import avro.errors
import pytest

from components.queryprocessor import QueryAnalyzer, parse_unibasql
from components.queryprocessor.expression import BinOp, ColumnRef, LiteralExpr
from components.queryprocessor.unibasql_algebra import (
    Difference,
    ResolvedColumn,
    ResolvedTable,
    Selection,
    TableScan,
)
from components.queryprocessor.unibasql_ddl_nodes import TableDDL
from components.queryprocessor.unibasql_tcl_nodes import (
    AbstractTCLNode,
    TCLNode,
    TransactionNode,
    TxEndNode,
    TxStartNode,
)
from components.queryprocessor.unibasql_unresolved import ColumnConstraint, DataType, DDLOp, TxResult

Node: TypeAlias = AbstractTCLNode


def employees_table() -> ResolvedTable:
    return ResolvedTable(
        name="employees",
        alias=None,
        table_schema=(
            ResolvedColumn(
                name="id", type=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY, qualifier="employees"
            ),
        ),
    )


def create_table() -> TableDDL:
    return TableDDL(op=DDLOp.CREATE, table=employees_table())


def scan() -> TableScan:
    return TableScan(table=employees_table())


def selection() -> Selection:
    return Selection(
        expr=BinOp(op="=", left=ColumnRef(name="id", qualifier="employees"), right=LiteralExpr(type="int", value=7)),
        child=scan(),
    )


def difference() -> Difference:
    return Difference(left=scan(), right=selection(), custom={"source": "delete"})


def txn(tx_id: str = "t1", statements: list | None = None) -> TransactionNode:
    return TransactionNode(
        tx_start=TxStartNode(tx_id=tx_id),
        tx_end=TxEndNode(tx_result=TxResult.ROLLBACK),
        statements=statements if statements is not None else [],
    )


# --- datum shape (to_avro, no binary encoding involved) ----------------------


def test_to_avro_tx_start_datum_shape() -> None:
    datum = TxStartNode(tx_id="t1").to_avro()
    assert datum == {
        "kind": "TxStartNode",
        "tx_id": "t1",
        "tx_result": None,
        "tx_start": None,
        "tx_end": None,
        "statements": None,
        "custom": None,
    }


def test_to_avro_tx_end_datum_shape() -> None:
    # The wire tx_result is the avro enum symbol string.
    datum = TxEndNode(tx_result=TxResult.COMMIT).to_avro()
    assert datum["kind"] == "TxEndNode"
    assert datum["tx_result"] == "COMMIT"
    assert isinstance(datum["tx_result"], str)
    assert datum["tx_id"] is None
    assert datum["statements"] is None


def test_to_avro_transaction_datum_shape() -> None:
    datum = txn(statements=[scan(), create_table()]).to_avro()
    assert datum["kind"] == "TransactionNode"
    assert datum["tx_start"] == {
        "kind": "TxStartNode",
        "tx_id": "t1",
        "tx_result": None,
        "tx_start": None,
        "tx_end": None,
        "statements": None,
        "custom": None,
    }
    assert datum["tx_end"]["tx_result"] == "ROLLBACK"
    # Statements ride the union as bare records; branch validation disambiguates the flavor.
    assert datum["statements"][0]["kind"] == "TableScan"
    assert "tx_id" not in datum["statements"][0]
    assert datum["statements"][1]["kind"] == "TableDDL"
    assert "idx_name" in datum["statements"][1]


def test_to_avro_empty_transaction_writes_empty_statements() -> None:
    # An empty statement list is a present-but-empty array on the wire, not null.
    datum = txn().to_avro()
    assert datum["statements"] == []


# --- round-trip matrix over hand-built transactions ---------------------------


@pytest.mark.parametrize(
    "node",
    [
        pytest.param(TxStartNode(tx_id="t1"), id="TxStartNode"),
        pytest.param(TxEndNode(tx_result=TxResult.COMMIT), id="TxEndNode commit"),
        pytest.param(TxEndNode(tx_result=TxResult.ROLLBACK), id="TxEndNode rollback"),
        pytest.param(txn(), id="TransactionNode empty"),
        pytest.param(txn(statements=[scan()]), id="TransactionNode algebra stmt"),
        pytest.param(txn(statements=[difference()]), id="TransactionNode custom stmt"),
        pytest.param(txn(statements=[create_table()]), id="TransactionNode ddl stmt"),
        pytest.param(txn(statements=[txn("nested", [scan()])]), id="TransactionNode nested txn"),
        pytest.param(
            TransactionNode(
                tx_start=TxStartNode(tx_id="t1"),
                tx_end=TxEndNode(tx_result=TxResult.COMMIT),
                statements=[scan(), create_table(), txn("t2")],
                custom={"source": "transaction"},
            ),
            id="TransactionNode mixed + custom",
        ),
    ],
)
def test_round_trip_bytes(node: Node) -> None:
    assert AbstractTCLNode.from_avro_bytes(node.to_avro_bytes()) == node


def test_round_trip_datum_direct() -> None:
    node = txn(statements=[scan(), create_table()])
    assert AbstractTCLNode.from_avro(node.to_avro()) == node


def test_round_trip_restores_statement_union_flavors() -> None:
    round_tripped = AbstractTCLNode.from_avro_bytes(txn(statements=[scan(), create_table()]).to_avro_bytes())
    assert isinstance(round_tripped.statements[0], TableScan)
    assert isinstance(round_tripped.statements[1], TableDDL)


def test_tx_result_enum_commutes_via_symbol_names() -> None:
    round_tripped = AbstractTCLNode.from_avro_bytes(TxEndNode(tx_result=TxResult.ROLLBACK).to_avro_bytes())
    assert round_tripped.tx_result is TxResult.ROLLBACK


# --- generic node wire compatibility -----------------------------------------


def test_minimal_generic_tcl_node_round_trips() -> None:
    # kind-only nodes (everything else null) are valid wire datums.
    node = TCLNode(kind="Placeholder")
    assert AbstractTCLNode.from_avro_bytes(node.to_avro_bytes()) == node


def test_generic_tcl_node_round_trips() -> None:
    node = TCLNode(
        kind="FutureThing",
        tx_id="tx9",
        tx_result=TxResult.COMMIT,
        tx_start=TxStartNode(tx_id="t1"),
        tx_end=TxEndNode(tx_result=TxResult.ROLLBACK),
        statements=[scan(), txn("nested")],
        custom={"source": "future"},
    )
    assert AbstractTCLNode.from_avro(node.to_avro()) == node
    assert AbstractTCLNode.from_avro_bytes(node.to_avro_bytes()) == node


def test_generic_node_with_registered_kind_decodes_to_concrete() -> None:
    # Encoding a generic node with a registered kind produces the same wire datum a
    # concrete node would; the reader reconstructs the concrete class.
    node = TCLNode(kind="TxStartNode", tx_id="t7", custom={"source": "begin"})
    round_tripped = AbstractTCLNode.from_avro_bytes(node.to_avro_bytes())
    assert round_tripped == TxStartNode(tx_id="t7", custom={"source": "begin"})


def test_unknown_kind_decodes_to_generic_tcl_node() -> None:
    # Forward compatibility: a reader that does not know the kind still receives the transaction.
    datum = txn(statements=[scan()]).to_avro()
    datum["kind"] = "SavePointNode"
    round_tripped = AbstractTCLNode.from_avro(datum)
    assert isinstance(round_tripped, TCLNode)
    assert round_tripped.kind == "SavePointNode"
    assert round_tripped.tx_start == txn().tx_start
    assert round_tripped.tx_end == txn().tx_end
    assert round_tripped.statements == [scan()]
    assert round_tripped.tx_id is None
    assert round_tripped.tx_result is None


def test_concrete_tcl_node_kinds_are_registered() -> None:
    # Concrete node kinds self-register for avro decode dispatch (__pydantic_init_subclass__);
    # the generic TCLNode (kind without a literal default) is excluded.
    assert set(AbstractTCLNode._AVRO_TCL_NODE_KINDS) == {"TxStartNode", "TxEndNode", "TransactionNode"}


# --- analyzer-produced transactions, end to end --------------------------------


def parse_and_analyze(sql: str) -> Node:
    statements = parse_unibasql(sql)
    # Transaction SQL parses into a single Transaction object (not a statement list).
    return QueryAnalyzer().analyze_query(statements[0] if isinstance(statements, list) else statements)


def test_analyzer_delete_transaction_round_trips() -> None:
    node = parse_and_analyze("BEGIN TRANSACTION t1; DELETE FROM employees WHERE id = 7; ROLLBACK;")
    assert isinstance(node, TransactionNode)
    round_tripped = AbstractTCLNode.from_avro_bytes(node.to_avro_bytes())
    assert round_tripped == node
    assert round_tripped.tx_start.tx_id == "t1"
    assert round_tripped.tx_end.tx_result is TxResult.ROLLBACK


def test_analyzer_transaction_statements_decode_to_their_codec() -> None:
    node = parse_and_analyze("BEGIN TRANSACTION t1; DELETE FROM employees WHERE id = 7; ROLLBACK;")
    round_tripped = AbstractTCLNode.from_avro_bytes(node.to_avro_bytes())
    statement = round_tripped.statements[0]
    assert isinstance(statement, Difference)
    assert statement.custom == {"source": "delete"}
    assert isinstance(statement.left, TableScan)
    assert statement.right.expr.right == LiteralExpr(type="int", value=7)


# --- error paths ----------------------------------------------------------------


def test_from_avro_unknown_statement_branch_raises() -> None:
    node = txn(statements=[scan()])
    datum = node.to_avro()
    datum["statements"][0] = {"mystery_key": 1}
    with pytest.raises(ValueError, match="Not a valid transaction statement datum"):
        AbstractTCLNode.from_avro(datum)


def test_from_avro_unknown_nested_node_kind_raises_into_generic() -> None:
    # An unknown TCL kind nested as tx_start still decodes (into a generic TCLNode).
    node = TCLNode(kind="Outer", tx_start=TCLNode(kind="DeepProtocol"))
    round_tripped = AbstractTCLNode.from_avro_bytes(node.to_avro_bytes())
    assert isinstance(round_tripped.tx_start, TCLNode)
    assert round_tripped.tx_start.kind == "DeepProtocol"


@pytest.mark.parametrize("raw", [pytest.param(b"", id="empty"), pytest.param(b"\x99garbage\x00\xff", id="garbage")])
def test_from_avro_bytes_malformed_input_raises(raw: bytes) -> None:
    with pytest.raises(avro.errors.InvalidAvroBinaryEncoding):
        AbstractTCLNode.from_avro_bytes(raw)


def test_from_avro_bytes_truncated_input_raises() -> None:
    raw = txn(statements=[scan()]).to_avro_bytes()
    with pytest.raises(avro.errors.InvalidAvroBinaryEncoding):
        AbstractTCLNode.from_avro_bytes(raw[:-1])
