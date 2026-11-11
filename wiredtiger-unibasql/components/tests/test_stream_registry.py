"""Unit tests for the StreamRegistry disposal contract (no sockets, no components involved).

Whatever happens — exhaustion, early close, sweep, force-disposal — the iterator's close()
and the resource closer run EXACTLY ONCE.
"""

import pytest

from components.stream_registry import StreamRegistry


class CountingIter:
    """Synthetic row source recording how often it was closed."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.position = 0
        self.closed = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.position >= len(self.rows):
            raise StopIteration
        row = self.rows[self.position]
        self.position += 1
        return row

    def close(self):
        self.closed += 1


def _fresh(rows=("a", "b", "c")) -> tuple[StreamRegistry, CountingIter, str]:
    reg = StreamRegistry()
    it = CountingIter(rows)
    return reg, it, reg.open(it, resource="res")


def test_pages_are_bucketed_by_max_items_and_order_preserved():
    reg, _it, sid = _fresh(list(range(10)))
    rows, done = reg.next(sid, max_items=4)
    assert rows == [0, 1, 2, 3] and done is False
    rows, done = reg.next(sid, max_items=100)
    assert rows == [4, 5, 6, 7, 8, 9] and done is True


def test_exhaustion_auto_disposes_exactly_once():
    reg, it, sid = _fresh()
    _rows, done = reg.next(sid, max_items=10)
    assert done is True
    assert it.closed == 1
    # gone from the bookkeeping: pulling again raises
    with pytest.raises(ValueError, match="Unknown stream"):
        reg.next(sid, max_items=1)


def test_partial_exhaustion_on_exact_fit_then_empty_tail():
    reg, it, sid = _fresh()
    rows, done = reg.next(sid, max_items=3)
    assert (rows, done) == (["a", "b", "c"], False)
    rows, done = reg.next(sid, max_items=3)
    assert (rows, done) == ([], True)
    assert it.closed == 1


def test_early_close_is_idempotent():
    reg, it, sid = _fresh()
    assert reg.close(sid) is True
    assert it.closed == 1
    assert reg.close(sid) is False
    assert it.closed == 1  # no second dispose


def test_unknown_stream_and_bad_paging_args():
    reg, _it, sid = _fresh(("x",))
    with pytest.raises(ValueError):
        reg.next("nope", max_items=1)
    with pytest.raises(ValueError):
        reg.next(sid, max_items=0)
    # still consumable after the failed call
    assert reg.next(sid, max_items=1) == (["x"], False)


def test_sweep_expires_only_idle_streams_and_calls_hook_sources():
    reg = StreamRegistry()
    fresh_it, stale_it = CountingIter(["a"]), CountingIter(["b"])
    reg.open(fresh_it)
    stale_sid = reg.open(stale_it)
    reg._streams[stale_sid].opened_at -= 999  # simulate age past the TTL
    swept = reg.sweep(ttl_seconds=60.0)
    assert swept == [stale_sid]
    assert stale_it.closed == 1 and len(reg) == 1
    # a second sweep does not resurrect anything
    assert reg.sweep(ttl_seconds=60.0) == []


def test_resource_closer_runs_once_on_every_disposal_path():
    reg = StreamRegistry()
    released = []
    it = CountingIter(["a"])
    sid = reg.open(it, resource="conn", closer=lambda: released.append(1))
    reg.close(sid)
    reg.close(sid)  # idempotent
    reg.sweep(60.0)  # already gone, must not double-release
    assert released == [1] and it.closed == 1
