from pathlib import Path

from components.catalog import CatalogComponent

if __name__ == "__main__":
    catalog = CatalogComponent(
        name="catalog",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/catalog.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/catalog.sock",
    )

    catalog.run()
