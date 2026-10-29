"""Isolated storage manager tests: handlers driven directly against a real WiredTiger home.

No query processor / client components are involved; the execute handler is
called with an already-converted algebra node (the shape the RPC layer decodes to).
"""

import struct
import uuid

import pytest

from components.errors import WiredTigerError
from components.queryprocessor.avro_utils import to_avro_node
from components.queryprocessor.docalgebra import CreateTable, Delete, DeleteTable, Insert, Update
from components.queryprocessor.expression import AttributeRef, BinOp, LiteralExpr
from components.storagemanager.value_utils import encode_value


def execute_plan(sm, node):
    """Call the SM's execute handler with an algebra node, bypassing the wire."""
    return sm.responder.execute({"request": {"plan": to_avro_node(node), "custom": None}})


def where_equals(attribute: str, value, type_: str = "string") -> BinOp:
    return BinOp(op="==", left=AttributeRef(name=attribute), right=LiteralExpr(value=value, type=type_))


# docids are pinned so the (docid, attribute) scan order is deterministic:
# DOC_FIRST < DOC_MIDDLE < DOC_LAST
DOC_FIRST = bytes(16)
DOC_MIDDLE = bytes([0x80]) + bytes(15)
DOC_LAST = bytes([0xFF] * 16)


def write_row(sm, table: str, docid: bytes, attribute: str, tag: str, value) -> None:
    """Insert one (docid, attribute) -> (tag, payload) row directly, bypassing the insert plan."""
    with sm.connection.open_session() as session:
        with session.open_cursor(f"table:{table}") as cursor:
            cursor.set_key(docid, attribute)
            cursor.set_value(tag, encode_value(tag, value))
            assert cursor.insert() == 0


def insert_document(sm, table: str, docid: bytes, doc: dict) -> None:
    tags = {str: "string", int: "int", float: "float", type(None): "null"}
    for attribute, value in doc.items():
        write_row(sm, table, docid, attribute, tags[type(value)], value)


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
    execute_plan(
        storage_manager, Insert(table="tbl_delete", values=[{"name": LiteralExpr(value="luc", type="string")}])
    )
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
    assert encode_value("null", None) == b""
    assert encode_value("string", "luc") == b"luc"
    assert encode_value("int", 27) == b"27"
    assert encode_value("float", 1.79) == struct.pack(">d", 1.79)

    with pytest.raises(TypeError):
        encode_value("bool", True)


# --- delete with WHERE (regressions for the demo.py "Delete FROM ... WHERE" bug chain) ---


def test_delete_with_where_removes_only_matching_document(storage_manager):
    """A single-attribute document BETWEEN two multi-attribute documents used to
    have its rows leaked into the following document's dict_doc, deleting the
    wrong document (identity claim ran before the boundary flush)."""
    execute_plan(storage_manager, CreateTable(table="tbl_where"))
    insert_document(storage_manager, "tbl_where", DOC_FIRST, {"name": "luc", "age": 30})
    insert_document(storage_manager, "tbl_where", DOC_MIDDLE, {"name": "test"})
    insert_document(storage_manager, "tbl_where", DOC_LAST, {"name": "anna", "age": 42})

    res = execute_plan(storage_manager, Delete(table="tbl_where", expr=where_equals("name", "test")))

    assert list(res["value"]) == [str(uuid.UUID(bytes=DOC_MIDDLE))]
    assert res["value"][str(uuid.UUID(bytes=DOC_MIDDLE))] == {"type": "docid", "value": DOC_MIDDLE}

    remaining = stored_documents(storage_manager, "tbl_where")
    assert set(remaining) == {DOC_FIRST, DOC_LAST}
    assert remaining[DOC_FIRST]["name"] == ("string", b"luc")
    assert remaining[DOC_LAST]["name"] == ("string", b"anna")


def test_delete_with_where_matches_last_document_in_scan_order(storage_manager):
    """The final flush after the scan loop was missing, so the last document in
    scan order was never evaluated and could never be deleted."""
    execute_plan(storage_manager, CreateTable(table="tbl_last"))
    insert_document(storage_manager, "tbl_last", DOC_FIRST, {"name": "luc", "age": 30})
    insert_document(storage_manager, "tbl_last", DOC_LAST, {"name": "test"})

    res = execute_plan(storage_manager, Delete(table="tbl_last", expr=where_equals("name", "test")))

    assert list(res["value"]) == [str(uuid.UUID(bytes=DOC_LAST))]

    remaining = stored_documents(storage_manager, "tbl_last")
    assert set(remaining) == {DOC_FIRST}


def test_delete_with_where_deletes_all_rows_of_matching_document(storage_manager):
    """delete_doc used to early-return because search_near for (docid, '') is a
    near miss (ret != 0) unless a document has a row with an empty attribute."""
    execute_plan(storage_manager, CreateTable(table="tbl_rows"))
    insert_document(storage_manager, "tbl_rows", DOC_FIRST, {"name": "test", "age": 27, "height": 1.79})

    res = execute_plan(storage_manager, Delete(table="tbl_rows", expr=where_equals("name", "test")))

    assert list(res["value"]) == [str(uuid.UUID(bytes=DOC_FIRST))]
    assert stored_documents(storage_manager, "tbl_rows") == {}


