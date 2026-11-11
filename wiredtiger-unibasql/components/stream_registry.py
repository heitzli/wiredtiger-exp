from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Callable


def _dispose_iterator(iterator: Any) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 — disposing must never raise into the worker
            pass


@dataclass
class StreamHandle:
    id: str
    iterator: Iterator[Any]
    resource: Any = None
    closer: Callable[[], None] | None = None
    opened_at: float = field(default_factory=time.monotonic)
    disposed: bool = False

    def dispose(self) -> None:
        if self.disposed:
            return
        self.disposed = True
        _dispose_iterator(self.iterator)
        if self.closer is not None:
            try:
                self.closer()
            except Exception:
                pass


class StreamRegistry:
    def __init__(self) -> None:
        self._streams: dict[str, StreamHandle] = {}
        self._lock = threading.Lock()

    def open(self, iterator: Iterator[Any], resource: Any = None, closer: Callable[[], None] | None = None) -> str:
        stream_id = uuid.uuid4().hex
        with self._lock:
            self._streams[stream_id] = StreamHandle(id=stream_id, iterator=iterator, resource=resource, closer=closer)
        return stream_id

    def next(self, stream_id: str, max_items: int) -> tuple[list[Any], bool]:
        if max_items < 1:
            raise ValueError(f"max_items must be >= 1, got {max_items}")
        with self._lock:
            handle = self._streams.get(stream_id)
            if handle is None or handle.disposed:
                raise ValueError(f"Unknown stream {stream_id!r}")

            rows: list[Any] = []
            done = False
            for _ in range(max_items):
                try:
                    rows.append(next(handle.iterator))
                except StopIteration:
                    done = True
                    break

            if done:
                handle.dispose()
                del self._streams[stream_id]
        return rows, done

    def close(self, stream_id: str) -> bool:
        with self._lock:
            handle = self._streams.pop(stream_id, None)
        if handle is None:
            return False
        handle.dispose()
        return True

    def sweep(self, ttl_seconds: float) -> list[str]:
        swept: list[str] = []
        cutoff = time.monotonic() - ttl_seconds
        with self._lock:
            for stream_id, handle in list(self._streams.items()):
                if handle.opened_at <= cutoff:
                    handle.dispose()
                    del self._streams[stream_id]
                    swept.append(stream_id)
        return swept

    def __len__(self) -> int:
        with self._lock:
            return len(self._streams)
