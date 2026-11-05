"""Avro serde tests: AbstractAlgebraNode.to_avro / from_avro round-trips.

The wire shape is the generic AlgebraNode record from ``protocol/algebranode.avdl``
(compiled to ``protocol/algebranode.avsc`` via ``protocol/compile.sh``).
"""

from typing import TypeAlias

import avro.errors
import pytest

from components.queryprocessor import QueryAnalyzer, parse_unibasql
from components.queryprocessor.expression import BinOp, ColumnRef, LiteralExpr, TableRef, UnOp
from components.queryprocessor.unibasql_algebra import (
    AbstractAlgebraNode,
    AlgebraNode,
    Cross,
    Difference,
    Projection,
    ResolvedColumn,
    ResolvedTable,
    ResolvedValue,
    Selection,
    TableScan,
    TupleUpdate,
    Union,
    VirtualTableScan,
)
from components.queryprocessor.unibasql_unresolved import ColumnConstraint, DataType

Node: TypeAlias = AbstractAlgebraNode


def employees_schema(qualifier: str) -> tuple[ResolvedColumn, ...]:
    return (
        ResolvedColumn(name="id", type=DataType.INT, qualifier=qualifier),
        ResolvedColumn(name="name", type=DataType.STRING, qualifier=qualifier),
        ResolvedColumn(name="active", type=DataType.BOOL, qualifier=qualifier),
    )


def employees_table(alias: str | None = None) -> ResolvedTable:
    return ResolvedTable(name="employees", alias=alias, table_schema=employees_schema(alias or "employees"))


def rc(name: str, type_: DataType, constraint: ColumnConstraint | None = None) -> ResolvedColumn:
    return ResolvedColumn(name=name, type=type_, constraint=constraint, qualifier="employees")


def rv(value: object, type_: DataType) -> ResolvedValue:
    return ResolvedValue(type=type_, value=value)


def where_expr() -> BinOp:
    return BinOp(
        op="=",
        left=ColumnRef(name="active", qualifier="employees"),
        right=LiteralExpr(type="bool", value=False),
    )


def scan() -> TableScan:
    return TableScan(table=employees_table())


def scan_aliased() -> TableScan:
    return TableScan(table=employees_table(alias="e"))


def scan_with_constraints() -> TableScan:
    return TableScan(
        table=ResolvedTable(
            name="users",
            alias=None,
            table_schema=(
                rc("id", DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY),
                rc("nick", DataType.STRING, constraint=ColumnConstraint.NOT_NULL),
            ),
        )
    )


def scan_values() -> VirtualTableScan:
    return VirtualTableScan(
        values=[
            (rv(27, DataType.INT), rv(True, DataType.BOOL), rv(2.5, DataType.DOUBLE), rv("s", DataType.STRING)),
            (rv(None, DataType.INT), rv(None, DataType.BOOL), rv(None, DataType.DOUBLE), rv(None, DataType.STRING)),
        ]
    )


def projection() -> Projection:
    return Projection(columns=employees_schema("employees"), child=scan())


def selection() -> Selection:
    return Selection(expr=where_expr(), child=scan())


def cross() -> Cross:
    return Cross(left=scan(), right=scan_aliased())


def union() -> Union:
    return Union(left=scan(), right=scan_aliased())


def union_custom() -> Union:
    return Union(left=scan(), right=scan_values(), custom={"source": "insert"})


def difference() -> Difference:
    return Difference(left=scan(), right=scan_aliased())


def tuple_update() -> TupleUpdate:
    return TupleUpdate(
        assignments=(
            (rc("nick", DataType.STRING), LiteralExpr(type="string", value="x")),
            (rc("active", DataType.BOOL), UnOp(op="NOT", expr=ColumnRef(name="active", qualifier="employees"))),
        ),
        child=selection(),
    )


