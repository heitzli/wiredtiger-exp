import json
import socketserver
import threading
from pathlib import Path

from .logger import LOG_COLLECTOR_PORT

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "log.jsonl"


def render(entry: dict) -> str:
    line = f"{entry.get('timestamp', '')} {entry.get('level', ''):<7} {entry.get('component', '')}: {entry.get('message', '')}"
    for field in ("peer", "rpc", "correlation_id", "content"):
        value = entry.get(field)
        if value in (None, ""):
            continue
        text = value if isinstance(value, str) else json.dumps(value)
        line += f"  {field}={text[:100]}{'...' if len(text) > 100 else ''}"
    if entry.get("exception"):
        line += f"\n{entry['exception']}"
    return line


def connection_handler(server: "LogServer"):
    class EntryHandler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for raw in self.rfile:  # newline-delimited JSON entries
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    if server.print_entries:
                        print(f"[logserver] dropping undecodable line: {line[:100]}", flush=True)
                    return  # drop the connection; the producer re-connects
                if server.print_entries:
                    print(render(entry), flush=True)
                server.write_line(line)

    return EntryHandler


class LogServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = LOG_COLLECTOR_PORT,
        out_path: str | Path = LOG_FILE,
        print_entries: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.print_entries = print_entries
        self._out_lock = threading.Lock()
        self._serve_thread: threading.Thread | None = None
        self._out = None
        if out_path:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            self._out = open(out_path, "a", encoding="utf-8")

        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        self._server = _Server((host, port), connection_handler(self))
        self._server.daemon_threads = True
        if print_entries:
            print(f"[logserver] listening on {host}:{port}, writing {out_path}", flush=True)

    def write_line(self, line: str) -> None:
        with self._out_lock:
            if self._out is not None:
                self._out.write(line + "\n")
                self._out.flush()

    def serve_forever(self) -> None:
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="log-collector", daemon=True)
        self._serve_thread = thread
        thread.start()
        return thread

    def close(self) -> None:
        if self._serve_thread is not None and self._serve_thread is not threading.current_thread():
            self._server.shutdown()
        self._server.server_close()
        with self._out_lock:
            if self._out is not None:
                self._out.close()
                self._out = None
