"""Consumer-side handle over one streamed RPC: init reply, paged pulls, idempotent close.

One implementation serves every consumer seat (client queries, `Component.open_stream_relay`,
the exec-tree scan leaf). `close()` clears the id first, so racing finalizers/closers are safe.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .component import Component


class RemoteStream:
    """Handle over one streamed RPC reply. `init` is the initiating reply (the row type
    authority rides its `value["columns"]`); `pages()` pulls raw pages, iterating flattens
    rows; `close()` disposes the whole upstream chain (no-op after server-side exhaustion)."""

    def __init__(self, component: Component, destination: str, init: dict[str, Any], max_items: int):
        self.component = component
        self.destination = destination
        self.init = init
        self.max_items = max_items
        self._stream_id: str | None = init.get("stream_id")

    @property
    def streamed(self) -> bool:
        """False means the reply was one-shot and there is nothing to pull or close."""
        return self._stream_id is not None

    @property
    def stream_id(self) -> str | None:
        return self._stream_id

    @property
    def header(self) -> str | None:
        """Row-type authority (`value["columns"]`) from the init reply, if any."""
        return (self.init.get("value") or {}).get("columns")

    def pages(self) -> Iterator[list[Any]]:
        """Raw page pulls (one upstream stream_next per page); no implicit closing — pair with
        iteration or call close()."""
        if self._stream_id is None:
            return
        while True:
            page = self.component.call_remote(
                self.destination, "stream_next", {"stream_id": self._stream_id, "max_items": self.max_items}
            )
            yield page["rows"]
            if page["done"]:
                break

    def __iter__(self) -> Iterator[Any]:
        """Flatten pages into items; closes (exactly once) on exhaustion or early exit."""
        try:
            for page_rows in self.pages():
                yield from page_rows
        finally:
            self.close()

    def close(self) -> None:
        sid, self._stream_id = self._stream_id, None
        if sid is None:
            return
        try:
            self.component.call_remote(self.destination, "stream_close", {"stream_id": sid})
        except Exception:  # noqa: BLE001 — upstream already gone is the benign case
            self.component.log.debug("stream_close failed", extra={"stream_id": sid})

    def __enter__(self) -> RemoteStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
