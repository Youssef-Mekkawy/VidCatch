"""Thread-safe event hub: worker threads publish, the browser receives them over Server-Sent Events.

Workers (download threads) are plain threads, the web server is asyncio. `publish` is safe to call
from any thread; each subscriber owns an asyncio queue that is filled with `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import threading
import time


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self.last_activity = time.monotonic()  # last time a browser connected or disconnected
        self.ever_connected = False

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue:
        """Call from inside the event loop. Returns the queue to read events from."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append((asyncio.get_running_loop(), queue))
            self.ever_connected = True
            self.last_activity = time.monotonic()
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s[1] is not queue]
            self.last_activity = time.monotonic()

    def publish(self, event: dict) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(self._put, queue, event)
            except RuntimeError:  # loop already closed during shutdown
                pass

    @staticmethod
    def _put(queue: asyncio.Queue, event: dict) -> None:
        if queue.full():  # a stuck browser must never block or grow without bound
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(event)
