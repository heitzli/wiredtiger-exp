"""End-to-end tests: client -> broker -> queryprocessor -> broker -> storagemanager -> WiredTiger.

Everything runs in one process on temp sockets with a temp WiredTiger home, but
through the real wire protocol (avro binary envelopes, broker routing, error unions).
"""

import struct
import uuid

import pytest

from components.errors import DocQLSyntaxError, RemoteError, WiredTigerError


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


def test_select_with_where_returns_only_matching_document(stack):
    """Full stack: client -> broker -> queryprocessor (parse to
    Projection(child=Selection(child=TableScan))) -> storagemanager.

    The client returns the documents map: docid -> {attribute: {"type", "value"}}.
    """
    assert stack.client._query(query="CREATE TABLE test;") == {}
    stack.client._query(
        query="INSERT INTO test VALUES ({'name':'luc', 'age':27}, {'name':'test'})"
    )

    documents = stack.client._query(query="SELECT name FROM test WHERE name = 'luc';")

    # only luc's document matches; the projected attribute travels plus the
    # always-present synthesized _id
    assert len(documents) == 1
    ((docid, attrs),) = documents.items()
    assert attrs == {
        "name": {"type": "string", "value": b"luc"},
        "_id": {"type": "string", "value": docid.encode()},
    }


def test_select_without_where_returns_all_documents_with_projected_attributes(stack):
    """SELECT without a WHERE clause (no Selection node) returns every stored
    document, and — since there is no SELECT * — only the explicitly projected
    attributes per document."""
    assert stack.client._query(query="CREATE TABLE test;") == {}
    stack.client._query(
        query="INSERT INTO test VALUES "
        "({'name':'luc', 'age':27, 'height':1.79, 'hobby': NULL}, {'name':'test'})"
    )

    documents = stack.client._query(query="SELECT name, age FROM test;")

    assert len(documents) == 2
    for docid, attrs in documents.items():
        # _id always travels; besides it only the projected attributes may
        # appear and missing ones are not backfilled (no SELECT *)
        assert set(attrs) <= {"name", "age", "_id"}
        assert attrs["_id"] == {"type": "string", "value": docid.encode()}

    # the 'test' document has no age; it is not backfilled (no SELECT *)
    test_doc = next(doc for doc in documents.values() if doc["name"]["value"] == b"test")
    assert "age" not in test_doc
    luc_doc = next(doc for doc in documents.values() if doc["name"]["value"] == b"luc")
    assert luc_doc["age"] == {"type": "int", "value": b"27"}


def test_select_returns_type_faithful_values(stack):
    """Every projected attribute travels as {"type", "value"} with the tag the
    client needs to reconvert: int -> ascii digits, float -> big-endian double,
    string -> utf-8, null -> empty bytes."""
    assert stack.client._query(query="CREATE TABLE test;") == {}
    stack.client._query(
        query="INSERT INTO test VALUES "
        "({'name':'luc', 'age':27, 'height':1.79, 'hobby': NULL}, {'name':'test'})"
    )

    documents = stack.client._query(query="SELECT name, age, height, hobby FROM test WHERE name = 'luc';")

    assert len(documents) == 1
    ((docid, attrs),) = documents.items()
    assert attrs == {
        "name": {"type": "string", "value": b"luc"},
        "age": {"type": "int", "value": b"27"},
        "height": {"type": "float", "value": struct.pack(">d", 1.79)},
        "hobby": {"type": "null", "value": b""},
        "_id": {"type": "string", "value": docid.encode()},
    }


def test_select_on_missing_table_raises_typed_error_at_client(stack):
    """Was pinned as 'parsed but not executable'; now the plan reaches the
    storage manager, whose missing-table failure travels typed to the client."""
    with pytest.raises(RemoteError, match="No such file or directory"):
        stack.client._query(query="SELECT a FROM ghost;")


def test_delete_from_with_where_matches_demo_scenario(stack):
    """Regression for demo.py query 3: DELETE FROM test WHERE name='test'.

    Used to fail through a chain of bugs: '=' missing from the evaluator's ops
    table, missing final flush / wrong identity-claim ordering in the delete
    scan, delete_doc near-miss early return, and an unwired reply shape.
    """
    assert stack.client._query(query="CREATE TABLE test;") == {}
    docids = stack.client._query(
        query="INSERT INTO test VALUES ({'name':'luc', 'age':27, 'height':1.79, 'hobby': NULL}, {}, {'name':'test'})"
    )
    assert len(docids) == 2

    deleted = stack.client._query(query="Delete FROM test WHERE name='test';")

    assert len(deleted) == 1
    ((docid, entry),) = deleted.items()
    assert docid in docids
    assert entry == {"type": "docid", "value": docids[docid]["value"]}

    # only the 'luc' document survives, with all four attributes intact
    rows: dict[str, tuple[str, bytes]] = {}
    with stack.storagemanager.connection.open_session() as session:
        with session.open_cursor("table:test") as cursor:
            while cursor.next() == 0:
                _, attribute = tuple(cursor.get_key())
                rows[attribute] = tuple(cursor.get_value())
    assert rows == {
        "age": ("int", b"27"),
        "height": ("float", struct.pack(">d", 1.79)),
        "hobby": ("null", b""),
        "name": ("string", b"luc"),
    }


