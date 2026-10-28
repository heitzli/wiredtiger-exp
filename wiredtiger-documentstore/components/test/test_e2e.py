"""End-to-end tests: client -> broker -> queryprocessor -> broker -> storagemanager -> WiredTiger.

Everything runs in one process on temp sockets with a temp WiredTiger home, but
through the real wire protocol (avro binary envelopes, broker routing, error unions).
"""

import struct

import pytest

from components.errors import DocQLSyntaxError, QueryProcessorError, RemoteError, WiredTigerError


def test_create_and_delete_table_roundtrip(stack):
    assert stack.client._query(query="CREATE TABLE e2e_round;") == {}
    assert stack.client._query(query="DELETE TABLE e2e_round;") == {}


def test_insert_persists_documents_and_returns_docids(stack):
    stack.client._query(query="CREATE TABLE e2e_ins;")

    docids = stack.client._query(
        query="INSERT INTO e2e_ins VALUES ({'name': 'luc', 'age': 27, 'height': 1.79, 'hobby': NULL})"
    )

    assert len(docids) == 1
    (entry,) = docids.values()
    assert entry["type"] == "docid"
    assert isinstance(entry["value"], bytes) and len(entry["value"]) == 16

    rows: dict[str, tuple[str, bytes]] = {}
    with stack.storagemanager.connection.open_session() as session:
        with session.open_cursor("table:e2e_ins") as cursor:
            while cursor.next() == 0:
                _, attribute = tuple(cursor.get_key())
                rows[attribute] = tuple(cursor.get_value())

    # NOTE: int literals arrive at the SM as float (avro writes every numeric
    # union member as double, AVRO-973); from_avro_expr normalizes them back to
    # int via the type tag, so they persist as b"27" and not b"27.0".
    assert rows == {
        "name": ("string", b"luc"),
        "age": ("int", b"27"),
        "height": ("float", struct.pack(">d", 1.79)),
        "hobby": ("null", b""),
    }


def test_delete_table_after_insert_no_resource_busy(stack):
    """Regression: DELETE TABLE used to fail with 'Resource busy' right after an insert."""
    stack.client._query(query="CREATE TABLE e2e_busy;")
    stack.client._query(query="INSERT INTO e2e_busy VALUES ({'a': 1})")

    assert stack.client._query(query="DELETE TABLE e2e_busy;") == {}

    with stack.storagemanager.connection.open_session() as session:
        with session.open_cursor("metadata:") as cursor:
            keys = []
            while cursor.next() == 0:
                keys.append(cursor.get_key())
    assert "table:e2e_busy" not in keys


def test_delete_missing_table_raises_typed_error_at_client(stack):
    with pytest.raises(WiredTigerError, match="Table ghost does not exist."):
        stack.client._query(query="DELETE TABLE ghost;")


def test_syntax_error_travels_typed_to_client(stack):
    with pytest.raises(DocQLSyntaxError, match="Syntax error"):
        stack.client._query(query="this is $not docql")


def test_insert_into_missing_table_raises_remote_error_at_client(stack):
    with pytest.raises(RemoteError):
        stack.client._query(query="INSERT INTO ghost VALUES ({'a': 1})")


def test_select_is_parsed_but_not_executable_yet(stack):
    """Documents the current gap: the storage manager has no projection handler,
    so the execute reply cannot be encoded and evaluate fails wrapped."""
    with pytest.raises(QueryProcessorError):
        stack.client._query(query="SELECT a FROM ghost;")
