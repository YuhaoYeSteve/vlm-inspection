import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class TaskEventBroker:
    def __init__(self, queue_size: int = 100) -> None:
        self.queue_size = queue_size
        self._subscribers: dict[
            str,
            set[asyncio.Queue[dict[str, object]]],
        ] = defaultdict(set)

    async def publish(self, task_id: str, event: dict[str, object]) -> None:
        for queue in tuple(self._subscribers[task_id]):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event.copy())

    @asynccontextmanager
    async def subscribe(
        self,
        task_id: str,
    ) -> AsyncIterator[asyncio.Queue[dict[str, object]]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(self.queue_size)
        self._subscribers[task_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[task_id].discard(queue)
            if not self._subscribers[task_id]:
                self._subscribers.pop(task_id, None)
