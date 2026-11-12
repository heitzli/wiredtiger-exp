from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import wiredtiger
from wiredtiger import wiredtiger_strerror

from ..errors import WiredTigerError


def check(err: int, *, what: str = "", source: str | None = None) -> None:
    """Raise :class:`WiredTigerError` if ``err`` is a non-zero error code."""
    if err:
        msg = wiredtiger_strerror(err)  # type: ignore
        raise WiredTigerError(message=f"{what}: {msg}" if what else msg, source=source)


@contextmanager
def wt_transaction(session: wiredtiger.Session, *, source: str | None = None) -> Iterator[wiredtiger.Session]:
    """Run a block inside a WiredTiger transaction: begins it, commits on normal exit,
    rolls back on any exception (including generator abandonment via ``GeneratorExit``)."""
    check(session.begin_transaction(), what="begin_transaction", source=source)
    try:
        yield session
    except BaseException:
        check(session.rollback_transaction(), what="rollback_transaction", source=source)
        raise
    check(session.commit_transaction(), what="commit_transaction", source=source)
