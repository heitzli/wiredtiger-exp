from socket import socket as Socket

import avro.ipc


class FramedSocket:
    """
    Adapt a Unix socket to Avro's Requestor transceiver interface
    while using Avro's framed-message transport.
    """

    def __init__(self, sock: Socket, remote_name: str):
        """Wrap `sock` for logging as `remote_name`; exposes Avro framed reader/writer on top."""
        self.sock = sock
        self.remote_name = remote_name
        self.reader = avro.ipc.FramedReader(self)
        self.writer = avro.ipc.FramedWriter(self)

    def read(self, size: int = -1) -> bytes:
        """Read exactly `size` bytes (Avro's framer depends on exact-length fills); unbounded reads unsupported."""
        if size < 0:
            raise ValueError("unbounded read is not supported")

        if size == 0:
            return b""

        data = bytearray()

        while len(data) < size:
            chunk = self.sock.recv(size - len(data))

            if not chunk:
                raise ConnectionError("socket closed while reading")

            data.extend(chunk)

        return bytes(data)

    def write(self, data) -> int:
        """Send all bytes in one sendall; returns the count."""
        self.sock.sendall(data)
        return len(data)

    def flush(self):
        """No-op; the socket provides no intermediate buffering."""
        pass

    def transceive(self, request: bytes) -> bytes:
        """Write one framed request and read one framed reply."""
        self.writer.write_framed_message(request)
        return self.reader.read_framed_message()

    def close(self):
        self.sock.close()
