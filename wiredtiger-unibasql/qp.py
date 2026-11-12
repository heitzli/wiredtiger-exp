"""Launcher for the query processor; constructs a QueryProcessor with bypass_catalog=False."""

from pathlib import Path

from components import QueryProcessor

qp = QueryProcessor(
    name="queryprocessor",
    broker_protocol_path=Path("protocol/broker.avpr").absolute(),
    protocol_path=Path("protocol/queryprocessor.avpr").absolute(),
    broker_socket="/tmp/broker.sock",
    component_socket="/tmp/queryprocessor.sock",
    bypass_catalog=False,
)


if __name__ == "__main__":
    qp.run()
