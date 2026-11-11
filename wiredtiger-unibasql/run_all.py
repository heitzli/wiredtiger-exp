"""Starts the full UnibaSQL component stack (broker + five components) in background threads.

Each component serves on its usual unix socket. Ctrl+C stops them all: worker queues are
drained, listeners closed, socket files unlinked.
"""

import logging
import os
import threading
import time
from pathlib import Path

from components import QueryOptimizer, QueryProcessor
from components.broker import Broker
from components.catalog import CatalogComponent
from components.component import Component
from components.executionengine.executionengine_component import ExecutionEngine
from components.storagemanager import StorageManagerComponent

logging.basicConfig(level=logging.INFO)

SOCKET_PATHS = [
    "/tmp/broker.sock",
    "/tmp/catalog.sock",
    "/tmp/sm.sock",
    "/tmp/queryprocessor.sock",
    "/tmp/qo.sock",
    "/tmp/ee.sock",
]
ROOT = Path(__file__).parent


def _serve(component: Component, label: str, stop_done: threading.Event) -> None:
    """Wraps run() so a closed listening socket (our shutdown path) ends the thread quietly."""
    try:
        component.run()
    except OSError:
        pass  # accept() aborted by sock.close() during shutdown
    finally:
        stop_done.set()
        logging.info("%s stopped", label)


def main() -> None:
    threads: list[threading.Thread] = []
    stop_events: list[threading.Event] = []

    # 1) broker first — everything else registers against it in __init__
    broker = Broker(protocol_path=ROOT / "protocol" / "broker.avpr")
    ev = threading.Event()
    threads.append(threading.Thread(target=_serve, args=(broker, "broker", ev), name="broker-listener", daemon=True))
    stop_events.append(ev)
    threads[-1].start()
    while not os.path.exists("/tmp/broker.sock"):  # tiny wait until it listens
        time.sleep(0.05)

    # 2) components; constructing registers each with the broker
    factories = {
        "catalog": lambda: CatalogComponent(
            name="catalog",
            broker_protocol_path=ROOT / "protocol" / "broker.avpr",
            protocol_path=ROOT / "protocol" / "catalog.avpr",
            broker_socket="/tmp/broker.sock",
            component_socket="/tmp/catalog.sock",
        ),
        "storagemanager": lambda: StorageManagerComponent(
            name="storagemanager",
            broker_protocol_path=ROOT / "protocol" / "broker.avpr",
            protocol_path=ROOT / "protocol" / "storagemanager.avpr",
            broker_socket="/tmp/broker.sock",
            component_socket="/tmp/sm.sock",
        ),
        "queryprocessor": lambda: QueryProcessor(
            name="queryprocessor",
            broker_protocol_path=ROOT / "protocol" / "broker.avpr",
            protocol_path=ROOT / "protocol" / "queryprocessor.avpr",
            broker_socket="/tmp/broker.sock",
            component_socket="/tmp/queryprocessor.sock",
        ),
        "queryoptimizer": lambda: QueryOptimizer(
            name="queryoptimizer",
            broker_protocol_path=ROOT / "protocol" / "broker.avpr",
            protocol_path=ROOT / "protocol" / "queryoptimizer.avpr",
            broker_socket="/tmp/broker.sock",
            component_socket="/tmp/qo.sock",
        ),
        # mirrors the SM's stream_* surface (opaque relay — see components/executionengine/)
        "executionengine": lambda: ExecutionEngine(
            name="executionengine",
            broker_protocol_path=ROOT / "protocol" / "broker.avpr",
            protocol_path=ROOT / "protocol" / "executionengine.avpr",
            broker_socket="/tmp/broker.sock",
            component_socket="/tmp/ee.sock",
        ),
    }

    components = []
    for label, factory in factories.items():
        component = factory()
        components.append((label, component))
        ev = threading.Event()
        threads.append(threading.Thread(target=_serve, args=(component, label, ev), name=label, daemon=True))
        stop_events.append(ev)
        threads[-1].start()
        time.sleep(0.1)  # let registration land before the next one

    logging.info(
        "stack up: broker, catalog, storagemanager, queryprocessor, queryoptimizer, executionengine — Ctrl+C to stop"
    )

    try:
        while any(t.is_alive() for t in threads):
            threading.Event().wait(timeout=0.5)
    except KeyboardInterrupt:
        logging.info("shutting down stack...")
        # order matters: producers first, broker last
        for (label, component), thread, ev in reversed(list(zip(components, threads[1:], stop_events[1:]))):
            logging.info("stopping %s", label)
            try:
                component.shutdown()  # drains the worker queue (joins <= 10 s)
            except Exception as e:
                logging.warning("%s: shutdown() failed: %s", label, e)
            try:
                component.close()  # broker-side connection
            except Exception as e:
                logging.debug("%s: close() failed: %s", label, e)
            try:
                component.sock.close()  # unblocks accept() -> thread exits
            except Exception as e:
                logging.debug("%s: sock.close() failed: %s", label, e)
            thread.join(timeout=3)

        logging.info("stopping broker")
        broker.close()  # listener + LogServer
        for path in SOCKET_PATHS:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        logging.info("bye")


if __name__ == "__main__":
    main()
