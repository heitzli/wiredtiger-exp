from pathlib import Path

from components import Client

if __name__ == "__main__":
    client = Client(
        name="in_code_client",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/client.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/client.sock",
    )

    try:
        val1 = client._query(query="CREATE TABLE test123;")
        print(f" get received : {val1}")
    except Exception as e:
        print(e)

    try:
        val2 = client._query(query="DELETE TABLE test123;")
        print(f" get received : {val2}")
    except Exception as e:
        print(e)

    try:
        val3 = client._query(query="DELETE TABLE test123;")
        print(f" get received : {val3}")
    except Exception as e:
        print(e)
