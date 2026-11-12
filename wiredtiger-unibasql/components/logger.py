"""Logging pipeline: JSON-over-TCP collection and/or console output via a queued handler.

Configure via env vars: LOG_TCP="[host:]port", LOG_CONSOLE=1, LOG_LEVEL=LEVEL
(default INFO). Configures logging at import time.
"""
import json
import logging
import os
import socket
import sys
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener
from queue import Queue

_FIELDS = ("peer", "rpc", "correlation_id", "sequence_id", "content")

LOG_COLLECTOR_PORT = 64242
LOG_COLLECTOR_HOSTPORT = f"127.0.0.1:{LOG_COLLECTOR_PORT}"

log_queue = Queue(-1)
_logger = logging.getLogger("root")
_configured = False


def _json_default(value):
    """Coerce bytes/bytearray lossily to str; everything else via repr."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return repr(value)


class JSONFormatter(logging.Formatter):
    """Emit each record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the standard fields plus non-empty extras and the exception, if any."""
        entry = {
            "timestamp": datetime.fromtimestamp(record.created).astimezone().isoformat(),
            "level": record.levelname,
            "component": record.name.removeprefix("root."),
            "message": record.getMessage(),
            "thread": record.threadName,
        }
        for field in _FIELDS:
            value = record.__dict__.get(field)
            if value not in (None, ""):
                entry[field] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=_json_default)


class ConsoleFormatter(logging.Formatter):
    """Compact human-readable one-line format with structured extras appended."""

    def format(self, record: logging.LogRecord) -> str:
        """Render standard fields plus non-empty extras (truncated to 100 chars)."""
        line = (
            f"{datetime.fromtimestamp(record.created):%H:%M:%S} "
            f"{record.levelname:<7} {record.name.removeprefix('root.')} [{record.threadName}] {record.getMessage()}"
        )
        for field in _FIELDS:
            value = record.__dict__.get(field)
            if value not in (None, ""):
                text = str(value)
                line += f"  {field}={text[:100]}{'...' if len(text) > 100 else ''}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class TcpHandler(logging.Handler):
    """Ships each record as a JSON line to a TCP collector; reconnects lazily and drops logs while it is down."""

    def __init__(self, host: str, port: int) -> None:
        """Store the address; the socket connects lazily on first emit."""
        super().__init__()
        self._address = (host, port)
        self._sock: socket.socket | None = None
        self.setFormatter(JSONFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        """Best-effort delivery: connect on demand, close and reset on network failure."""
        try:
            if self._sock is None:
                self._sock = socket.create_connection(self._address, timeout=1.0)
            self._sock.sendall((self.format(record) + "\n").encode("utf-8"))
        except OSError:
            sock, self._sock = self._sock, None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def env_flag(name: str) -> bool:
    """Truthiness of a boolean-style env var ("1"/"true"/"yes"/"on")."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def configure() -> None:
    """Install the queued TCP/console pipeline exactly once (env vars documented in the module docstring)."""
    global _configured
    if _configured:
        return
    _configured = True

    handlers = []
    if tcp := os.environ.get("LOG_TCP", LOG_COLLECTOR_HOSTPORT):
        host, _, port = tcp.partition(":")
        handlers.append(TcpHandler(host or "127.0.0.1", int(port)))
    if env_flag("LOG_CONSOLE"):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(ConsoleFormatter())
        handlers.append(console)
    _logger.addHandler(QueueHandler(log_queue))
    _logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    QueueListener(log_queue, *handlers).start()


def get_logger(name: str) -> logging.Logger:
    """Child of the shared root logger."""
    return _logger.getChild(name)


configure()
