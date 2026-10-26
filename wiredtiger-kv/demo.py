from pathlib import Path

from components.client import Client

if __name__ == "__main__":
    client = Client(
        name="in_code_client",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/client.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/client.sock",
    )

    # val = client._query(query="PUT testkey 12345;")
    # print(f"_query received : {val}")

    try:
        val2 = client.get(key="testkey")
        print(f" get received : {val2}")
    except Exception as e:
        print(e)
    try:
        val2 = client.delete(key="testkey")
        print(f" get received : {val2}")
    except Exception as e:
        print(e)

    try:
        val2 = client._query(query="PUTHD adkdk dkdkdk")
        print(f" get received : {val2}")
    except Exception as e:
        print(e)

    val = client.put(key="testkey", value="123456")
    print(f"put received : {val}")

    val2 = client.get(key="testkey")
    print(f" get received : {val2}")

    val3 = client.delete(key="testkey")
    print(f" get received : {val3}")
