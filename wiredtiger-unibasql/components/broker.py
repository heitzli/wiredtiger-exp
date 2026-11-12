from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from socket import socket as Socket
from typing import TYPE_CHECKING, Any, TypeAlias

import avro.ipc
import avro.protocol

from .framedsocket import FramedSocket
from .logger import env_flag, get_logger
from .logserver import LogServer
from .message_codec import Message, decode_message, encode_message, message_from_dict, message_to_dict

if TYPE_CHECKING:
    from avro.protocol import Protocol


RequestDict: TypeAlias = dict[str, Any]
ResultDict: TypeAlias = dict[str, Any]

log = get_logger("broker")


class BrokerResponder(avro.ipc.Responder):
    """Avro responder for the broker's own RPC surface: register, sendMessage, getComponentProtocol."""

    def __init__(self, protocol: avro.protocol.Protocol, broker: Broker) -> None:
        """Initialize with the broker protocol and a back-reference for routing."""
        super().__init__(local_protocol=protocol)
        self._broker: Broker = broker

    def invoke(self, message: avro.protocol.Message, request: RequestDict):  # type: ignore
        """Dispatch one broker-side RPC to its handler by message name."""
        method = message.name

        if method == "register":
            return self.register(request)

        if method == "sendMessage":
            return self.send_message(request)

        if method == "getComponentProtocol":
            return self.get_component_protocol(request)

        log.error("unknown rpc %r", method)
        raise RuntimeError(f"Unknown RPC method: {method}")

    def send_message(self, request: RequestDict) -> ResultDict:
        """Forward one client Message via Broker.forward and wrap the reply back as a dict."""
        message = message_from_dict(request["message"])
        log.info(
            "route",
            extra={
                "peer": message.destination,
                "correlation_id": message.correlation_id,
                "sequence_id": message.sequence_id,
                "content": message.payload,
            },
        )
        reply = self._broker.forward(message)
        return message_to_dict(reply)

    def register(self, request: RequestDict) -> dict[str, int]:
        """Record a component's socket path and protocol under its name."""
        inner = request["request"]
        name = inner["name"]
        socket_path = inner["socket"]
        component_protocol: str = inner["protocol"]

        with self._broker.brocker_lock:
            self._broker.components[name] = socket_path
            log.info("registered %s at %s", name, socket_path)
            self._broker.known_protocols[name] = avro.protocol.parse(component_protocol)

        return {"result": 0}

    def get_component_protocol(self, request):
        """Return the named component's protocol as JSON (None if unknown)."""
        inner = request["request"]
        name = inner["name"]

        with self._broker.brocker_lock:
            protocol = self._broker.known_protocols.get(name)
        log.info("protocol request for %s", name)
        protocol = json.dumps(protocol.to_json()) if protocol else None
        return {"protocol": protocol}


class Broker:
    """Central message broker routing opaque Messages between components over Unix sockets.

    On registration each component sends all messages of its protocol; other
    components can look up the message format by target name (getComponentProtocol).
    """

    def __init__(self, protocol_path: Path) -> None:
        """Bind the responder (with back-reference to self), the listener socket, and the log collector."""
        self.responder = BrokerResponder(protocol=self._load_protocol(protocol_path=protocol_path), broker=self)
        self.sock = self.start_unix_server(path="/tmp/broker.sock")
        self.components: dict[str, str] = dict()
        self.brocker_lock: threading.Lock = threading.Lock()

        self.known_protocols: dict[str, Protocol] = dict()
        try:
            self.log_server: LogServer | None = LogServer(print_entries=env_flag("LOG_PRINT"))
            self.log_server.start_background()
        except OSError:
            self.log_server = None

    def forward(self, message: Message) -> Message:
        """Route an opaque Message to its destination and return the reply Message."""
        socket_path = self.components.get(message.destination)
        if socket_path is None:
            log.error("unknown destination %r", message.destination)
            raise KeyError(f"Unknown destination {message.destination!r}; registered: {sorted(self.components)}")

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(socket_path)
            framed_socket = FramedSocket(sock, remote_name=f"unix:{socket_path}")
            framed_socket.writer.write_framed_message(message=encode_message(message))
            reply = decode_message(framed_socket.reader.read_framed_message())

        if reply.correlation_id != message.correlation_id:
            log.error("correlation mismatch")
            raise RuntimeError(f"Correlation mismatch: sent {message.correlation_id}, got {reply.correlation_id}")
        log.info(
            "reply",
            extra={
                "peer": reply.destination,
                "correlation_id": reply.correlation_id,
                "sequence_id": reply.sequence_id,
                "content": reply.payload,
            },
        )
        return reply

    def _load_protocol(self, protocol_path: Path) -> Protocol:
        """Parse the broker's own protocol from an .avpr file."""
        with open(file=str(protocol_path.absolute()), mode="r", encoding="utf-8") as f:
            return avro.protocol.parse(json_string=f.read())

    def start_unix_server(self, path: str) -> Socket:
        """Bind a fresh AF_UNIX listener, replacing any stale socket file at `path`."""
        if os.path.exists(path):
            os.unlink(path)
        server = socket.socket(
            family=socket.AF_UNIX,
            type=socket.SOCK_STREAM,
        )
        server.bind(path)
        server.listen()
        return server

    def close(self) -> None:
        """Shut down the log collector (if any) and the listener socket."""
        if self.log_server is not None:
            self.log_server.close()
        self.sock.close()

    def run(self) -> None:
        """Accept connections forever, spawning one daemon thread per client."""
        while True:
            client_sock, address = self.sock.accept()
            thread = threading.Thread(target=self._handle_connection, args=(client_sock, address), daemon=True)
            thread.start()

    def _handle_connection(self, sock: socket.socket, address: str) -> None:
        """Serve one client: read framed requests and answer each with a framed response."""
        log.info("connect from %r", address)
        framed_socket = FramedSocket(sock, remote_name=f"unix:{address}")
        reader = avro.ipc.FramedReader(framed_socket)
        writer = avro.ipc.FramedWriter(framed_socket)

        try:
            while True:
                try:
                    request = reader.read_framed_message()
                except Exception as exc:
                    log.debug("connection closed: %s", exc)
                    break
                if not request:
                    break
                try:
                    response = self.responder.respond(call_request=request)
                    writer.write_framed_message(message=response)
                except Exception as exc:
                    log.exception("request failed: %s", exc)
                    raise
        finally:
            framed_socket.close()
            log.debug("disconnect")
