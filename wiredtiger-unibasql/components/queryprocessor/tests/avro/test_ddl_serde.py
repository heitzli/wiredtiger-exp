"""Avro serde tests: AbstractDDLNode.to_avro / from_avro round-trips.

The wire shape is the generic DDLNode record from ``protocol/ddlnode.avdl``
(compiled to ``protocol/ddlnode.avsc`` via ``protocol/compile.sh``). DDL
statements are wire leaves: children() is always [] and the record has no
node-valued fields.
"""

from typing import TypeAlias

import pytest

from components.queryprocessor import QueryAnalyzer, parse_unibasql
from components.queryprocessor.unibasql_algebra import ResolvedColumn, ResolvedTable
from components.queryprocessor.unibasql_ddl_nodes import AbstractDDLNode, DDLNode, IndexDDL, TableDDL
from components.queryprocessor.unibasql_unresolved import ColumnConstraint, DataType, DDLOp

Node: TypeAlias = AbstractDDLNode


def employees_schema() -> tuple[ResolvedColumn, ...]:
    return (
        ResolvedColumn(name="id", type=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY),
        ResolvedColumn(name="name", type=DataType.STRING, constraint=ColumnConstraint.NOT_NULL),
        ResolvedColumn(name="score", type=DataType.DOUBLE),
    )


def employees_table(alias: str | None = None) -> ResolvedTable:
    return ResolvedTable(name="employees", alias=alias, table_schema=employees_schema())


def create_table() -> TableDDL:
    return TableDDL(op=DDLOp.CREATE, table=employees_table())


def drop_table() -> TableDDL:
    return TableDDL(op=DDLOp.DROP, table=employees_table(alias="e"))


def index_columns() -> tuple[ResolvedColumn, ...]:
    return (employees_schema()[0], ResolvedColumn(name="score", type=DataType.DOUBLE))


def create_index() -> IndexDDL:
    return IndexDDL(op=DDLOp.CREATE, idx_name="emp_score", table=employees_table(), columns=index_columns())


def drop_index() -> IndexDDL:
    return IndexDDL(op=DDLOp.DROP, idx_name="emp_score", table=employees_table())


def create_table_custom() -> TableDDL:
    return TableDDL(op=DDLOp.CREATE, table=employees_table(), custom={"source": "ddl"})


# --- datum shape (to_avro, no binary encoding involved) ----------------------


def test_to_avro_create_table_datum_shape() -> None:
    # The avro record always carries every field; unused ones are None.
    datum = create_table().to_avro()
    assert datum == {
        "kind": "TableDDL",
        "table": {
            "kind": "ResolvedTable",
            "name": "employees",
            "tableSchema": [
                {
                    "kind": "ResolvedColumn",
                    "name": "id",
                    "type": "INT",
                    "constraint": "PRIMARY_KEY",
                    "qualifier": None,
                    "custom": None,
                },
                {
                    "kind": "ResolvedColumn",
                    "name": "name",
                    "type": "STRING",
                    "constraint": "NOT_NULL",
                    "qualifier": None,
                    "custom": None,
                },
                {
                    "kind": "ResolvedColumn",
                    "name": "score",
                    "type": "DOUBLE",
                    "constraint": None,
                    "qualifier": None,
                    "custom": None,
                },
            ],
            "alias": None,
            "custom": None,
        },
        "op": "CREATE",
        "idx_name": None,
        "columns": None,
        "custom": {},  # FIX: wire custom map is non-optional; root carries {}, inner rows stay None
    }


def test_to_avro_drop_table_datum_shape() -> None:
    datum = drop_table().to_avro()
    assert datum["kind"] == "TableDDL"
    assert datum["op"] == "DROP"
    assert datum["table"]["alias"] == "e"
    assert datum["idx_name"] is None
    assert datum["columns"] is None


def test_to_avro_create_index_datum_shape() -> None:
    datum = create_index().to_avro()
    assert datum["kind"] == "IndexDDL"
    assert datum["op"] == "CREATE"
    assert datum["idx_name"] == "emp_score"
    assert datum["table"]["name"] == "employees"
    assert datum["columns"] == [
        {
            "kind": "ResolvedColumn",
            "name": "id",
            "type": "INT",
            "constraint": "PRIMARY_KEY",
            "qualifier": None,
            "custom": None,
        },
        {
            "kind": "ResolvedColumn",
            "name": "score",
            "type": "DOUBLE",
            "constraint": None,
            "qualifier": None,
            "custom": None,
        },
    ]


def test_to_avro_drop_index_has_null_columns() -> None:
    datum = drop_index().to_avro()
    assert datum["op"] == "DROP"
    assert datum["columns"] is None


def test_to_avro_inner_records_carry_null_custom() -> None:
    # `custom` belongs to the statement node; the embedded ResolvedTable/ResolvedColumn
    # wire copies always carry custom=None (matches the algebra serde).
    datum = create_table_custom().to_avro()
    assert datum["custom"] == {"source": "ddl"}
    assert datum["table"]["custom"] is None
    assert all(column["custom"] is None for column in datum["table"]["tableSchema"])