def table_ref() -> Selection:
    return Selection(
        expr=BinOp(op="=", left=TableRef(name="employees", alias="e"), right=LiteralExpr(type="bool", value=True)),
        child=scan(),
    )


def parse_and_analyze(sql: str) -> Node:
    statements = parse_unibasql(sql)
    return QueryAnalyzer().analyze_query(statements[0] if isinstance(statements, list) else statements)


# --- datum shape (to_avro, no binary encoding involved) ----------------------


def test_to_avro_table_scan_datum_shape() -> None:
    # The avro record always carries every field; unused ones are None.
    datum = scan_aliased().to_avro()
    assert datum == {
        "kind": "TableScan",
        "table": {
            "kind": "ResolvedTable",
            "name": "employees",
            "tableSchema": [
                {
                    "kind": "ResolvedColumn",
                    "name": "id",
                    "type": "INT",
                    "constraint": None,
                    "qualifier": "e",
                    "custom": None,
                },
                {
                    "kind": "ResolvedColumn",
                    "name": "name",
                    "type": "STRING",
                    "constraint": None,
                    "qualifier": "e",
                    "custom": None,
                },
                {
                    "kind": "ResolvedColumn",
                    "name": "active",
                    "type": "BOOL",
                    "constraint": None,
                    "qualifier": "e",
                    "custom": None,
                },
            ],
            "alias": "e",
            "custom": None,
        },
        "values": None,
        "columns": None,
        "expr": None,
        "left": None,
        "right": None,
        "child": None,
        "assignments": None,
        "custom": None,
    }


def test_to_avro_wraps_expr_in_expr_record() -> None:
    # The avro Expr field is a union wrapper: {"expr": <typed inner record>}.
    datum = selection().to_avro()
    inner = datum["expr"]["expr"]
    assert inner["kind"] == "BinOp"
    assert inner["op"] == "="
    assert inner["left"]["expr"]["kind"] == "ColumnRef"
    assert inner["right"]["expr"] == {"kind": "LiteralExpr", "type": "bool", "value": False, "custom": None}


def test_to_avro_assignments_use_column_and_expr_keys() -> None:
    datum = tuple_update().to_avro()
    first = datum["assignments"][0]
    assert set(first) == {"column", "expr"}
    assert first["column"]["name"] == "nick"
    assert first["expr"]["expr"]["kind"] == "LiteralExpr"


def test_to_avro_values_are_resolved_value_rows() -> None:
    datum = scan_values().to_avro()
    assert datum["values"] == [
        [
            {"kind": "ResolvedValue", "type": "INT", "value": 27, "custom": None},
            {"kind": "ResolvedValue", "type": "BOOL", "value": True, "custom": None},
            {"kind": "ResolvedValue", "type": "DOUBLE", "value": 2.5, "custom": None},
            {"kind": "ResolvedValue", "type": "STRING", "value": "s", "custom": None},
        ],
        [
            {"kind": "ResolvedValue", "type": "INT", "value": None, "custom": None},
            {"kind": "ResolvedValue", "type": "BOOL", "value": None, "custom": None},
            {"kind": "ResolvedValue", "type": "DOUBLE", "value": None, "custom": None},
            {"kind": "ResolvedValue", "type": "STRING", "value": None, "custom": None},
        ],
    ]


def test_generic_algebra_node_round_trips() -> None:
    # The generic node mirrors the wire record 1:1 (incl. child); every populated field
    # round-trips through binary avro.
    node = AlgebraNode(
        kind="FutureThing",
        table=employees_table(),
        values=[(rv(27, DataType.INT),), (rv(None, DataType.STRING),)],
        columns=employees_schema("employees"),
        expr=where_expr(),
        left=scan(),
        right=scan_aliased(),
        child=selection(),
        assignments=((rc("name", DataType.STRING), LiteralExpr(type="string", value="x")),),
        custom={"source": "future"},
    )
    assert AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes()) == node


