"""Launcher for the query optimizer component."""

from pathlib import Path

from components import QueryOptimizer

if __name__ == "__main__":
    qo = QueryOptimizer(
        name="queryoptimizer",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/queryoptimizer.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/qo.sock",
    )

    qo.run()
