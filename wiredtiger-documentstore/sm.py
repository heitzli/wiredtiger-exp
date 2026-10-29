from pathlib import Path

from components import StorageManagerComponent

if __name__ == "__main__":
    sm = StorageManagerComponent(
        name="storagemanager",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/storagemanager.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/sm.sock",
    )

    sm.run()