def test_minimal_generic_algebra_node_round_trips() -> None:
    # kind-only nodes (everything else null) are valid wire datums.
    node = AlgebraNode(kind="Placeholder")
    assert AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes()) == node


def test_generic_node_with_registered_kind_decodes_to_concrete() -> None:
    # Encoding a generic node with a registered kind produces the same wire datum a
    # concrete node would; the reader reconstructs the concrete class.
    node = AlgebraNode(kind="Union", left=scan(), right=scan_aliased(), custom={"source": "delete"})
    round_tripped = AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes())
    assert round_tripped == Union(left=scan(), right=scan_aliased(), custom={"source": "delete"})


def test_unknown_kind_decodes_to_generic_algebra_node() -> None:
    # Forward compatibility: a reader that does not know the kind still receives the plan.
    datum = projection().to_avro()
    datum["kind"] = "FutureNode"
    round_tripped = AbstractAlgebraNode.from_avro(datum)
    assert isinstance(round_tripped, AlgebraNode)
    assert round_tripped.kind == "FutureNode"
    assert round_tripped.columns == projection().columns
    assert round_tripped.child == projection().child


def test_concrete_node_kinds_are_registered() -> None:
    # Concrete node kinds self-register for avro decode dispatch (__pydantic_init_subclass__);
    # the generic AlgebraNode (kind without a literal default) is excluded.
    assert set(AbstractAlgebraNode._AVRO_NODE_KINDS) == {
        "TableScan",
        "VirtualTableScan",
        "Projection",
        "Selection",
        "Cross",
        "Union",
        "Difference",
        "TupleUpdate",
    }


# --- round-trip matrix over hand-built trees ---------------------------------


@pytest.mark.parametrize(
    "node",
    [
        pytest.param(scan(), id="TableScan"),
        pytest.param(scan_aliased(), id="TableScan aliased"),
        pytest.param(scan_with_constraints(), id="TableScan constraints"),
        pytest.param(scan_values(), id="VirtualTableScan"),
        pytest.param(projection(), id="Projection"),
        pytest.param(selection(), id="Selection"),
        pytest.param(cross(), id="Cross"),
        pytest.param(union(), id="Union"),
        pytest.param(union_custom(), id="Union custom source"),
        pytest.param(difference(), id="Difference"),
        pytest.param(tuple_update(), id="TupleUpdate"),
        pytest.param(table_ref(), id="TableRef expr"),
    ],
)
def test_round_trip_bytes(node: Node) -> None:
    assert AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes()) == node


def test_round_trip_datum_direct() -> None:
    node = cross()
    assert AbstractAlgebraNode.from_avro(node.to_avro()) == node


def test_round_trip_preserves_custom_markers() -> None:
    node = union_custom()
    round_tripped = AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes())
    assert round_tripped.custom == {"source": "insert"}


def test_round_trip_preserves_enum_symbols() -> None:
    node = scan_with_constraints()
    round_tripped = AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes())
    column = round_tripped.table.table_schema[0]
    assert column.type is DataType.INT
    assert column.constraint is ColumnConstraint.PRIMARY_KEY


# --- value normalization on decode -------------------------------------------


def test_int_values_decode_back_to_int_not_float() -> None:
    # Ints ride the double branch on the wire (see algebranode.avdl); decode renormalizes.
    round_tripped = AbstractAlgebraNode.from_avro_bytes(scan_values().to_avro_bytes())
    first_row = round_tripped.values[0]
    assert isinstance(first_row[0].value, int)
    assert first_row[0].value == 27
    assert isinstance(first_row[1].value, bool) and first_row[1].value is True
    assert first_row[2].value == 2.5
    assert first_row[3].value == "s"


