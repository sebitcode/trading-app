from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .models import DEFAULT_ACCOUNT_ID

_current_account_id: ContextVar[str | None] = ContextVar(
    "current_account_id", default=None
)


def get_current_account_id() -> str | None:
    return _current_account_id.get()


@contextmanager
def account_scope(account_id: str = DEFAULT_ACCOUNT_ID) -> Iterator[None]:
    token = _current_account_id.set(account_id)
    try:
        yield
    finally:
        _current_account_id.reset(token)
