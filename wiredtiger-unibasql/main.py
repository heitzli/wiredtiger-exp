"""DML smoke test: runs DDL handlers directly against the storage manager."""

from pathlib import Path

from components.queryprocessor.unibasql_algebra import ResolvedColumn, ResolvedTable
from components.queryprocessor.unibasql_ddl_nodes import IndexDDL, TableDDL
from components.queryprocessor.unibasql_unresolved import ColumnConstraint, DataType, DDLOp
from components.storagemanager import StorageManagerComponent

table = ResolvedTable(
    name="test2",
    table_schema=(
        ResolvedColumn(name="id", type=DataType.INT, constraint=ColumnConstraint.PRIMARY_KEY),
        ResolvedColumn(name="name", type=DataType.STRING),
        ResolvedColumn(name="salary", type=DataType.DOUBLE),
        ResolvedColumn(name="active", type=DataType.BOOL),
    ),
)

test = TableDDL(
    op=DDLOp.CREATE,
    table=table,
)

test_idx = IndexDDL(
    op=DDLOp.CREATE,
    idx_name="test_idx",
    table=table,
    columns=(ResolvedColumn(name="id", type=DataType.INT), ResolvedColumn(name="name", type=DataType.DOUBLE)),
)

if __name__ == "__main__":
    sm = StorageManagerComponent(
        name="storagemanager",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/storagemanager.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/sm.sock",
    )

    sm.run()

    """
    res = sm.responder._handle_create_table(test)
    print(res)
    res = sm.responder._handle_create_index(test_idx)
    print(res)
    res = sm.responder._handle_drop_index(test_idx)
    print(res)
    res = sm.responder._handle_drop_table(test)
    print(res)
    """