# --- round-trip matrix over hand-built statements -----------------------------


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param(create_table(), id="TableDDL create"),
        pytest.param(drop_table(), id="TableDDL drop"),
        pytest.param(create_index(), id="IndexDDL create"),
        pytest.param(drop_index(), id="IndexDDL drop"),
        pytest.param(create_table_custom(), id="TableDDL custom source"),
    ],
)
def test_round_trip_bytes(statement: Node) -> None:
    assert AbstractDDLNode.from_avro(statement.to_avro()) == statement


def test_round_trip_datum_direct() -> None:
    statement = create_index()
    assert AbstractDDLNode.from_avro(statement.to_avro()) == statement


def test_round_trip_restores_columns_tuple_and_enums() -> None:
    round_tripped = AbstractDDLNode.from_avro(create_index().to_avro())
    assert isinstance(round_tripped, IndexDDL)
    assert isinstance(round_tripped.columns, tuple)
    assert round_tripped.columns[0].type is DataType.INT
    assert round_tripped.columns[0].constraint is ColumnConstraint.PRIMARY_KEY


def test_op_enum_commutes_via_symbol_names() -> None:
    # The wire op is the avro enum symbol string; decode maps it back to the StrEnum member.
    datum = create_table().to_avro()
    assert datum["op"] == "CREATE"
    assert isinstance(datum["op"], str)
    assert AbstractDDLNode.from_avro(datum).op is DDLOp.CREATE


# --- generic node wire compatibility -----------------------------------------


def test_minimal_generic_ddl_node_round_trips() -> None:
    # kind-only statements (everything else null) are valid wire datums.
    node = DDLNode(kind="Placeholder")
    assert AbstractDDLNode.from_avro(node.to_avro()) == node


def test_generic_ddl_node_round_trips() -> None:
    # The generic node mirrors the wire record 1:1; every populated field round-trips.
    node = DDLNode(
        kind="FutureThing",
        op=DDLOp.CREATE,
        table=employees_table(),
        idx_name="some_idx",
        columns=index_columns(),
        custom={"source": "future"},
    )
    assert AbstractDDLNode.from_avro(node.to_avro()) == node
    assert AbstractDDLNode.from_avro(node.to_avro()) == node


def test_generic_node_with_registered_kind_decodes_to_concrete() -> None:
    # Encoding a generic node with a registered kind produces the same wire datum a
    # concrete node would; the reader reconstructs the concrete class.
    node = DDLNode(kind="TableDDL", op=DDLOp.DROP, table=employees_table(), custom={"source": "ddl"})
    round_tripped = AbstractDDLNode.from_avro(node.to_avro())
    assert round_tripped == TableDDL(op=DDLOp.DROP, table=employees_table(), custom={"source": "ddl"})


def test_unknown_kind_decodes_to_generic_ddl_node() -> None:
    # Forward compatibility: a reader that does not know the kind still receives the statement.
    datum = create_table().to_avro()
    datum["kind"] = "FutureDDL"
    round_tripped = AbstractDDLNode.from_avro(datum)
    assert isinstance(round_tripped, DDLNode)
    assert round_tripped.kind == "FutureDDL"
    assert round_tripped.op is DDLOp.CREATE
    assert round_tripped.table == create_table().table
    assert round_tripped.columns is None
    assert round_tripped.idx_name is None


def test_concrete_ddl_node_kinds_decode_to_typed_classes() -> None:
    # Dispatch reaches each concrete kind; the generic DDLNode (kind without a literal default)
    # is reserved for forward compatibility.
    assert type(AbstractDDLNode.from_avro(create_table().to_avro())) is TableDDL
    assert type(AbstractDDLNode.from_avro(create_index().to_avro())) is IndexDDL


# --- analyzer-produced statements, end to end ---------------------------------


def test_analyzer_create_table_round_trips() -> None:
    statements = parse_unibasql("CREATE TABLE users (id INT PRIMARY KEY, name STRING NOT NULL)")
    node = QueryAnalyzer().analyze_query(statements[0])
    assert isinstance(node, TableDDL)
    assert AbstractDDLNode.from_avro(node.to_avro()) == node


def test_analyzer_create_index_round_trips() -> None:
    statements = parse_unibasql("CREATE INDEX emp_idx ON employees (id, name)")
    node = QueryAnalyzer().analyze_query(statements[0])
    assert isinstance(node, IndexDDL)
    assert AbstractDDLNode.from_avro(node.to_avro()) == node


# --- error paths ---------------------------------------------------------------


def test_from_avro_unknown_op_symbol_raises() -> None:
    datum = create_table().to_avro()
    datum["op"] = "GRANT"
    with pytest.raises(KeyError):
        AbstractDDLNode.from_avro(datum)


def test_from_avro_missing_kind_raises() -> None:
    with pytest.raises(KeyError):
        AbstractDDLNode.from_avro({})
