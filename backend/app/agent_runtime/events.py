"""Bounded device-scoped fanout; a slow browser never blocks agent workers."""
import asyncio
from dataclasses import dataclass, field
from typing import Any
from .config import Identity


@dataclass(eq=False)
class Subscriber:
    identity: Identity
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(64))


class PanelEvents:
    def __init__(self) -> None:
        self.subscribers: set[Subscriber] = set()

    def subscribe(self, identity: Identity) -> Subscriber:
        if len(self.subscribers) >= 128 or sum(item.identity == identity for item in self.subscribers) >= 8:
            raise ValueError("Panel connection capacity reached")
        item = Subscriber(identity)
        self.subscribers.add(item)
        return item

    def send(self, item: Subscriber, message: dict[str, Any]) -> None:
        try:
            item.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.subscribers.discard(item)
            while not item.queue.empty():
                item.queue.get_nowait()
            item.queue.put_nowait({"type": "disconnect"})

    def publish(self, identity: Identity, message: dict[str, Any]) -> None:
        for item in self.subscribers.copy():
            if item.identity == identity:
                self.send(item, message)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self.subscribers.discard(subscriber)
