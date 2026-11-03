import json
import logging
import os
import socket
import sys
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener
from queue import Queue

_FIELDS = ("peer", "rpc", "correlation_id", "content")

LOG_COLLECTOR_PORT = 64242
LOG_COLLECTOR_HOSTPORT = f"127.0.0.1:{LOG_COLLECTOR_PORT}"

log_queue = Queue(-1)
_logger = logging.getLogger("root")
_configured = False


def _json_default(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return repr(value)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
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
    def format(self, record: logging.LogRecord) -> str:
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
    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self._address = (host, port)
        self._sock: socket.socket | None = None
        self.setFormatter(JSONFormatter())

    def emit(self, record: logging.LogRecord) -> None:
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
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def configure() -> None:
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
    return _logger.getChild(name)


configure()