def test_delete_without_expression_removes_all_documents(storage_manager):
    execute_plan(storage_manager, CreateTable(table="tbl_all"))
    insert_document(storage_manager, "tbl_all", DOC_FIRST, {"name": "luc"})
    insert_document(storage_manager, "tbl_all", DOC_MIDDLE, {"name": "test"})
    insert_document(storage_manager, "tbl_all", DOC_LAST, {"name": "anna"})

    res = execute_plan(storage_manager, Delete(table="tbl_all", expr=None))

    assert set(res["value"]) == {
        str(uuid.UUID(bytes=DOC_FIRST)),
        str(uuid.UUID(bytes=DOC_MIDDLE)),
        str(uuid.UUID(bytes=DOC_LAST)),
    }
    assert stored_documents(storage_manager, "tbl_all") == {}


def test_delete_with_where_no_match_keeps_everything(storage_manager):
    execute_plan(storage_manager, CreateTable(table="tbl_none"))
    insert_document(storage_manager, "tbl_none", DOC_FIRST, {"name": "luc"})

    res = execute_plan(storage_manager, Delete(table="tbl_none", expr=where_equals("name", "test")))

    assert res == {"value": {}}
    assert set(stored_documents(storage_manager, "tbl_none")) == {DOC_FIRST}


def test_delete_with_where_on_missing_attribute_keeps_documents_without_it(storage_manager):
    """Documents lacking the WHERE attribute must not match (and must not have
    their rows merged into neighbors' documents during the scan)."""
    execute_plan(storage_manager, CreateTable(table="tbl_mixed"))
    insert_document(storage_manager, "tbl_mixed", DOC_FIRST, {"name": "luc", "age": 27})
    insert_document(storage_manager, "tbl_mixed", DOC_MIDDLE, {"name": "test"})
    insert_document(storage_manager, "tbl_mixed", DOC_LAST, {"name": "anna", "age": 27})

    res = execute_plan(storage_manager, Delete(table="tbl_mixed", expr=where_equals("age", 27, "int")))

    assert set(res["value"]) == {str(uuid.UUID(bytes=DOC_FIRST)), str(uuid.UUID(bytes=DOC_LAST))}

    remaining = stored_documents(storage_manager, "tbl_mixed")
    assert set(remaining) == {DOC_MIDDLE}
    assert remaining[DOC_MIDDLE] == {"name": ("string", b"test")}


# --- update with WHERE (regressions for the demo.py "UPDATE ... WHERE" hang / lost writes) ---


def test_update_with_where_updates_matching_document_only(storage_manager):
    """The single-attribute document BETWEEN two multi-attribute documents must
    be evaluated via its own dict_doc (identity claim after the boundary flush)
    and get the kv pair upserted; the other documents stay untouched.

    Reading through a fresh session (as stored_documents does) only sees the
    update because the handler commits its transaction: the missing commit used
    to silently roll everything back while the reply still reported success.
    """
    execute_plan(storage_manager, CreateTable(table="tbl_upd_where"))
    insert_document(storage_manager, "tbl_upd_where", DOC_FIRST, {"name": "luc", "age": 30})
    insert_document(storage_manager, "tbl_upd_where", DOC_MIDDLE, {"name": "test"})
    insert_document(storage_manager, "tbl_upd_where", DOC_LAST, {"name": "anna", "age": 42})

    res = execute_plan(
        storage_manager,
        Update(
            table="tbl_upd_where",
            kv=[("age", LiteralExpr(value=99, type="int"))],
            expr=where_equals("name", "test"),
        ),
    )

    middle_id = str(uuid.UUID(bytes=DOC_MIDDLE))
    assert res["value"] == {f"{middle_id}-age": {"type": "int", "value": b"99"}}

    stored = stored_documents(storage_manager, "tbl_upd_where")
    assert stored[DOC_MIDDLE] == {"name": ("string", b"test"), "age": ("int", b"99")}
    assert stored[DOC_FIRST] == {"name": ("string", b"luc"), "age": ("int", b"30")}
    assert stored[DOC_LAST] == {"name": ("string", b"anna"), "age": ("int", b"42")}


def test_update_matches_last_document_in_scan_order(storage_manager):
    """Without the final flush after the scan loop the LAST document in scan
    order is never evaluated (no boundary follows it) and can never match.
    (Before the restructure this test used to hang instead of fail: a nested
    double loop plus WiredTiger's next()-after-EOF restart quirk.)"""
    execute_plan(storage_manager, CreateTable(table="tbl_upd_last"))
    insert_document(storage_manager, "tbl_upd_last", DOC_FIRST, {"name": "luc"})
    insert_document(storage_manager, "tbl_upd_last", DOC_LAST, {"name": "test"})

    res = execute_plan(
        storage_manager,
        Update(
            table="tbl_upd_last",
            kv=[("name", LiteralExpr(value="molly", type="string"))],
            expr=where_equals("name", "test"),
        ),
    )

    last_id = str(uuid.UUID(bytes=DOC_LAST))
    assert res["value"] == {f"{last_id}-name": {"type": "string", "value": b"molly"}}

    stored = stored_documents(storage_manager, "tbl_upd_last")
    assert stored[DOC_LAST] == {"name": ("string", b"molly")}
    assert stored[DOC_FIRST] == {"name": ("string", b"luc")}


