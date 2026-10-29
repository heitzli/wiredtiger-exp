"""Shared fixtures for component-level and end-to-end tests.

The fixtures boot pieces of the system in-process (broker + components on temp
unix sockets + a temp WiredTiger home) so components can be tested without
manually running broker.py / qp.py / sm.py first.

NOTE: components.broker.Broker hardcodes its listening socket at
/tmp/broker.sock, so manually started dev processes (broker.py, qp.py, sm.py)
must be stopped before running the test suite, otherwise the tests take over
that socket path.
"""

import shutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from components.broker import Broker
from components.client import Client
from components.queryprocessor import QueryProcessor
from components.storagemanager import StorageManagerComponent

BROKER_SOCKET = "/tmp/broker.sock"  # hardcoded inside components.broker.Broker


def _listen_in_background(runnable) -> None:
    thread = threading.Thread(target=runnable, daemon=True)
    thread.start()


@pytest.fixture
def project_root(pytestconfig):
    return Path(pytestconfig.rootdir)


@pytest.fixture
def wt_home(tmp_path):
    """Isolated WiredTiger home per test, passed to the storage manager explicitly."""
    return tmp_path / "WT_DOC_HOME"


@pytest.fixture
def socket_dir():
    """Short unix-socket directory (macOS limits AF_UNIX paths to ~104 chars, pytest tmp paths are longer)."""
    base = Path(tempfile.mkdtemp(prefix="wtds-tests-"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def broker(project_root):
    broker = Broker(protocol_path=project_root / "protocol" / "broker.avpr")
    _listen_in_background(broker.run)
    try:
        yield broker
    finally:
        broker.sock.close()


@pytest.fixture
def storage_manager(broker, wt_home, project_root, socket_dir):
    sm = StorageManagerComponent(
        name="storagemanager",
        broker_protocol_path=project_root / "protocol" / "broker.avpr",
        protocol_path=project_root / "protocol" / "storagemanager.avpr",
        broker_socket=BROKER_SOCKET,
        component_socket=str(socket_dir / "storagemanager.sock"),
        wiredtiger_home=str(wt_home),
    )
    _listen_in_background(sm.run)
    try:
        yield sm
    finally:
        sm.shutdown()
        sm.close()
        sm.sock.close()
        sm.connection.close()


@pytest.fixture
def query_processor(broker, project_root, socket_dir):
    qp = QueryProcessor(
        name="queryprocessor",
        broker_protocol_path=project_root / "protocol" / "broker.avpr",
        protocol_path=project_root / "protocol" / "queryprocessor.avpr",
        broker_socket=BROKER_SOCKET,
        component_socket=str(socket_dir / "queryprocessor.sock"),
    )
    _listen_in_background(qp.run)
    try:
        yield qp
    finally:
        qp.shutdown()
        qp.close()
        qp.sock.close()


@pytest.fixture
def client_component(broker, project_root, socket_dir):
    """Client registered at the broker; usable as a generic RPC endpoint into the stack."""
    client = Client(
        name="client",
        broker_protocol_path=project_root / "protocol" / "broker.avpr",
        protocol_path=project_root / "protocol" / "client.avpr",
        broker_socket=BROKER_SOCKET,
        component_socket=str(socket_dir / "client.sock"),
    )
    try:
        yield client
    finally:
        client.shutdown()
        client.close()
        client.sock.close()


@pytest.fixture
def stack(broker, storage_manager, query_processor, client_component, wt_home):
    """Full system: broker + storagemanager + queryprocessor + client in one process."""
    return SimpleNamespace(
        broker=broker,
        storagemanager=storage_manager,
        queryprocessor=query_processor,
        client=client_component,
        wt_home=wt_home,
    )
