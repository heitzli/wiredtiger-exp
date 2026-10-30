import uuid
from abc import ABC, abstractmethod

import wiredtiger
from wiredtiger import wiredtiger_strerror

from ..errors import WiredTigerError
from ..queryprocessor import AbstractAlgebraNode, Projection, Selection, TableScan, dict_satisfies
from .value_utils import decode_value


class ExecNode(ABC):
    @abstractmethod
    def execute(self, wt_conn: wiredtiger.Connection): ...


class TableScanExecNode(ExecNode):
    def __init__(self, algebra_node: TableScan) -> None:
        self.table = algebra_node.table
        self.kind = algebra_node.kind

    def execute(self, wt_conn: wiredtiger.Connection):  # type: ignore
        with wt_conn.open_session() as session:
            with session.open_cursor(f"table:{self.table}") as cursor:
                if start_tx_err := session.begin_transaction():
                    raise WiredTigerError(
                        message=wiredtiger_strerror(start_tx_err),  # type: ignore
                        source=self.kind,
                    )

                current_doc_id: bytes | None = None
                dict_doc = {}

                try:
                    while cursor.next() == 0:
                        key, attribute = cursor.get_key()

                        if current_doc_id is not None and current_doc_id != key:
                            yield dict_doc
                            dict_doc = {}
                        current_doc_id = key
                        if "_id" not in dict_doc:
                            dict_doc["_id"] = str(uuid.UUID(bytes=key))

                        val_type, value_bytes = cursor.get_value()
                        dict_doc[attribute] = decode_value(val_type=val_type, data=value_bytes)

                    if dict_doc:
                        yield dict_doc

                    session.commit_transaction()

                except Exception:
                    session.rollback_transaction()
                    raise


class SelectionExecNode(ExecNode):
    def __init__(self, algebra_node: Selection, table_scan_exec: TableScanExecNode) -> None:
        self.expr = algebra_node.expr
        self.child = table_scan_exec

    def execute(self, wt_conn: wiredtiger.Connection):  # type: ignore
        for doc in self.child.execute(wt_conn=wt_conn):
            if dict_satisfies(expr=self.expr, values=doc):
                yield doc


class ProjectionExecNode(ExecNode):
    def __init__(self, algebra_node: Projection, child: SelectionExecNode | TableScanExecNode) -> None:
        self.projection: set[str] = set(k.name for k in algebra_node.attributes)
        # always return the id
        self.projection.add("_id")
        self.child = child

    def execute(self, wt_conn: wiredtiger.Connection):  # type: ignore
        for doc in self.child.execute(wt_conn=wt_conn):
            filtered_dict = {k: v for k, v in doc.items() if k in self.projection}
            if filtered_dict:
                yield filtered_dict


class ExecBuilder:
    def build(self, algebra_node: AbstractAlgebraNode) -> ExecNode:  # type: ignore
        if isinstance(algebra_node, TableScan):
            return TableScanExecNode(algebra_node=algebra_node)

        if isinstance(algebra_node, Selection):
            return SelectionExecNode(algebra_node=algebra_node, table_scan_exec=self.build(algebra_node.child))  # type: ignore

        if isinstance(algebra_node, Projection):
            return ProjectionExecNode(algebra_node=algebra_node, child=self.build(algebra_node=algebra_node.child))  # type: ignore
