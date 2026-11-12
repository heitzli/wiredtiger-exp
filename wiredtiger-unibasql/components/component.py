import itertools
import json
import os
import queue
import socket
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from socket import socket as Socket
from typing import Any

import avro.ipc
import avro.protocol
from avro.protocol import Protocol

from .errors import WiredTigerError, error_from_datum
from .framedsocket import FramedSocket
from .logger import get_logger
from .message_codec import (
    CODEC,
    Message,
    decode_avro_datum,
    decode_message,
    decode_payload_envelope,
    encode_error_payload,
    encode_message,
    encode_payload,
    encode_response_payload,
    message_from_dict,
    message_to_dict,
)
from .remote_stream import RemoteStream
from .stream_registry import StreamRegistry

DEFAULT_STREAM_TTL_SECONDS = 60.0
DEFAULT_STREAM_PAGE_CAP = 4096
DEFAULT_RELAY_PAGE = 256


@dataclass
class _ReplyChannel:
    """Write-back path for one accepted connection. Only the worker writes."""

    writer: Any
    lock: threading.Lock = field(default_factory=threading.Lock)
    closed: bool = False
    future: Future[Message] | None = None
    framed_socket: Any = None


@dataclass
class _QueuedRequest:
    """One inbound request awaiting the worker, together with its reply channel."""

    raw: bytes
    channel: _ReplyChannel


