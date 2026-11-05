from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict

from .unibasql_algebra import AbstractAlgebraNode
from .unibasql_ddl_nodes import AbstractDDLNode
from .unibasql_unresolved import TxResult

TxNodes: TypeAlias = AbstractAlgebraNode | AbstractDDLNode


# NOTE seperate algebra from ddl but effectively the same
class AbstractTCLNode(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    custom: dict[str, str] | None = None

    @abstractmethod
    def children(self) -> list[AbstractTCLNode]: ...

    @abstractmethod
    def label(self) -> str: ...

    def print_tree(self, indent=0):
        print("  " * indent + self.label())
        for child in self.children():
            child.print_tree(indent + 1)


class TxStartNode(AbstractTCLNode):
    tx_id: str

    def children(self) -> list[AbstractTCLNode]:
        return []

    def label(self) -> str:
        return f"TxStart({self.tx_id})"


class TxEndNode(AbstractTCLNode):
    tx_result: TxResult

    def children(self) -> list[AbstractTCLNode]:
        return []

    def label(self) -> str:
        return f"TxEnd({str(self.tx_result)})"


class TransactionNode(AbstractTCLNode):
    tx_start: TxStartNode
    tx_end: TxEndNode
    statements: list[TxNodes]

    def children(self) -> list[TxNodes]:
        return self.statements

    def label(self) -> str:
        return f"Transaction({str(self.tx_start.tx_id)}) {str(self.tx_end.tx_result)}"
