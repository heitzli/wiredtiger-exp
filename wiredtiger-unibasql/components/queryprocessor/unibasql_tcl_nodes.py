from __future__ import annotations

import io
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeAlias

import avro.io
import avro.schema
from pydantic import BaseModel, ConfigDict

from .unibasql_algebra import AbstractAlgebraNode
from .unibasql_ddl_nodes import AbstractDDLNode
from .unibasql_unresolved import TxResult

TxNodes: TypeAlias = AbstractAlgebraNode | AbstractDDLNode | "AbstractTCLNode"


# NOTE seperate algebra from ddl but effectively the same
class AbstractTCLNode(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    custom: dict[str, str] | None = None
    # TODO in the future each component will have their own protocol folder for the moment ref the "global proto folder"
    _TCL_NODE_SCHEMA: ClassVar[avro.schema.Schema] = avro.schema.parse(
        (Path(__file__).resolve().parents[2] / "protocol" / "tclnode.avsc").read_text(encoding="utf-8")
    )
    _AVRO_TCL_NODE_KINDS: ClassVar[dict[str, type]] = {}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        kind_field = cls.model_fields.get("kind", None)
        if kind_field is not None and isinstance(kind_field.default, str):
            cls._AVRO_TCL_NODE_KINDS[kind_field.default] = cls

    @abstractmethod
    def children(self) -> list[TxNodes]: ...

    @abstractmethod
    def label(self) -> str: ...

    def print_tree(self, indent=0):
        print("  " * indent + self.label())
        for child in self.children():
            child.print_tree(indent + 1)

    def to_avro(self) -> dict[str, Any]:
        """Render this transaction as the plain dict matching the avro TCLNode record."""
        return self._tcl_node_to_avro()

    def to_avro_bytes(self) -> bytes:
        """Binary-encode this transaction using the compiled protocol/tclnode.avsc."""
        buffer = io.BytesIO()
        avro.io.DatumWriter(self._TCL_NODE_SCHEMA).write(self.to_avro(), avro.io.BinaryEncoder(buffer))
        return buffer.getvalue()

    @classmethod
    def from_avro(cls, datum: Mapping[str, Any]) -> AbstractTCLNode:
        """Rebuild a transaction from a dict matching the avro TCLNode record."""
        return cls._tcl_node_from_avro(datum)

    @classmethod
    def from_avro_bytes(cls, raw: bytes) -> AbstractTCLNode:
        """Decode bytes produced by :meth:`to_avro_bytes` back into a transaction."""
        datum = avro.io.DatumReader(cls._TCL_NODE_SCHEMA).read(avro.io.BinaryDecoder(io.BytesIO(raw)))
        return cls.from_avro(datum)

    def _tcl_node_to_avro(self) -> dict[str, Any]:
        """Python TCL node -> plain dict matching the avro TCLNode record."""
        # NOTE: prefill every record field (all have avro defaults) so DatumWriter always finds a value.
        datum: dict[str, Any] = {
            "kind": self.kind,
            "tx_id": None,
            "tx_result": None,
            "tx_start": None,
            "tx_end": None,
            "statements": None,
            "custom": self.custom,
        }
        match self:
            case TxStartNode():
                datum["tx_id"] = self.tx_id
            case TxEndNode():
                datum["tx_result"] = self.tx_result.name
            case TransactionNode():
                datum["tx_start"] = self.tx_start._tcl_node_to_avro()
                datum["tx_end"] = self.tx_end._tcl_node_to_avro()
                datum["statements"] = [self._statement_to_wire(statement) for statement in self.statements]
            case TCLNode():
                if self.tx_id is not None:
                    datum["tx_id"] = self.tx_id
                if self.tx_result is not None:
                    datum["tx_result"] = self.tx_result.name
                if self.tx_start is not None:
                    datum["tx_start"] = self.tx_start._tcl_node_to_avro()
                if self.tx_end is not None:
                    datum["tx_end"] = self.tx_end._tcl_node_to_avro()
                if self.statements is not None:
                    datum["statements"] = [self._statement_to_wire(statement) for statement in self.statements]
            case _:
                raise ValueError(f"Cannot serialize TCL statement of kind {self.kind!r}")
        return datum

    def _statement_to_wire(self, statement: TxNodes) -> dict[str, Any]:
        # NOTE: union of record branches -> the datum is the bare record; DatumWriter picks
        # the first branch whose key set validates (the flavors' keys are disjoint, so the
        # match is unique — verified for kind-only datums too).
        match statement:
            case AbstractTCLNode():
                return statement._tcl_node_to_avro()
            case AbstractDDLNode():
                return statement.to_avro()
            case AbstractAlgebraNode():
                return statement.to_avro()
            case _:
                raise ValueError(f"Cannot serialize transaction statement of type {type(statement).__name__}")

    @classmethod
    def _tcl_node_from_avro(cls, datum: Mapping[str, Any]) -> AbstractTCLNode:
        """Plain dict matching the avro TCLNode record -> Python TCL node."""
        custom = datum.get("custom")
        match datum["kind"]:
            case "TxStartNode":
                return TxStartNode(tx_id=datum["tx_id"], custom=custom)
            case "TxEndNode":
                return TxEndNode(tx_result=TxResult[datum["tx_result"]], custom=custom)
            case "TransactionNode":
                return TransactionNode(
                    tx_start=cls._tcl_node_from_avro(datum["tx_start"]),
                    tx_end=cls._tcl_node_from_avro(datum["tx_end"]),
                    statements=[cls._statement_from_wire(statement) for statement in datum["statements"]],
                    custom=custom,
                )
            case _:
                # NOTE: forward compatibility — a reader that does not know a kind still receives
                # the statement as a generic TCLNode instead of failing the whole decode.
                return TCLNode(
                    kind=datum["kind"],
                    tx_id=datum.get("tx_id"),
                    tx_result=TxResult[datum["tx_result"]] if datum.get("tx_result") is not None else None,
                    tx_start=cls._tcl_node_from_avro(datum["tx_start"]) if datum.get("tx_start") is not None else None,
                    tx_end=cls._tcl_node_from_avro(datum["tx_end"]) if datum.get("tx_end") is not None else None,
                    statements=(
                        [cls._statement_from_wire(statement) for statement in datum["statements"]]
                        if datum.get("statements") is not None
                        else None
                    ),
                    custom=custom,
                )

    @classmethod
    def _statement_branches(cls) -> list:
        """The statement union's record branches from the compiled schema (writer order)."""
        field = cls._TCL_NODE_SCHEMA.fields_dict["statements"]  # union{null, array<union{...}>}
        return field.type.schemas[1].items.schemas

    @classmethod
    def _statement_from_wire(cls, wire: Mapping[str, Any]) -> TxNodes:
        """avro statement union datum -> Python node (dispatch on the validating branch)."""
        for branch in cls._statement_branches():
            if avro.io.validate(branch, wire, raise_on_error=False):
                match branch.fullname:
                    case "unibasql.queryprocessor.algebra.AlgebraNode":
                        return AbstractAlgebraNode.from_avro(wire)
                    case "unibasql.queryprocessor.ddl.DDLNode":
                        return AbstractDDLNode.from_avro(wire)
                    case "unibasql.queryprocessor.tcl.TCLNode":
                        return cls._tcl_node_from_avro(wire)
                    case _:
                        break
        raise ValueError(f"Not a valid transaction statement datum (keys {sorted(wire)})")


class TCLNode(AbstractTCLNode):
    kind: str
    tx_id: str | None = None
    tx_result: TxResult | None = None
    tx_start: AbstractTCLNode | None = None
    tx_end: AbstractTCLNode | None = None
    statements: list[TxNodes] | None = None

    def children(self) -> list[TxNodes]:
        if self.statements:
            return self.statements
        return []

    def label(self) -> str:
        return f"{self.kind}"


class TxStartNode(AbstractTCLNode):
    kind: Literal["TxStartNode"] = "TxStartNode"
    tx_id: str

    def children(self) -> list[TxNodes]:
        return []

    def label(self) -> str:
        return f"TxStart({self.tx_id})"


class TxEndNode(AbstractTCLNode):
    kind: Literal["TxEndNode"] = "TxEndNode"
    tx_result: TxResult

    def children(self) -> list[TxNodes]:
        return []

    def label(self) -> str:
        return f"TxEnd({str(self.tx_result)})"


class TransactionNode(AbstractTCLNode):
    kind: Literal["TransactionNode"] = "TransactionNode"
    tx_start: TxStartNode
    tx_end: TxEndNode
    statements: list[TxNodes]

    def children(self) -> list[TxNodes]:
        return self.statements

    def label(self) -> str:
        return f"Transaction({str(self.tx_start.tx_id)}) {str(self.tx_end.tx_result)}"
