"""Cooperative cancellation shared by file and analysis workers."""

from threading import Event


class OperationCancelled(Exception):
    """A requested operation was cancelled without changing its destination."""


def check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise OperationCancelled()