class Component(ABC):
    """Component with a single inbox queue and one worker thread.

    Reader threads (one per accepted connection) only read frames and enqueue
    them. The worker is the sole thread invoking the responder, so handler
    state is serialized by construction.

    Constraint: call graphs must be free of re-entrant cycles, i.e. a handler
    must not issue a request that transitively waits on its own worker.
    Downward chains (A -> B -> C with replies unwinding) are fine.

    Streaming is generic: EVERY component can own streams — locally produced
    (`register_stream`) or mirrored relays of an upstream (`open_stream_relay`).
    Serving stream_next/stream_close is generic, so concrete components never
    parse stream frames.
    """

    def __init__(
        self, name: str, broker_protocol_path: Path, protocol_path: Path, broker_socket: str, component_socket: str
    ) -> None:
        """Load protocols, open the component listener and broker connection, and start the worker.

        Responder invocation is worker-thread-confined (no internal locking needed);
        `_broker_lock` serializes transceive on the single shared broker connection.
        Registers with the broker before returning.
        """
        self.name = name
        self.log = get_logger(name)
        self.socket_path: str = component_socket
        self.sock = self.start_unix_server(path=component_socket)
        self.broker_protocol = self._load_protocol(protocol_path=broker_protocol_path)
        self.protocol = self._load_protocol(protocol_path=protocol_path)
        self.component_protocols: dict[str, Protocol] = dict()
        self.broker_connection = self._connect_to_broker(broker_socket=broker_socket)

        self.requestor = avro.ipc.Requestor(self.broker_protocol, self.broker_connection)
        self.responder = self._create_responder(self.protocol)
        self._correlation_ids = itertools.count(1)
        self._sequence_ids = itertools.count(1)
        self._broker_lock = threading.Lock()
        self._inbox: queue.Queue[_QueuedRequest | None] = queue.Queue()
        self._worker_ident: int | None = None
        self.registry = StreamRegistry()
        self.stream_ttl_seconds = DEFAULT_STREAM_TTL_SECONDS
        self.stream_page_cap = DEFAULT_STREAM_PAGE_CAP
        self.stream_relay_page = DEFAULT_RELAY_PAGE
        self._worker = threading.Thread(target=self._serve, daemon=True, name=f"{name}-worker")
        self._worker.start()
        self.register()

    @abstractmethod
    def _create_responder(self, protocol: Protocol) -> avro.ipc.Responder:
        """Define custom responder class that is used to handle rpc calls by the component."""
        ...

    def register_stream(
        self, iterator: Iterator[Any], resource: Any = None, closer: Callable[[], None] | None = None
    ) -> str:
        """Publishes a locally-produced row iterator as a pull stream; returns the client-facing id."""
        return self.registry.open(iterator, resource=resource, closer=closer)

    def open_stream_relay(
        self,
        upstream: str,
        init_reply: dict,
        *,
        transform: Callable[[Iterable[Any]], Iterator[Any]] | None = None,
    ) -> str:
        """Mirror an upstream stream one hop behind (init_reply from `call_remote`): the RETURNED id
        is the client-facing one (upstream sid consumed invisibly; non-stream replies handled by
        the caller). Optional `transform` intercepts each page (iterable -> iterable) to run
        operators in flight; its exceptions propagate untouched.
        """
        upstream_stream = RemoteStream(self, upstream, init_reply, self.stream_relay_page)
        upstream_sid = init_reply["stream_id"]

        def mirrored_rows() -> Iterator[Any]:
            try:
                for page_rows in upstream_stream.pages():
                    yield from transform(page_rows) if transform is not None else page_rows
            except Exception as e:
                # NOTE morph foreign RemoteErrors into OUR declared error type
                raise WiredTigerError(f"Upstream {upstream} stream failed: {e}") from e
            finally:
                upstream_stream.close()  # started-lifecycle; the registry closer covers never-started

        relay_sid = self.registry.open(mirrored_rows(), closer=upstream_stream.close)
        self.log.info("relay stream opened", extra={"stream_id": relay_sid, "upstream": upstream_sid})
        return relay_sid

    def discard_stream(self, stream_id: str) -> bool:
        """Forced disposal (e.g. TxEnd releasing leaked scans); True if a live stream died."""
        closed = self.registry.close(stream_id)
        if closed:
            self._on_stream_gone(stream_id)
        return closed

    def _serve_stream_next(self, request: dict) -> dict:
        """Generic stream_next endpoint — no component-specific code below this line."""
        self._stream_maintenance()
        try:
            rows, done = self.registry.next(
                stream_id=request["stream_id"], max_items=max(1, min(int(request["max_items"]), self.stream_page_cap))
            )
        except ValueError as e:
            raise WiredTigerError(f"Unknown stream {request['stream_id']!r}") from e
        if done:
            self.log.info("stream exhausted", extra={"stream_id": request["stream_id"]})
            self._on_stream_gone(request["stream_id"])
        return {"rows": rows, "done": done}

    def _serve_stream_close(self, request: dict) -> bool:
        """Generic stream_close endpoint; disposing the mirror generator closes upstreams too."""
        self._stream_maintenance()
        closed = self.registry.close(request["stream_id"])
        if closed:
            self._on_stream_gone(request["stream_id"])
        self.log.info("stream closed", extra={"stream_id": request["stream_id"], "was_open": closed})
        return closed

    def _stream_maintenance(self) -> None:
        """Opportunistic TTL sweep; runs at every stream RPC (no timer thread needed)."""
        for swept in self.registry.sweep(self.stream_ttl_seconds):
            self._on_stream_gone(swept)

    def _on_stream_gone(self, stream_id: str) -> None:
        """Hook: a stream ended (exhausted / closed / swept / force-discarded). Components with
        additional ownership bookkeeping (e.g. StorageManager tx leases) override this."""

    def _load_protocol(self, protocol_path: Path) -> Protocol:
        """Helper function to load the protocol implemented by the component."""
        with open(protocol_path, "r", encoding="utf-8") as f:
            return avro.protocol.parse(f.read())

    def _connect_to_broker(self, broker_socket: str) -> FramedSocket:
        """Create longlived connection to broker."""
        sock = Socket(family=socket.AF_UNIX, type=socket.SOCK_STREAM)
        sock.connect(broker_socket)
        return FramedSocket(sock=sock, remote_name=f"unix:{broker_socket}")

    def next_correlation_id(self) -> int:
        """Create correlation ids to match messages, currently not strictly necessary but might be helpful in the future."""
        return next(self._correlation_ids)

    def next_sequence_id(self) -> int:
        """Sequence id of this component's outbound messages (tcp-like: monotonically increasing per sender)."""
        return next(self._sequence_ids)

    def register(self) -> Any:
        """Register the component to the broker. Maybe add more metadata in the future depending on the role of the broker."""
        self.log.debug("registering with broker")
        return self.requestor.request(
            "register",
            {
                "request": {
                    "name": self.name,
                    "socket": self.socket_path,
                    "protocol": json.dumps(self.protocol.to_json()),
                }
            },
        )

    def send_message(self, message: Message) -> Message:
        """Wrap the request in a message that is sent through the broker to the destination. The payload is opaque to the broker."""
        # Serializes all requestor.request calls on the shared broker_connection.
        with self._broker_lock:
            reply_datum = self.requestor.request(
                message_name="sendMessage",
                request_datum={"message": message_to_dict(message)},
            )
        return message_from_dict(reply_datum)

    def _remote_message(self, rpc_name: str, remote_name: str) -> Any:
        """Helper function to retrieve the remote protocol's message definition."""
        destination_protocol = self.component_protocols.get(remote_name)
        if destination_protocol is None:
            proto = self.requestor.request(
                "getComponentProtocol",
                {
                    "request": {
                        "name": remote_name,
                    }
                },
            )
            protcol = proto["protocol"]
            if protcol is None:
                raise ValueError(f"Unknown remote component {remote_name!r}")
            self.component_protocols[remote_name] = avro.protocol.parse(protcol)
            self.log.debug("protocol fetched from %s", remote_name)
            destination_protocol = self.component_protocols[remote_name]

        avro_message = destination_protocol.messages.get(rpc_name)  # type: ignore
        if avro_message is None:
            raise ValueError(
                f"Unknown remote RPC {rpc_name!r} on {remote_name!r}; known: {sorted(destination_protocol.messages)}"  # type: ignore
            )
        return avro_message

    def _request_schema(self, rpc_name: str, destination: str) -> Any:
        """Helper function to retrieve the request schema"""
        return self._remote_message(rpc_name=rpc_name, remote_name=destination).request

    def _response_schema(self, rpc_name: str, remote_name: str) -> Any:
        """Helper function to retrieve the response schema"""
        return self._remote_message(rpc_name=rpc_name, remote_name=remote_name).response

    def _errors_schema(self, rpc_name: str, remote_name: str) -> Any:
        """Helper function to retrieve the declared errors union of the remote RPC."""
        return self._remote_message(rpc_name=rpc_name, remote_name=remote_name).errors

    def create_message(self, datum: dict, request_name: str, destination: str) -> Message:
        """Encapsulate an Avro RPC call into an opaque Message.

        datum is the full request datum, e.g. {"request": {...}} for lock.avpr.
        The method name is nested inside payload so the broker stays opaque.
        """
        payload = encode_payload(request_name, datum, self._request_schema(request_name, destination))
        return Message(
            source=self.name,
            destination=destination,
            correlation_id=self.next_correlation_id(),
            sequence_id=self.next_sequence_id(),
            payload=payload,
        )

    def _decode_reply(self, message: Message, reply: Message, rpc_name: str) -> Any:
        """Decode the rpc reply, raising typed errors, correlation id currently not necessary but might be needed in the future."""
        if reply.correlation_id != message.correlation_id:
            raise RuntimeError(f"Correlation mismatch: sent {message.correlation_id}, got {reply.correlation_id}")
        method, body, is_error = decode_payload_envelope(reply.payload)
        if method != rpc_name:
            raise RuntimeError(f"RPC method mismatch: sent {rpc_name!r}, got {method!r}")
        if CODEC == "avro":
            if is_error:
                # Re-raise the remote error locally as its declared error class.
                raise error_from_datum(
                    decode_avro_datum(self._errors_schema(rpc_name, remote_name=message.destination), body)
                )
            return decode_avro_datum(self._response_schema(rpc_name, remote_name=message.destination), body)
        if is_error:
            raise error_from_datum(body)
        return body

    def _dispatch_self_call(self, message: Message) -> Message:
        """Serve a request addressed to this component without touching the broker."""
        reply_raw = self._dispatch(encode_message(message))
        return decode_message(reply_raw)

    def call_remote(self, destination: str, rpc_name: str, params: dict) -> Any:
        """Synchronous RPC through the broker returns the decoded response datum."""
        message = self.create_message(datum=params, request_name=rpc_name, destination=destination)
        self.log.info(
            "send",
            extra={
                "peer": destination,
                "rpc": rpc_name,
                "correlation_id": message.correlation_id,
                "sequence_id": message.sequence_id,
                "content": params,
            },
        )
        if destination == self.name:
            # Bypass the broker: enqueue from other threads, dispatch inline
            # from the worker itself (enqueue-and-wait there would self-deadlock).
            if threading.get_ident() == self._worker_ident:
                reply = self._dispatch_self_call(message)
            else:
                future: Future[Message] = Future()
                self._inbox.put(_QueuedRequest(encode_message(message), _ReplyChannel(writer=None, future=future)))
                reply = future.result()
        else:
            reply = self.send_message(message)
        return self._decode_reply(message, reply, rpc_name)

    def iterate(self, destination: str, init_rpc: str, params: dict[str, Any], *, max_items: int = 256) -> RemoteStream:
        """Issue an RPC and return a handle over the result: `.init` is the initiating reply
        (for streamed replies `value["columns"]` is the row type authority), iteration pulls
        the remaining pages, `close()` releases the whole upstream chain. For a one-shot reply
        the handle simply wraps it — iterate `init` yourself."""
        init = self.call_remote(destination, init_rpc, params)
        return RemoteStream(self, destination, init, max_items)

    def stream(self, destination: str, init_rpc: str, params: dict[str, Any], *, max_items: int = 256) -> Iterator[Any]:
        """Pull-based convenience: yields ITEMS one by one (rows for a streamed reply; the whole
        reply for a one-shot). Pages via stream_next; close runs on exhaustion and on early break."""
        remote = self.iterate(destination, init_rpc, params, max_items=max_items)
        if not remote.streamed:
            yield remote.init
            return
        yield from remote

    def close(self) -> None:
        """Close the connection to the broker."""
        self.broker_connection.close()

    def shutdown(self) -> None:
        """Stop the worker thread, drains items first."""
        self._inbox.put(None)
        self._worker.join(timeout=10)

    def start_unix_server(self, path: str) -> Socket:
        """Start the unix socket that listens to requests."""
        if os.path.exists(path):
            os.unlink(path)
        server = socket.socket(
            family=socket.AF_UNIX,
            type=socket.SOCK_STREAM,
        )
        server.bind(path)
        server.listen()
        return server

    def run(self) -> None:
        """Listener entry point."""
        while True:
            client_sock, address = self.sock.accept()
            thread = threading.Thread(target=self._handle_connection, args=(client_sock, address), daemon=True)
            thread.start()

    def _handle_connection(self, sock: socket.socket, address: str) -> None:
        """Connection handler, reads framed socket"""
        self.log.info("connect from %r", address)
        framed_socket = FramedSocket(sock, remote_name=f"unix:{address}")
        reader = avro.ipc.FramedReader(framed_socket)
        channel = _ReplyChannel(writer=avro.ipc.FramedWriter(framed_socket), framed_socket=framed_socket)

        try:
            while True:
                try:
                    raw = reader.read_framed_message()
                except Exception as exc:
                    self.log.debug("connection closed: %s", exc)
                    break
                if not raw:
                    break
                self._inbox.put(_QueuedRequest(raw, channel))
        finally:
            with channel.lock:
                channel.closed = True
            framed_socket.close()
            self.log.debug("disconnect")

    def _serve(self) -> None:
        """Worker loop: one worker thread for the moment that dispatches requests."""
        self._worker_ident = threading.get_ident()
        while True:
            item = self._inbox.get()
            try:
                if item is None:
                    return
                try:
                    reply_raw = self._dispatch(item.raw)
                except Exception as exc:
                    # _dispatch encodes handler errors itself; reaching here means
                    # the reply could not be built at all, so fail the channel.
                    self.log.exception("request failed: %s", exc)
                    self._fail(item.channel, exc)
                    continue
                try:
                    self._answer(item.channel, reply_raw)
                except Exception as exc:
                    self.log.exception("reply failed: %s", exc)
                    self._fail(item.channel, exc)
            except Exception as exc:  # last resort: never let the worker thread die
                self.log.exception("worker error: %s", exc)
            finally:
                self._inbox.task_done()

    def _answer(self, channel: _ReplyChannel, reply_raw: bytes) -> None:
        """Returns the response to the callee through the channel."""
        if channel.future is not None:
            if not channel.future.done():
                channel.future.set_result(decode_message(reply_raw))
            return
        assert channel.writer is not None
        with channel.lock:
            if channel.closed:
                return
            try:
                channel.writer.write_framed_message(message=reply_raw)
            except (ConnectionError, BrokenPipeError, OSError) as exc:
                self.log.warning("reply dropped, client gone: %s", exc)
                channel.closed = True

    def _fail(self, channel: _ReplyChannel, exc: Exception | None = None) -> None:
        """Dispatch or reply failed. For a waiting caller, set the exception on its future;
        otherwise close the socket so the blocked reader thread and the waiting client
        both observe EOF instead of hanging.
        """
        if channel.future is not None:
            if not channel.future.done():
                detail = f": {exc}" if exc is not None else ""
                channel.future.set_exception(RuntimeError(f"[{self.name}] request failed{detail}"))
            return
        with channel.lock:
            channel.closed = True
            if channel.framed_socket is not None:
                try:
                    channel.framed_socket.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    channel.framed_socket.close()
                except OSError:
                    pass

    def _dispatch(self, raw: bytes) -> bytes:
        """Decode Message, invoke local responder, return encoded reply."""
        message = decode_message(raw)
        method, body, _is_error = decode_payload_envelope(message.payload)
        if messages := self.protocol.messages:
            local_message = messages.get(method)
        else:
            raise ValueError("Error retrieving messages.")
        if local_message is None:
            raise ValueError(f"Unknown RPC {method!r} for {self.name}; known: {sorted(self.protocol.messages)}")
        if CODEC == "avro":
            request_datum = decode_avro_datum(local_message.request, body)
        else:
            request_datum = body
        self.log.info(
            "receive",
            extra={
                "peer": message.source,
                "rpc": method,
                "correlation_id": message.correlation_id,
                "sequence_id": message.sequence_id,
                "content": request_datum,
            },
        )
        try:
            if method == "stream_next" or method == "stream_close":
                serve = self._serve_stream_next if method == "stream_next" else self._serve_stream_close
                response_datum = serve(request_datum)
            else:
                response_datum = self.responder.invoke(local_message, request_datum)
        except Exception as exc:
            # Handler errors are encoded into the RPC's declared errors union and
            # travel as data inside the reply, so the broker stays opaque.
            self.log.exception("handler error: %s", exc)
            payload = encode_error_payload(method=method, exc=exc, errors_schema=local_message.errors, source=self.name)
        else:
            payload = encode_response_payload(method, response_datum, local_message.response)
        reply = Message(
            source=self.name,
            destination=message.source,
            correlation_id=message.correlation_id,
            # NOTE: replies are messages sent BY this component, so they get their own sequence id; correlation_id links the pair.
            sequence_id=self.next_sequence_id(),
            payload=payload,
        )
        return encode_message(reply)