def test_update_matches_first_document_in_scan_order(storage_manager):
    """The old outer loop consumed the first row of the scan — DOC_FIRST's
    alphabetically-first attribute row ('age') — before assembling the first
    dict_doc, so a WHERE on that attribute could never match DOC_FIRST."""
    execute_plan(storage_manager, CreateTable(table="tbl_upd_first"))
    insert_document(storage_manager, "tbl_upd_first", DOC_FIRST, {"name": "test", "age": 30})
    insert_document(storage_manager, "tbl_upd_first", DOC_LAST, {"name": "anna", "age": 42})

    res = execute_plan(
        storage_manager,
        Update(
            table="tbl_upd_first",
            kv=[("name", LiteralExpr(value="molly", type="string"))],
            expr=where_equals("age", 30, "int"),
        ),
    )

    first_id = str(uuid.UUID(bytes=DOC_FIRST))
    assert res["value"] == {f"{first_id}-name": {"type": "string", "value": b"molly"}}

    stored = stored_documents(storage_manager, "tbl_upd_first")
    assert stored[DOC_FIRST] == {"name": ("string", b"molly"), "age": ("int", b"30")}
    assert stored[DOC_LAST] == {"name": ("string", b"anna"), "age": ("int", b"42")}


def test_update_without_where_updates_all_documents(storage_manager):
    """Update(expr=None) must behave like delete: dict_satisfies(None, ...)
    matches every document."""
    execute_plan(storage_manager, CreateTable(table="tbl_upd_all"))
    insert_document(storage_manager, "tbl_upd_all", DOC_FIRST, {"name": "luc", "age": 27})
    insert_document(storage_manager, "tbl_upd_all", DOC_MIDDLE, {"name": "test"})
    insert_document(storage_manager, "tbl_upd_all", DOC_LAST, {"name": "anna", "age": 42})

    res = execute_plan(
        storage_manager,
        Update(table="tbl_upd_all", kv=[("hobby", LiteralExpr(value="chess", type="string"))]),
    )

    assert res["value"] == {
        f"{str(uuid.UUID(bytes=DOC_FIRST))}-hobby": {"type": "string", "value": b"chess"},
        f"{str(uuid.UUID(bytes=DOC_MIDDLE))}-hobby": {"type": "string", "value": b"chess"},
        f"{str(uuid.UUID(bytes=DOC_LAST))}-hobby": {"type": "string", "value": b"chess"},
    }

    stored = stored_documents(storage_manager, "tbl_upd_all")
    assert stored[DOC_FIRST] == {"name": ("string", b"luc"), "age": ("int", b"27"), "hobby": ("string", b"chess")}
    assert stored[DOC_MIDDLE] == {"name": ("string", b"test"), "hobby": ("string", b"chess")}
    assert stored[DOC_LAST] == {"name": ("string", b"anna"), "age": ("int", b"42"), "hobby": ("string", b"chess")}


def test_update_with_where_on_missing_attribute_matches_nothing(storage_manager):
    """Documents lacking the WHERE attribute must not match (and nothing may be
    upserted): AttributeRef raises KeyError, which dict_satisfies evaluates to
    False instead of an error."""
    execute_plan(storage_manager, CreateTable(table="tbl_upd_none"))
    insert_document(storage_manager, "tbl_upd_none", DOC_FIRST, {"name": "luc"})

    res = execute_plan(
        storage_manager,
        Update(
            table="tbl_upd_none",
            kv=[("age", LiteralExpr(value=99, type="int"))],
            expr=where_equals("city", "basel"),
        ),
    )

    assert res == {"value": {}}
    assert stored_documents(storage_manager, "tbl_upd_none") == {DOC_FIRST: {"name": ("string", b"luc")}}


def test_update_upserts_row_with_new_type_tag(storage_manager):
    """_update_doc upserts the whole (docid, attribute) row: overwriting an
    attribute with a different type replaces the complete value, tag included."""
    execute_plan(storage_manager, CreateTable(table="tbl_upd_type"))
    insert_document(storage_manager, "tbl_upd_type", DOC_FIRST, {"name": "luc", "age": 30})

    res = execute_plan(
        storage_manager,
        Update(
            table="tbl_upd_type",
            kv=[("age", LiteralExpr(value="ninety-nine", type="string"))],
            expr=where_equals("name", "luc"),
        ),
    )

    first_id = str(uuid.UUID(bytes=DOC_FIRST))
    assert res["value"] == {f"{first_id}-age": {"type": "string", "value": b"ninety-nine"}}
    assert stored_documents(storage_manager, "tbl_upd_type")[DOC_FIRST] == {
        "name": ("string", b"luc"),
        "age": ("string", b"ninety-nine"),
    }
