from pathlib import Path

from components.client import Client

client = Client(
    name="in_code_client",
    broker_protocol_path=Path("protocol/broker.avpr").absolute(),
    protocol_path=Path("protocol/client.avpr").absolute(),
    broker_socket="/tmp/broker.sock",
    component_socket="/tmp/client.sock",
)

val = client.evaluate(query="PUT testkey 12345;")
print(f"received : {val}")

val2 = client.evaluate(query="GET testkey;")
print(f"received : {val2}")

val3 = client.evaluate(query="DELETE testkey;")
print(f"received : {val3}")
