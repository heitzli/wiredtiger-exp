"""Scalar value <-> wire-bytes encoder/decoder (big-endian AVRO-style doubles): the per-cell
variant of row_codec, kept for the legacy dict-row executor (execution_node)."""
import struct

from ..queryprocessor.unibasql_unresolved import DataType


def encode_value(val_type: DataType, value: int | float | str | None) -> bytes:
    """Typed scalar -> wire bytes (NULL encodes to empty)."""
    if val_type == DataType.NULL:
        return b""
    if val_type == DataType.STRING:
        return str(value).encode("utf-8")
    if val_type == DataType.INT:
        return str(value).encode("ascii")
    if val_type == DataType.DOUBLE:
        return struct.pack(">d", value)  # NOTE: >d is big-endian encoding!
    raise TypeError(f"Unsupported type: {type(value).__name__}")


def decode_value(val_type: DataType, data: bytes) -> int | float | str | None:
    """Wire bytes -> typed scalar (inverse of encode_value)."""
    if val_type == DataType.NULL:
        return None
    if val_type == DataType.STRING:
        return data.decode("utf-8")
    if val_type == DataType.INT:
        return int(data.decode("ascii"))
    if val_type == DataType.DOUBLE:
        return struct.unpack(">d", data)[0]  # Big-endian double
    raise TypeError(f"Unsupported type: {val_type}")
