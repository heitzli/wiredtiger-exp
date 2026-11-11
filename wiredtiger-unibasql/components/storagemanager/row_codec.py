# NOTE: Relational cell codec for UnibaSQL tables.

from __future__ import annotations

import struct

from ..queryprocessor.unibasql_unresolved import DataType


def unwire_cell(dtype: DataType, raw: object) -> object:
    """Container wire cell -> Python value (inverse of wire_cell, header-tag typed); None passes (union erased BOOL/INT)."""
    if raw is None:
        return None
    match dtype:
        case DataType.BOOL:
            return bool(int(raw))
        case DataType.INT:
            return int(raw)
        case DataType.STRING:
            return str(raw)
        case DataType.DOUBLE:
            return float(raw)
    raise TypeError(f"Unsupported type: {dtype}")


def parse_header(header: str) -> tuple[list[str], list[DataType]]:
    """Stream header (CSV 'name:TAG' tuple order) -> parallel (names, dtypes); companion to type_tag()."""
    names: list[str] = []
    dtypes: list[DataType] = []
    for entry in filter(None, header.split(",")):
        name, sep, tag = entry.rpartition(":")
        if not (sep and name):
            raise ValueError(f"Malformed stream header entry {entry!r}")
        try:
            tag_type = DataType[tag.upper()]
        except KeyError:
            raise ValueError(f"Unknown type tag {tag!r} in stream header") from None
        names.append(name)
        dtypes.append(tag_type)
    return names, dtypes


def type_tag(dtype: DataType) -> str:
    """Uppercase wire tag — DataType member names ARE the tags (enum VALUES are lowercase)."""
    return dtype.name


def encode_nullmap(null_flags: list[bool] | tuple[bool, ...]) -> bytes:
    """bit i of the bitmap <=> value column i is NULL; little-endian per byte."""
    bitmap = bytearray((len(null_flags) + 7) // 8)
    for i, null in enumerate(null_flags):
        if null:
            bitmap[i // 8] |= 1 << (i % 8)
    return bytes(bitmap)


def decode_nullmap(data: bytes, num_value_cols: int) -> list[bool]:
    flags = []
    for i in range(num_value_cols):
        flags.append(bool(data[i // 8] >> (i % 8) & 1) if i // 8 < len(data) else False)
    return flags


def encode_cell(dtype: DataType, value: object) -> object:
    match dtype:
        case DataType.BOOL:
            return int(bool(value))
        case DataType.INT:
            return int(value)
        case DataType.STRING:
            return str(value)
        case DataType.DOUBLE:
            return struct.pack(">d", float(value))  # NOTE big-endian, matching decode
        case DataType.NULL:
            raise TypeError("Encode a concrete cell; NULL columns take blank_cell()")
    raise TypeError(f"Unsupported type: {dtype}")


def blank_cell(dtype: DataType) -> str | bytes | int:
    # NOTE null bitmap is authorative, these are just dummy values to set
    match dtype:
        case DataType.STRING:
            return ""
        case DataType.DOUBLE:
            return struct.pack(">d", 0.0)
        case DataType.BOOL | DataType.INT:
            return 0
    raise TypeError(f"Unsupported type: {dtype}")


def decode_cell(dtype: DataType, raw: str | bytes | int) -> object:
    match dtype:
        case DataType.BOOL:
            return bool(int(raw))
        case DataType.INT:
            return int(raw)
        case DataType.STRING:
            return raw if isinstance(raw, str) else bytes(raw).decode("utf-8")
        case DataType.DOUBLE:
            return struct.unpack(">d", bytes(raw))[0]  # BIG-ENDIAN IEEE-754 double
        case DataType.NULL:
            return None
    raise TypeError(f"Unsupported type: {dtype}")


def wire_cell(dtype: DataType, value: object) -> object:
    if value is None:
        return None
    match dtype:
        case DataType.BOOL:
            return int(bool(value))
        case DataType.INT:
            return int(value)
        case DataType.STRING:
            return str(value)
        case DataType.DOUBLE:
            return float(value)
    raise TypeError(f"Unsupported type: {dtype}")
