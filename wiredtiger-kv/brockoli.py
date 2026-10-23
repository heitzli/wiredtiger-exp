from pathlib import Path

from components.broker import Broker

if __name__ == "__main__":
    b = Broker(protocol_path=Path("protocol/broker.avpr").absolute())
    b.run()
