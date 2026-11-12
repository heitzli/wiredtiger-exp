from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Callable


def _dispose_iterator(iterator: Any) -> None:
    """Silently close a generator (if it supports close()); errors swallowed."""
    close = getattr(iterator, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


@dataclass
class StreamHandle:
    """One live stream plus its teardown parts; `dispose()` is exactly-once and never raises."""

    id: str
    iterator: Iterator[Any]
    resource: Any = None
    closer: Callable[[], None] | None = None
    opened_at: float = field(default_factory=time.monotonic)
    disposed: bool = False

    def dispose(self) -> None:
        """Tear down iterator and closer, guarded by the disposed flag."""
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
    """Thread-safe id -> stream map enforcing exactly-once dispose: whichever path
    (exhaustion, close, sweep) finishes a handle first removes and disposes it;
    later operations simply see it as gone.
    """

    def __init__(self) -> None:
        """Start with an empty, unlocked registry."""
        self._streams: dict[str, StreamHandle] = {}
        self._lock = threading.Lock()

    def open(self, iterator: Iterator[Any], resource: Any = None, closer: Callable[[], None] | None = None) -> str:
        """Register an iterator under a fresh uuid; returns its id."""
        stream_id = uuid.uuid4().hex
        with self._lock:
            self._streams[stream_id] = StreamHandle(id=stream_id, iterator=iterator, resource=resource, closer=closer)
        return stream_id

    def next(self, stream_id: str, max_items: int) -> tuple[list[Any], bool]:
        """Pull up to max_items items; auto-disposes on exhaustion. Unknown ids raise ValueError."""
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
        """Explicit dispose; True only if a live stream existed."""
        with self._lock:
            handle = self._streams.pop(stream_id, None)
        if handle is None:
            return False
        handle.dispose()
        return True

    def sweep(self, ttl_seconds: float) -> list[str]:
        """Idle-timeout GC: dispose handles older than ttl_seconds and return their ids."""
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
        """Number of live streams."""
        with self._lock:
            return len(self._streams)
