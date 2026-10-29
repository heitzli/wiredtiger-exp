import uuid
from pathlib import Path

from avro.ipc import Responder
from avro.protocol import Protocol

from ..component import Component
from ..storagemanager.value_utils import decode_value


class ClientResponder(Responder):
    def __init__(self, local_protocol):
        super().__init__(local_protocol)

    def invoke(self, local_message, request):  # type: ignore
        raise RuntimeError(f"Client serves no RPC; got {local_message.name!r}")


class Client(Component):
    def __init__(
        self,
        name: str,
        broker_protocol_path: Path,
        protocol_path: Path,
        broker_socket: str,
        component_socket: str,
    ) -> None:
        super().__init__(name, broker_protocol_path, protocol_path, broker_socket, component_socket)

    def _create_responder(self, protocol: Protocol) -> Responder:
        return ClientResponder(local_protocol=protocol)

    def _query(self, query: str) -> dict:
        res = self.call_remote(destination="queryprocessor", rpc_name="evaluate", params={"request": {"query": query}})
        documents = res.get("documents")
        if documents is not None:
            return documents
        return res["value"]

    @staticmethod
    def _decode_wtvalue(entry: dict) -> int | float | str | None:
        if entry["type"] == "docid":
            return str(uuid.UUID(bytes=entry["value"]))
        return decode_value(val_type=entry["type"], data=entry["value"])

    @staticmethod
    def _format_literal(value: int | float | str | None) -> str:
        """Format a python value as a DocQL literal: NULL, numbers, or a
        single-quoted string with ''-escaped inner single quotes."""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            raise TypeError("bool is not a supported DocQL literal")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            text = repr(value)
            if "." not in text:
                text = f"{value:f}"
            return text
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
        raise TypeError(f"unsupported literal type: {type(value).__name__}")

    def create_table(self, table: str) -> dict:
        return self._query(f"CREATE TABLE {table};")

    def delete_table(self, table: str) -> dict:
        return self._query(f"DELETE TABLE {table};")

    def insert(self, table: str, values: list[dict]) -> dict:
        rows = ", ".join(
            "{"
            + ", ".join(
                f"{self._format_literal(attribute)}: {self._format_literal(value)}"
                for attribute, value in row.items()
            )
            + "}"
            for row in values
        )
        reply = self._query(f"INSERT INTO {table} VALUES ({rows});")
        return {docid: self._decode_wtvalue(entry) for docid, entry in reply.items()}

    def delete(self, table: str, filter: str | None) -> dict:
        where = f" WHERE {filter}" if filter is not None else ""
        reply = self._query(f"DELETE FROM {table}{where};")
        return {docid: self._decode_wtvalue(entry) for docid, entry in reply.items()}

    def update(self, table: str, kv_pairs: tuple[str, int | float | str | None]) -> dict:
        attribute, value = kv_pairs
        reply = self._query(f"UPDATE {table} SET {attribute} = {self._format_literal(value)};")
        return {key: self._decode_wtvalue(entry) for key, entry in reply.items()}

    def select(self, table: str, projection: list[str], filter: str | None) -> dict:
        where = f" WHERE {filter}" if filter is not None else ""
        documents = self._query(f"SELECT {', '.join(projection)} FROM {table}{where};")
        return {
            docid: {attribute: self._decode_wtvalue(entry) for attribute, entry in attrs.items()}
            for docid, attrs in documents.items()
        }
