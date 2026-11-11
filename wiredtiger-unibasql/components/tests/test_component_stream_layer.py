"""Unit tests for the Component-base generic stream layer (no broker, no sockets).

The serving endpoints (_serve_stream_next/_serve_stream_close), relay machinery
(open_stream_relay: page pull, error morphing, close cascade) and the hook seams
are exercised against scripted call_remote fakes.
"""

import logging

import pytest

from components.component import Component
from components.errors import WiredTigerError


class StreamLayerHarness(Component):
    """Concrete stand-in so the abstract Component can be instantiated without __init__;
    tests attach registry/call_remote/log by hand."""

    def _create_responder(self, protocol):  # pragma: no cover — never constructed here
        raise NotImplementedError


class FakeUpstream:
    """Scripted peer: replays canned pages, records every call it receives."""

    def __init__(self, pages=None, error_on_call=None):
        self.pages = list(pages or [])
        self.calls: list[tuple[str, dict]] = []
        self.error_on_call = error_on_call  # (rpc_name, occurrence#) -> raises once

    def __call__(self, destination: str, rpc_name: str, params: dict):
        self.calls.append((rpc_name, params))
        if self.error_on_call and (rpc_name, self.calls.count((rpc_name, params))) == self.error_on_call:
            raise RuntimeError("connection reset by remote")
        return self.pages.pop(0) if self.pages else {"rows": [], "done": True}


def _dummy(cls=StreamLayerHarness, pages=None, upstream_err=None) -> tuple[Component, FakeUpstream]:
    """Component instance without __init__ plumbing; only stream-layer collaborators attached."""
    comp = object.__new__(cls)
    comp.log = logging.getLogger("test-stream-layer")
    comp.log.setLevel(logging.CRITICAL)
    fake = FakeUpstream(pages=pages, error_on_call=upstream_err)
    comp.call_remote = fake
    return comp, fake


def _attach(comp, stream_page_cap=4096, stream_relay_page=256, stream_ttl_seconds=60.0):
    from components.stream_registry import StreamRegistry

    comp.registry = StreamRegistry()
    comp.stream_page_cap = stream_page_cap
    comp.stream_relay_page = stream_relay_page
    comp.stream_ttl_seconds = stream_ttl_seconds
    comp.gone: list[str] = []
    comp._on_stream_gone = comp.gone.append  # record hook invocations
    return comp


# --- serving endpoints --------------------------------------------------------------------------------


def test_stream_next_buckets_pages_until_exhaustion():
    comp, _fake = _dummy()
    _attach(comp)
    it = iter("abcdef")
    sid = comp.register_stream(it)
    page = comp._serve_stream_next({"stream_id": sid, "max_items": 4})
    assert (page["rows"], page["done"]) == (["a", "b", "c", "d"], False)
    page = comp._serve_stream_next({"stream_id": sid, "max_items": 100})
    assert (page["rows"], page["done"]) == (["e", "f"], True)
    assert comp.gone == [sid]  # exhaustion fires the ownership hook exactly once


def test_stream_next_caps_requests_at_the_server_page_cap():
    comp, _fake = _dummy()
    _attach(comp, stream_page_cap=2)
    sid = comp.register_stream(iter(range(10)))
    page = comp._serve_stream_next({"stream_id": sid, "max_items": 10})
    assert (page["rows"], page["done"]) == ([0, 1], False)  # capped from 10 to 2
    comp._serve_stream_close({"stream_id": sid})


def test_stream_next_unknown_id_is_typed():
    comp, _fake = _dummy()
    _attach(comp)
    with pytest.raises(WiredTigerError, match="Unknown stream"):
        comp._serve_stream_next({"stream_id": "ghost", "max_items": 1})


def test_stream_close_reports_was_open_and_fires_hook_once():
    comp, _fake = _dummy()
    _attach(comp)
    sid = comp.register_stream(iter(()))
    assert comp._serve_stream_close({"stream_id": sid}) is True
    assert comp.gone == [sid]
    assert comp._serve_stream_close({"stream_id": sid}) is False
    assert comp.gone == [sid]


def test_expired_streams_are_swept_with_their_hook_at_next_stream_rpc():
    comp, _fake = _dummy()
    _attach(comp)
    aged, alive = comp.register_stream(iter("xy")), comp.register_stream(iter("ab"))
    comp.registry._streams[aged].opened_at -= 999
    comp._serve_stream_next({"stream_id": alive, "max_items": 1})  # any stream RPC sweeps first
    assert comp.gone == [aged]
    assert comp.registry.next(alive, max_items=1)[0] == ["b"]  # first row already pulled by the sweeping RPC


# --- relay machinery ----------------------------------------------------------------------------------


def test_open_stream_relay_pulls_upstream_pages_and_closes_upstream_when_done():
    comp, fake = _dummy(pages=[{"rows": [1, 2], "done": False}, {"rows": [3], "done": True}])
    _attach(comp, stream_relay_page=4)
    sid = comp.open_stream_relay("storagemanager", {"value": {"columns": "h"}, "stream_id": "up-1"})

    pulled: list = []
    while True:
        page = comp._serve_stream_next({"stream_id": sid, "max_items": 2})
        pulled += page["rows"]
        if page["done"]:
            break
    assert pulled == [1, 2, 3]
    upstream_calls = [(rpc, params["stream_id"]) for rpc, params in fake.calls]
    assert ("stream_next", "up-1") in upstream_calls  # paged against the UPSTREAM id
    assert ("stream_close", "up-1") in upstream_calls  # drained upstream -> cascade close
    assert ("stream_close", "up-1") == upstream_calls[-1]


def test_relay_close_cascades_even_before_drain():
    comp, fake = _dummy(pages=[{"rows": [1, 2, 3], "done": False}])
    _attach(comp, stream_relay_page=4)
    sid = comp.open_stream_relay("storagemanager", {"value": {}, "stream_id": "up-9"})
    comp._serve_stream_next({"stream_id": sid, "max_items": 1})  # consumer stops early
    assert comp._serve_stream_close({"stream_id": sid}) is True
    assert fake.calls[-1] == ("stream_close", {"stream_id": "up-9"})  # generator finally closes upstream


def test_relay_upstream_mid_stream_failure_is_morphed_but_still_closes():
    pages = [{"rows": [1], "done": False}, {"rows": [2], "done": True}]
    comp, fake = _dummy(pages=pages, upstream_err=("stream_next", 2))
    _attach(comp, stream_relay_page=1)
    sid = comp.open_stream_relay("storagemanager", {"value": {}, "stream_id": "up-x"})
    page = comp._serve_stream_next({"stream_id": sid, "max_items": 1})
    assert (page["rows"], page["done"]) == ([1], False)
    with pytest.raises(WiredTigerError, match="Upstream storagemanager stream failed"):
        comp._serve_stream_next({"stream_id": sid, "max_items": 1})
    # the generator's finally releases the upstream even on failure paths
    assert ("stream_close", {"stream_id": "up-x"}) in fake.calls


def test_discard_stream_forces_disposal_through_the_hook():
    comp, _fake = _dummy()
    _attach(comp)
    it_consumed = []
    sid = comp.register_stream((it_consumed.append(v) or v for v in "ab"))  # lazy gen
    assert comp.discard_stream(sid) is True
    assert comp.gone == [sid]
    assert comp.discard_stream(sid) is False  # already gone
