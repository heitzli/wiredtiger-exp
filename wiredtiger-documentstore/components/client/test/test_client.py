"""Tests for the client component: its responder guard and the _query RPC facade.

The _query tests run against the full in-process stack
(client -> broker -> queryprocessor -> broker -> storagemanager -> WiredTiger),
because _query is a thin facade that must be forwarded to a registered
queryprocessor to do anything.
"""

from types import SimpleNamespace

import pytest


def test_client_serves_no_incoming_rpc(client_component):
    with pytest.raises(RuntimeError, match="Client serves no RPC"):
        client_component.responder.invoke(SimpleNamespace(name="anything"), {})


def test_client_query_creates_table(stack):
    assert stack.client._query(query="CREATE TABLE t_client;") == {}


def test_client_query_deletes_table(stack):
    stack.client._query(query="CREATE TABLE t_del;")

    assert stack.client._query(query="DELETE TABLE t_del;") == {}
