"""Cooperative cancellation for staffer route searches.

A route job runs inside cancel_scope(event); search loops call check_cancelled()
every few expansions. Outside a scope check_cancelled() is a no-op, so direct
callers and tests behave exactly as before.
"""
import threading

_state = threading.local()


class RouteCancelled(Exception):
    """Raised inside a search when its job was cancelled."""


class cancel_scope:
    def __init__(self, event):
        self.event = event

    def __enter__(self):
        self.previous = getattr(_state, 'event', None)
        _state.event = self.event
        return self.event

    def __exit__(self, *exc):
        _state.event = self.previous
        return False


def check_cancelled():
    event = getattr(_state, 'event', None)
    if event is not None and event.is_set():
        raise RouteCancelled()
