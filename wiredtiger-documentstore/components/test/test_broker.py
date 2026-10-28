"""Isolated tests for components.broker.Broker (responder-level, no components attached)."""

import json

import pytest

from components.message_codec import Message


def test_register_stores_component_and_protocol(broker, project_root):
    with open(project_root / "protocol" / "storagemanager.avpr") as f:
        protocol_json = f.read()

    res = broker.responder.register(
        {"request": {"name": "storagemanager", "socket": "/tmp/unused.sock", "protocol": protocol_json}}
    )

    assert res == {"result": 0}
    assert broker.components == {"storagemanager": "/tmp/unused.sock"}


def test_get_component_protocol_returns_registered_protocol(broker, project_root):
    with open(project_root / "protocol" / "storagemanager.avpr") as f:
        protocol_json = f.read()
    broker.responder.register(
        {"request": {"name": "storagemanager", "socket": "/tmp/unused.sock", "protocol": protocol_json}}
    )

    res = broker.responder.get_component_protocol({"request": {"name": "storagemanager"}})

    parsed = json.loads(res["protocol"])
    assert parsed["protocol"] == "StorageManager"
    assert "execute" in parsed["messages"]


def test_get_component_protocol_of_unknown_component_is_none(broker):
    res = broker.responder.get_component_protocol({"request": {"name": "ghost"}})

    assert res == {"protocol": None}


def test_forward_unknown_destination_raises_keyerror(broker):
    message = Message(source="client", destination="ghost", correlation_id=1, payload=b"")

    with pytest.raises(KeyError, match="ghost"):
        broker.forward(message)