def test_delete_from_without_where_removes_all_documents(stack):
    """DELETE FROM without a WHERE clause parses to Delete(expr=None):
    dict_satisfies(None, ...) evaluates True for every document, so the whole
    table is emptied and every docid is reported as deleted."""
    assert stack.client._query(query="CREATE TABLE test;") == {}
    docids = stack.client._query(
        query="INSERT INTO test VALUES "
        "({'name':'luc', 'age':27, 'height':1.79, 'hobby': NULL}, {}, {'name':'test'}, {'name':'anna'})"
    )
    assert len(docids) == 3

    deleted = stack.client._query(query="DELETE FROM test;")

    assert sorted(deleted) == sorted(docids)
    for docid, entry in deleted.items():
        assert docid in docids
        assert entry == {"type": "docid", "value": docids[docid]["value"]}

    # the table must be empty now
    with stack.storagemanager.connection.open_session() as session:
        with session.open_cursor("table:test") as cursor:
            assert cursor.next() != 0


def test_delete_from_with_where_on_missing_attribute_keeps_document(stack):
    """DELETE FROM test WHERE age = 27 must not match documents that do not
    have an age attribute at all: AttributeRef raises KeyError, which
    dict_satisfies evaluates to False instead of an error."""
    assert stack.client._query(query="CREATE TABLE test;") == {}
    docids = stack.client._query(query="INSERT INTO test VALUES ({'name':'luc', 'age':27}, {'name':'test'})")
    assert len(docids) == 2

    deleted = stack.client._query(query="DELETE FROM test WHERE age = 27;")

    # only luc's document (age = 27) matches; 'test' has no age and survives
    assert len(deleted) == 1
    (deleted_docid,) = deleted.keys()
    assert deleted_docid in docids

    with stack.storagemanager.connection.open_session() as session:
        with session.open_cursor("table:test") as cursor:
            rows: dict[str, tuple[str, bytes]] = {}
            while cursor.next() == 0:
                _, attribute = tuple(cursor.get_key())
                rows[attribute] = tuple(cursor.get_value())
    assert rows == {"name": ("string", b"test")}


def test_update_with_where_demo_scenario(stack):
    """Regression for demo.py query 3: UPDATE test SET age=99, hobby='archery'
    WHERE name='test'.

    Used to hang forever: the update handler had an accidental nested double
    scan loop and WiredTiger's next()-after-EOF restart quirk (calling next()
    on an exhausted cursor restarts the scan returning 0) kept it spinning.
    After the restructure it even reported success while nothing was persisted,
    because the transaction was never committed.
    """
    assert stack.client._query(query="CREATE TABLE test;") == {}
    docids = stack.client._query(
        query="INSERT INTO test VALUES ({'name':'luc', 'age':27, 'height':1.79, 'hobby': NULL}, {}, {'name':'test'})"
    )
    assert len(docids) == 2

    updated = stack.client._query(query="UPDATE test SET age=99, hobby='archery' WHERE name='test';")

    # both upserted attributes are reported as "<docid>-<attribute>" keys of one document
    assert len(updated) == 2
    prefixes = {key.rsplit("-", 1)[0] for key in updated}
    attribute_of = {key.rsplit("-", 1)[1]: entry for key, entry in updated.items()}
    inserted_ids = {str(uuid.UUID(bytes=entry["value"])) for entry in docids.values()}

    assert len(prefixes) == 1
    assert prefixes <= inserted_ids
    assert set(attribute_of) == {"age", "hobby"}
    assert attribute_of["age"] == {"type": "int", "value": b"99"}
    assert attribute_of["hobby"] == {"type": "string", "value": b"archery"}

    # the update must be committed: the 'test' document is extended by age and
    # hobby while luc's document is untouched
    docs: dict[bytes, dict[str, tuple[str, bytes]]] = {}
    with stack.storagemanager.connection.open_session() as session:
        with session.open_cursor("table:test") as cursor:
            while cursor.next() == 0:
                docid, attribute = tuple(cursor.get_key())
                docs.setdefault(docid, {})[attribute] = tuple(cursor.get_value())

    assert len(docs) == 2
    test_doc = next(doc for doc in docs.values() if doc["name"] == ("string", b"test"))
    luc_doc = next(doc for doc in docs.values() if doc["name"] == ("string", b"luc"))
    assert test_doc == {
        "name": ("string", b"test"),
        "age": ("int", b"99"),
        "hobby": ("string", b"archery"),
    }
    assert luc_doc == {
        "name": ("string", b"luc"),
        "age": ("int", b"27"),
        "height": ("float", struct.pack(">d", 1.79)),
        "hobby": ("null", b""),
    }
