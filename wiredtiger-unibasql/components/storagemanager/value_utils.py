import struct


def encode_value(val_type: str, value: int | float | str | None) -> bytes:
    if val_type == "null":
        return b""
    if val_type == "string":
        return str(value).encode("utf-8")
    if val_type == "int":
        return str(value).encode("ascii")
    if val_type == "float":
        return struct.pack(">d", value)  # NOTE: >d is big-endian encoding!
    raise TypeError(f"Unsupported type: {type(value).__name__}")


def decode_value(val_type: str, data: bytes) -> int | float | str | None:
    if val_type == "null":
        return None
    if val_type == "string":
        return data.decode("utf-8")
    if val_type == "int":
        return int(data.decode("ascii"))
    if val_type == "float":
        return struct.unpack(">d", data)[0]  # Big-endian double
    raise TypeError(f"Unsupported type: {val_type}")
