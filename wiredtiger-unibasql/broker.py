from pathlib import Path

from components.broker import Broker

if __name__ == "__main__":
    Broker(protocol_path=Path(__file__).parent / "protocol" / "broker.avpr").run()
