from pathlib import Path
from pydoc import cli

from components import Client

if __name__ == "__main__":
    client = Client(
        name="in_code_client",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/client.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/client.sock",
    )

    # print(client.begin_transaction())

    print(
        client._query(
            query="CREATE TABLE test (id INT PRIMARY KEY, name STRING, salary DOUBLE, active BOOL, id2 INT PRIMARY KEY)"
        )
    )
    # NOTE works with QueryAnalyzer(bypass_catalog=True)
    print(client._query("SELECT * FROM test;"))
    print(client._query(query="CREATE INDEX test_idx ON test (id, salary);"))
    # print(client.commit())
    print(client._query(query="DROP INDEX test_idx ON test;"))
    print(client._query(query="DROP TABLE test;"))
