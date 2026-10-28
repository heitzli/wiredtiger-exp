"""Isolated storage manager tests: handlers driven directly against a real WiredTiger home.

No query processor / client components are involved; the execute handler is
called with an already-converted algebra node (the shape the RPC layer decodes to).
"""

import struct

import pytest

from components.errors import WiredTigerError
from components.queryprocessor.avro_utils import to_avro_node
from components.queryprocessor.docalgebra import CreateTable, DeleteTable, Insert
from components.queryprocessor.expression import LiteralExpr
from components.storagemanager.storagemanager_component import StorageManagerResponder


def execute_plan(sm, node):
    """Call the SM's execute handler with an algebra node, bypassing the wire."""
    return sm.responder.execute({"request": {"plan": to_avro_node(node), "custom": None}})


def registered_tables(sm) -> set[str]:
    """Table names known to WiredTiger's metadata."""
    names = set()
    with sm.connection.open_session() as session:
        with session.open_cursor("metadata:") as cursor:
            while cursor.next() == 0:
                key = cursor.get_key()
                if key.startswith("table:"):
                    names.add(key.removeprefix("table:"))
    return names


def stored_documents(sm, table) -> dict[bytes, dict[str, tuple[str, bytes]]]:
    """All rows of a table: docid -> {attribute: (type_tag, payload)}."""
    documents: dict[bytes, dict[str, tuple[str, bytes]]] = {}
    with sm.connection.open_session() as session:
        with session.open_cursor(f"table:{table}") as cursor:
            while cursor.next() == 0:
                docid, attribute = cursor.get_key()
                type_tag, payload = cursor.get_value()
                documents.setdefault(docid, {})[attribute] = (type_tag, payload)
    return documents


def test_create_table_registers_table_in_metadata(storage_manager):
    assert registered_tables(storage_manager) == set()

    res = execute_plan(storage_manager, CreateTable(table="tbl_create"))

    assert res == {"value": {}}
    assert registered_tables(storage_manager) == {"tbl_create"}


def test_create_table_on_existing_table_is_accepted(storage_manager):
    execute_plan(storage_manager, CreateTable(table="tbl_dup"))

    res = execute_plan(storage_manager, CreateTable(table="tbl_dup"))

    assert res == {"value": {}}
    assert "tbl_dup" in registered_tables(storage_manager)


def test_insert_persists_one_row_per_attribute_with_type_tag(storage_manager):
    execute_plan(storage_manager, CreateTable(table="tbl_insert"))
    doc = {
        "name": LiteralExpr(value="luc", type="string"),
        "age": LiteralExpr(value=27, type="int"),
        "height": LiteralExpr(value=1.79, type="float"),
        "hobby": LiteralExpr(value=None, type="null"),
    }

    res = execute_plan(storage_manager, Insert(table="tbl_insert", values=[doc]))

    assert len(res["value"]) == 1
    (docid,) = res["value"]
    assert res["value"][docid] == {"type": "docid", "value": res["value"][docid]["value"]}

    stored = stored_documents(storage_manager, "tbl_insert")
    assert len(stored) == 1
    (row,) = stored.values()
    assert row == {
        "name": ("string", b"luc"),
        "age": ("int", b"27"),
        "height": ("float", struct.pack(">d", 1.79)),
        "hobby": ("null", b""),
    }


def test_insert_generates_unique_document_ids_per_document(storage_manager):
    execute_plan(storage_manager, CreateTable(table="tbl_multi"))
    docs = [
        {"n": LiteralExpr(value=1, type="int")},
        {"n": LiteralExpr(value=2, type="int")},
    ]

    res = execute_plan(storage_manager, Insert(table="tbl_multi", values=docs))

    ids = list(res["value"])
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert set(stored_documents(storage_manager, "tbl_multi")) == {res["value"][i]["value"] for i in ids}


def test_insert_skips_empty_documents(storage_manager):
    execute_plan(storage_manager, CreateTable(table="tbl_empty"))

    res = execute_plan(storage_manager, Insert(table="tbl_empty", values=[{}, {}]))

    assert res["value"] == {}
    assert stored_documents(storage_manager, "tbl_empty") == {}


def test_delete_table_after_insert_removes_table(storage_manager):
    """Regression: dropping a freshly-inserted table used to fail with EBUSY
    (WiredTiger requires a checkpoint before dropping a table with data)."""
    execute_plan(storage_manager, CreateTable(table="tbl_delete"))
    execute_plan(storage_manager, Insert(table="tbl_delete", values=[{"name": LiteralExpr(value="luc", type="string")}]))
    assert registered_tables(storage_manager) == {"tbl_delete"}

    res = execute_plan(storage_manager, DeleteTable(table="tbl_delete"))

    assert res == {"value": {}}
    assert registered_tables(storage_manager) == set()


def test_delete_missing_table_raises_typed_error(storage_manager):
    with pytest.raises(WiredTigerError) as excinfo:
        execute_plan(storage_manager, DeleteTable(table="ghost"))

    assert "Table ghost does not exist." in str(excinfo.value)


def test_delete_missing_table_rolls_back_transaction(storage_manager):
    execute_plan(storage_manager, CreateTable(table="tbl_alive"))

    with pytest.raises(WiredTigerError):
        execute_plan(storage_manager, DeleteTable(table="ghost"))

    # the failed drop must not have disturbed other tables
    assert registered_tables(storage_manager) == {"tbl_alive"}


def test_responder_invokes_execute_and_creates_table(storage_manager):
    message = storage_manager.protocol.messages["execute"]

    res = storage_manager.responder.invoke(
        message,
        {"request": {"plan": to_avro_node(CreateTable(table="tbl_invoke")), "custom": None}},
    )

    assert res == {"value": {}}
    assert "tbl_invoke" in registered_tables(storage_manager)


def test_value_encodings():
    assert StorageManagerResponder.encode_value("null", None) == b""
    assert StorageManagerResponder.encode_value("string", "luc") == b"luc"
    assert StorageManagerResponder.encode_value("int", 27) == b"27"
    assert StorageManagerResponder.encode_value("float", 1.79) == struct.pack(">d", 1.79)

    with pytest.raises(TypeError):
        StorageManagerResponder.encode_value("bool", True)
