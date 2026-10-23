from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class AbstractAlgebraNode(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def children(self) -> list[AbstractAlgebraNode]: ...

    @abstractmethod
    def label(self) -> str: ...

    def print_tree(self, indent=0):
        print("  " * indent + self.label())
        for child in self.children():
            child.print_tree(indent + 1)

    def to_avro_node(self) -> dict[str, Any]:
        if isinstance(self, PUTNode):
            return {
                "kind": "PUTNode",
                "key": self.key,
                "value": self.value,
            }

        if isinstance(self, GETNode):
            return {
                "kind": "GETNode",
                "key": self.key,
            }

        if isinstance(self, DELETENode):
            return {
                "kind": "DELETENode",
                "key": self.key,
            }

        raise TypeError(f"Unsupported algebra node: {type(self).__name__}")


class PUTNode(AbstractAlgebraNode):
    kind: Literal["PUTNode"] = "PUTNode"
    key: str
    value: Any  # NOTE in KV value is opaque byte sequence

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"PUT key={self.key} : value={self.value}"


class GETNode(AbstractAlgebraNode):
    kind: Literal["GETNode"] = "GETNode"
    key: str

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"GET key={self.key}"


class DELETENode(AbstractAlgebraNode):
    kind: Literal["DELETENode"] = "DELETENode"
    key: str

    def children(self) -> list[AbstractAlgebraNode]:
        return []

    def label(self) -> str:
        return f"DELETE key={self.key}"