def test_padded_null_keeps_none_even_for_int_columns() -> None:
    # ResolvedValue(None) means an unbound column; int() normalization must not coerce it to 0.
    round_tripped = AbstractAlgebraNode.from_avro_bytes(scan_values().to_avro_bytes())
    padded = round_tripped.values[1]
    assert [v.value for v in padded] == [None, None, None, None]
    assert [v.type for v in padded] == [DataType.INT, DataType.BOOL, DataType.DOUBLE, DataType.STRING]


def test_int_literal_expr_decodes_to_int() -> None:
    node = Selection(
        expr=BinOp(op="=", left=ColumnRef(name="id", qualifier="employees"), right=LiteralExpr(type="int", value=3)),
        child=scan(),
    )
    round_tripped = AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes())
    literal = round_tripped.expr.right
    assert isinstance(literal, LiteralExpr)
    assert literal.value == 3
    assert isinstance(literal.value, int)  # not 3.0 from the double branch


def test_bool_literal_expr_survives_via_boolean_branch() -> None:
    node = selection()
    round_tripped = AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes())
    literal = round_tripped.expr.right
    assert literal == LiteralExpr(type="bool", value=False)


# --- analyzer-produced trees, end to end -------------------------------------


@pytest.mark.parametrize(
    "sql,custom",
    [
        pytest.param("SELECT * FROM employees", None, id="select star"),
        pytest.param("SELECT name FROM employees WHERE active = TRUE", None, id="select where"),
        pytest.param("SELECT * FROM employees, departments", None, id="cross"),
        pytest.param("SELECT * FROM employees UNION SELECT * FROM departments", None, id="set union"),
        pytest.param("SELECT * FROM employees EXCEPT SELECT * FROM departments", None, id="set except"),
        pytest.param("INSERT INTO employees VALUES (1, 'x', 2.5, TRUE, 3)", {"source": "insert"}, id="insert"),
        pytest.param("INSERT INTO employees (id) VALUES (1)", {"source": "insert"}, id="insert padded"),
        pytest.param("UPDATE employees SET name = 'x' WHERE id = 1", {"source": "update"}, id="update"),
        pytest.param("DELETE FROM employees WHERE active = FALSE", {"source": "delete"}, id="delete where"),
        pytest.param("DELETE FROM employees", {"source": "delete"}, id="delete all"),
    ],
)
def test_analyzer_trees_round_trip(sql: str, custom: dict[str, str] | None) -> None:
    node = parse_and_analyze(sql)
    round_tripped = AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes())
    assert round_tripped == node
    assert round_tripped.custom == custom


def test_update_shared_nodes_survive_as_tree() -> None:
    # Documented loss: the analyzer shares one scan/selection instance across the update's
    # union branches; the decoded tree is equal but built from fresh instances.
    node = parse_and_analyze("UPDATE employees SET name = 'x' WHERE id = 1")
    round_tripped = AbstractAlgebraNode.from_avro_bytes(node.to_avro_bytes())
    assert round_tripped == node
    assert round_tripped.left.right is not round_tripped.right.child


# --- error paths --------------------------------------------------------------


def test_from_avro_unknown_expr_kind_raises() -> None:
    datum = selection().to_avro()
    datum["expr"]["expr"] = {"kind": "Bogus", "op": "=", "left": None, "right": None, "custom": None}
    with pytest.raises(ValueError, match="Unknown expression kind 'Bogus'"):
        AbstractAlgebraNode.from_avro(datum)


@pytest.mark.parametrize("raw", [pytest.param(b"", id="empty"), pytest.param(b"\x99garbage\x00\xff", id="garbage")])
def test_from_avro_bytes_malformed_input_raises(raw: bytes) -> None:
    with pytest.raises(avro.errors.InvalidAvroBinaryEncoding):
        AbstractAlgebraNode.from_avro_bytes(raw)


def test_from_avro_bytes_truncated_input_raises() -> None:
    raw = scan().to_avro_bytes()
    with pytest.raises(avro.errors.InvalidAvroBinaryEncoding):
        AbstractAlgebraNode.from_avro_bytes(raw[:-1])
