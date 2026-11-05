from pathlib import Path

from components.queryprocessor import QueryProcessor

qp = QueryProcessor(
    name="queryprocessor",
    broker_protocol_path=Path("protocol/broker.avpr").absolute(),
    protocol_path=Path("protocol/queryprocessor.avpr").absolute(),
    broker_socket="/tmp/broker.sock",
    component_socket="/tmp/queryprocessor.sock",
)


if __name__ == "__main__":
    qp.run()
