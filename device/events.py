import heapq
from collections.abc import Callable
from itertools import count


class EventQueue:
    def __init__(self):
        self._now = 0
        self._sequence = count()
        self._events: list[tuple[int, int, Callable[[], object]]] = []

    def schedule(self, action: Callable[[], object], *, delay: int = 0) -> int:
        if type(delay) is not int or delay < 0:
            raise ValueError('event delay must be a nonnegative integer')
        sequence = next(self._sequence)
        heapq.heappush(self._events, (self._now + delay, sequence, action))
        return sequence

    def run_next(self) -> object:
        if not self._events:
            raise IndexError('no device event is queued')
        due, _, action = heapq.heappop(self._events)
        self._now = due
        return action()
