"""Tests with several concurrent clients against one running system.

Every additional client registers under its own name with its own component
socket and therefore owns its own connection to the broker; the broker serves
each connection in its own thread. These tests exercise broker routing, the
query processor and the storage manager while multiple clients hammer them at
the same time.

NOTE on concurrent transactions: every component processes its requests
through a single worker thread (component.Component:_serve), so racing client
requests are serialized inside each component and never overlap transactions
*inside* the storage manager. Write-write conflicts between transactions can
therefore not surface between client requests; on this WT build they would
only occur if two transactions overlapped directly (e.g. two sessions on one
connection).
"""

import threading
import time
from collections import Counter

import pytest

from components.client import Client
from components.conftest import BROKER_SOCKET

CLIENT_TIMEOUT = 30


@pytest.fixture
def make_client(stack, project_root, socket_dir):
    """Factory for extra clients, each with a unique name and component socket."""
    created: list[Client] = []

    def make(index: int) -> Client:
        client = Client(
            name=f"concurrent_client_{index}",
            broker_protocol_path=project_root / "protocol" / "broker.avpr",
            protocol_path=project_root / "protocol" / "client.avpr",
            broker_socket=BROKER_SOCKET,
            component_socket=str(socket_dir / f"client_{index}.sock"),
        )
        created.append(client)
        return client

    yield make

    for client in created:
        client.shutdown()
        client.close()
        client.sock.close()


def run_in_threads(workers: list) -> None:
    """Run worker callables concurrently; join with a timeout so a wedged
    request fails the test instead of hanging the whole suite."""
    threads = [threading.Thread(target=worker) for worker in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=CLIENT_TIMEOUT)
    assert not any(thread.is_alive() for thread in threads), "a worker did not finish in time"


def test_concurrent_clients_insert_into_shared_table(stack, make_client):
    """Several distinct clients insert into the same table at the same time:
    every document must arrive exactly once, with the owner attribute naming
    the client that inserted it, and the reported docids must match the
    stored rows."""
    assert stack.client.create_table("shared") == {}

    client_count, docs_per_client = 4, 5
    client_names = [f"concurrent_client_{index}" for index in range(client_count)]
    errors: list = []
    reported_docids: dict[int, set] = {}
    lock = threading.Lock()

    def worker(index: int) -> None:
        client = make_client(index)
        try:
            docs = [{"owner": client.name, "i": i} for i in range(docs_per_client)]
            result = client.insert("shared", docs)
            with lock:
                reported_docids[index] = set(result)
        except Exception as e:  # noqa: BLE001
            with lock:
                errors.append((client.name, e))

    run_in_threads([lambda index=index: worker(index) for index in range(client_count)])
    assert errors == []
    assert set(reported_docids) == set(range(client_count))

    # an independent client reads everything back
    reader = make_client(client_count)
    documents = reader.select("shared", ["owner", "i", "_id"], None)

    assert len(documents) == client_count * docs_per_client
    docids = set(documents)
    assert len(docids) == len(documents), "duplicate docids across concurrently inserted documents"
    assert set().union(*reported_docids.values()) == docids

    per_owner = Counter(attrs["owner"] for attrs in documents.values())
    assert per_owner == Counter({name: docs_per_client for name in client_names})
    assert all(0 <= attrs["i"] < docs_per_client for attrs in documents.values())


def test_concurrent_clients_full_crud_on_own_tables(stack, make_client):
    """Each client drives a complete create -> insert -> select -> update ->
    select -> delete -> delete_table cycle on its own table, all in parallel."""
    client_count = 3
    errors: list = []

    def worker(index: int) -> None:
        client = make_client(index)
        table = f"table_of_concurrent_client_{index}"
        try:
            assert client.create_table(table) == {}

            client.insert(table, [{"n": i, "owner": client.name} for i in range(5)])
            rows = client.select(table, ["n"], None)
            assert sorted(attrs["n"] for attrs in rows.values()) == list(range(5))

            client.update(table, ("n", 7))
            rows = client.select(table, ["n"], None)
            assert all(attrs["n"] == 7 for attrs in rows.values())

            client.delete(table, None)
            assert client.select(table, ["n"], None) == {}
            assert client.delete_table(table) == {}
        except Exception as e:  # noqa: BLE001
            errors.append((client.name, e))

    run_in_threads([lambda index=index: worker(index) for index in range(client_count)])
    assert errors == []


def test_concurrent_readers_observe_writers_progress(stack, make_client):
    """While one client updates a counter repeatedly, other clients poll the
    table with select: readers must only ever see committed values, and after
    the writer finishes the last write must be visible to everyone."""
    assert stack.client.create_table("counter") == {}
    assert stack.client.insert("counter", [{"counter": -1}]) != {}

    updates = 10
    reader_count = 3
    errors: list = []
    stop = threading.Event()

    def reader(index: int) -> None:
        client = make_client(50 + index)
        try:
            while not stop.is_set():
                rows = client.select("counter", ["counter"], None)
                values = [attrs["counter"] for attrs in rows.values() if "counter" in attrs]
                assert all(value == -1 or 0 <= value < updates for value in values), (client.name, values)
                time.sleep(0.01)
        except Exception as e:  # noqa: BLE001
            errors.append((client.name, e))
            stop.set()

    def writer() -> None:
        client = make_client(99)
        try:
            for i in range(updates):
                client.update("counter", ("counter", i))
        except Exception as e:  # noqa: BLE001
            errors.append((client.name, e))

    readers = [threading.Thread(target=reader, args=(index,)) for index in range(reader_count)]
    writer_thread = threading.Thread(target=writer)
    for reader_thread in readers:
        reader_thread.start()
    writer_thread.start()
    writer_thread.join(timeout=CLIENT_TIMEOUT)
    assert not writer_thread.is_alive(), "writer did not finish in time"

    stop.set()
    for reader_thread in readers:
        reader_thread.join(timeout=CLIENT_TIMEOUT)
    assert not any(reader_thread.is_alive() for reader_thread in readers), "readers did not stop in time"

    assert errors == []
    final = stack.client.select("counter", ["counter"], None)
    assert [attrs["counter"] for attrs in final.values()] == [updates - 1]
