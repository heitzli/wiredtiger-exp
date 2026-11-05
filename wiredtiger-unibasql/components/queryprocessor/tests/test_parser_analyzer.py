"""Combined parser + analyzer integration tests: SQL text in, resolved IR out."""

from typing import TypeAlias

import pytest

from components.errors import UnibaSQLSyntaxError
from components.queryprocessor import QueryAnalyzer, parse_unibasql
from components.queryprocessor.expression import BinOp, ColumnRef, LiteralExpr
from components.queryprocessor.unibasql_algebra import (
    AbstractAlgebraNode,
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
from components.queryprocessor.unibasql_ddl_nodes import AbstractDDLNode, IndexDDL, TableDDL
from components.queryprocessor.unibasql_tcl_nodes import AbstractTCLNode, TransactionNode, TxEndNode, TxStartNode
from components.queryprocessor.unibasql_unresolved import (
    AbstractStatement,
    ColumnConstraint,
    DataType,
    DDLOp,
    EndTransaction,
    StartTransaction,
    Transaction,
    TxResult,
    UnresolvedDQL,
)

AnalyzedResult: TypeAlias = AbstractAlgebraNode | AbstractDDLNode | AbstractTCLNode


def employees_schema(qualifier: str | None) -> tuple[ResolvedColumn, ...]:
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


def rv(value: bool | int | float | str | None, type_: DataType) -> ResolvedValue:
    return ResolvedValue(type=type_, value=value)


def parse_one(sql: str) -> AbstractStatement:
    """Parse ``sql`` and assert it yields exactly one statement."""
    result = parse_unibasql(sql)
    assert isinstance(result, list)
    assert len(result) == 1
    return result[0]


def parse_and_analyze(sql: str, analyzer: QueryAnalyzer | None = None) -> AnalyzedResult:
    return (analyzer or QueryAnalyzer()).analyze_query(parse_one(sql))  # type: ignore


def test_select_star_end_to_end() -> None:
    result = parse_and_analyze("SELECT * FROM employees")
    expected = Projection(columns=employees_schema("employees"), child=TableScan(table=employees_table()))
    assert result == expected


def test_select_qualified_alias_where_end_to_end() -> None:
    result = parse_and_analyze("SELECT e.salary FROM employees AS e WHERE e.active = TRUE")
    expected = Projection(
        columns=(ResolvedColumn(name="salary", qualifier="e", type=DataType.DOUBLE),),
        child=Selection(
            expr=BinOp(
                op="=", left=ColumnRef(name="active", qualifier="e"), right=LiteralExpr(type="bool", value=True)
            ),
            child=TableScan(table=employees_table("e")),
        ),
    )
    assert result == expected


def test_select_join_two_tables_end_to_end() -> None:
    result = parse_and_analyze(
        "SELECT departments.name FROM employees, departments WHERE employees.department = departments.id"
    )
    expected = Projection(
        columns=(ResolvedColumn(name="name", qualifier="departments", type=DataType.STRING),),
        child=Selection(
            expr=BinOp(
                op="=",
                left=ColumnRef(name="department", qualifier="employees"),
                right=ColumnRef(name="id", qualifier="departments"),
            ),
            child=Cross(left=TableScan(table=employees_table()), right=TableScan(table=departments_table())),
        ),
    )
    assert result == expected


def test_union_end_to_end() -> None:
    result = parse_and_analyze("SELECT name FROM employees UNION SELECT name FROM departments")
    expected = Union(
        left=Projection(
            columns=(ResolvedColumn(name="name", qualifier="employees", type=DataType.STRING),),
            child=TableScan(table=employees_table()),
        ),
        right=Projection(
            columns=(ResolvedColumn(name="name", qualifier="departments", type=DataType.STRING),),
            child=TableScan(table=departments_table()),
        ),
    )
    assert result == expected


def test_except_end_to_end() -> None:
    result = parse_and_analyze("SELECT id FROM employees EXCEPT SELECT id FROM departments")
    expected = Difference(
        left=Projection(
            columns=(ResolvedColumn(name="id", qualifier="employees", type=DataType.INT),),
            child=TableScan(table=employees_table()),
        ),
        right=Projection(
            columns=(ResolvedColumn(name="id", qualifier="departments", type=DataType.INT),),
            child=TableScan(table=departments_table()),
        ),
    )
    assert result == expected


def test_insert_with_column_list_end_to_end() -> None:
    result = parse_and_analyze("INSERT INTO employees (id, name) VALUES (1, 'x')")
    expected = Union(
        left=TableScan(table=employees_table()),
        right=VirtualTableScan(
            values=[
                (
                    rv(1, DataType.INT),
                    rv("x", DataType.STRING),
                    rv(None, DataType.DOUBLE),
                    rv(None, DataType.BOOL),
                    rv(None, DataType.INT),
                )
            ]
        ),
        custom={"source": "insert"},
    )
    assert result == expected


def test_insert_full_row_end_to_end() -> None:
    result = parse_and_analyze("INSERT INTO employees VALUES (1, 'x', 2.5, TRUE, 3)")
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
    assert result == expected


def test_update_with_where_end_to_end() -> None:
    result = parse_and_analyze("UPDATE employees SET salary = 1.5 WHERE id = 3")
    scan = TableScan(table=employees_table())
    selected = Selection(
        expr=BinOp(op="=", left=ColumnRef(name="id", qualifier="employees"), right=LiteralExpr(type="int", value=3)),
        child=scan,
    )
    expected = Union(
        left=Difference(left=scan, right=selected),
        right=TupleUpdate(
            assignments=(
                (
                    ResolvedColumn(name="salary", qualifier="employees", type=DataType.DOUBLE),
                    LiteralExpr(type="double", value=1.5),
                ),
            ),
            child=selected,
        ),
        custom={"source": "update"},
    )
    assert result == expected
    assert isinstance(result, Union)
    assert result.left.right is result.right.child


def test_update_without_where_end_to_end() -> None:
    result = parse_and_analyze("UPDATE employees SET salary = 2.0")
    scan = TableScan(table=employees_table())
    expected = Union(
        left=Difference(left=scan, right=scan),
        right=TupleUpdate(
            assignments=(
                (
                    ResolvedColumn(name="salary", qualifier="employees", type=DataType.DOUBLE),
                    LiteralExpr(type="double", value=2.0),
                ),
            ),
            child=scan,
        ),
        custom={"source": "update"},
    )
    assert result == expected


def test_delete_with_where_end_to_end() -> None:
    result = parse_and_analyze("DELETE FROM employees WHERE active = FALSE")
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
    assert result == expected


def test_delete_without_where_end_to_end() -> None:
    result = parse_and_analyze("DELETE FROM employees")
    expected = Difference(
        left=TableScan(table=employees_table()),
        right=TableScan(table=employees_table()),
        custom={"source": "delete"},
    )
    assert result == expected


def test_create_then_drop_table_round_trip() -> None:
    analyzer = QueryAnalyzer()
    created = parse_and_analyze("CREATE TABLE users (id INT PRIMARY KEY, name STRING NOT NULL)", analyzer)
    assert created == TableDDL(
        op=DDLOp.CREATE,
        table=ResolvedTable(
            name="users",
            alias=None,
            table_schema=(
                ResolvedColumn(name="id", type=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY),
                ResolvedColumn(name="name", type=DataType.STRING, constraint=ColumnConstraint.NOT_NULL),
            ),
        ),
    )
    # Analysis is side-effect free: the created table is not registered in the catalog.
    with pytest.raises(ValueError, match=r"Table users does not exist"):
        parse_and_analyze("DROP TABLE users", analyzer)
    # Pre-registering the table in a custom catalog makes the DROP resolve.
    catalog_analyzer = QueryAnalyzer(mock_catalog={"users": {"id": DataType.INT, "name": DataType.STRING}})
    dropped = parse_and_analyze("DROP TABLE users", catalog_analyzer)
    assert dropped == TableDDL(
        op=DDLOp.DROP,
        table=ResolvedTable(
            name="users",
            alias=None,
            table_schema=(
                ResolvedColumn(name="id", type=DataType.INT, qualifier="users"),
                ResolvedColumn(name="name", type=DataType.STRING, qualifier="users"),
            ),
        ),
    )


def test_create_index_end_to_end() -> None:
    result = parse_and_analyze("CREATE INDEX emp_idx ON employees (salary, active)")
    expected = IndexDDL(
        op=DDLOp.CREATE,
        idx_name="emp_idx",
        table=employees_table(),
        columns=(
            ResolvedColumn(name="salary", qualifier="employees", type=DataType.DOUBLE),
            ResolvedColumn(name="active", qualifier="employees", type=DataType.BOOL),
        ),
    )
    assert result == expected


def test_drop_index_end_to_end_with_custom_catalog() -> None:
    analyzer = QueryAnalyzer(mock_catalog={"users": {"id": DataType.INT}, "users_idx": {}})
    result = parse_and_analyze("DROP INDEX users_idx ON users", analyzer)
    expected = IndexDDL(
        op=DDLOp.DROP,
        idx_name="users_idx",
        table=ResolvedTable(
            name="users",
            alias=None,
            table_schema=(ResolvedColumn(name="id", type=DataType.INT, qualifier="users"),),
        ),
        columns=None,
    )
    assert result == expected


def test_transaction_end_to_end() -> None:
    parsed = parse_unibasql("BEGIN TRANSACTION t1; DELETE FROM employees WHERE id = 7; COMMIT;")
    assert isinstance(parsed, Transaction)
    result = QueryAnalyzer().analyze_query(parsed)
    expected = TransactionNode(
        tx_start=TxStartNode(tx_id="t1"),
        tx_end=TxEndNode(tx_result=TxResult.COMMIT),
        statements=[
            Difference(
                left=TableScan(table=employees_table()),
                right=Selection(
                    expr=BinOp(
                        op="=",
                        left=ColumnRef(name="id", qualifier="employees"),
                        right=LiteralExpr(type="int", value=7),
                    ),
                    child=TableScan(table=employees_table()),
                ),
                custom={"source": "delete"},
            )
        ],
    )
    assert result == expected


def test_standalone_start_transaction_end_to_end() -> None:
    parsed = parse_unibasql("BEGIN TRANSACTION t2;")
    assert isinstance(parsed, StartTransaction)
    assert QueryAnalyzer().analyze_query(parsed) == TxStartNode(tx_id="t2")


@pytest.mark.parametrize(("sql", "tx_result"), [("COMMIT;", TxResult.COMMIT), ("ROLLBACK;", TxResult.ROLLBACK)])
def test_standalone_end_transaction_end_to_end(sql: str, tx_result: TxResult) -> None:
    parsed = parse_unibasql(sql)
    assert isinstance(parsed, EndTransaction)
    assert QueryAnalyzer().analyze_query(parsed) == TxEndNode(tx_result=tx_result)


def test_semantic_error_propagates_after_successful_parse() -> None:
    # The statement parses fine but references a column the catalog does not know,
    # so only analysis (not parsing) fails.
    statement = parse_one("SELECT ghost FROM employees")
    assert isinstance(statement, UnresolvedDQL)
    with pytest.raises(ValueError, match=r"Column ghost does not exist"):
        QueryAnalyzer().analyze_query(statement)


def test_syntax_error_raised_before_analysis() -> None:
    with pytest.raises(UnibaSQLSyntaxError, match="Syntax error"):
        parse_and_analyze("SELECT FROM employees")
