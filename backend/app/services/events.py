import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class TaskEvent(dict[str, object]):
    def __init__(self, payload: dict[str, object], event_id: str) -> None:
        super().__init__(payload)
        self.event_id = event_id


class TaskEventBroker:
    def __init__(self, queue_size: int = 100) -> None:
        self.queue_size = queue_size
        self._subscribers: dict[
            str,
            set[asyncio.Queue[TaskEvent]],
        ] = defaultdict(set)
        self._history: dict[str, deque[TaskEvent]] = {}
        self._sequences: dict[str, int] = defaultdict(int)

    async def publish(self, task_id: str, event: dict[str, object]) -> None:
        self._sequences[task_id] += 1
        event_id = str(self._sequences[task_id])
        history = self._history.setdefault(task_id, deque(maxlen=self.queue_size))
        history.append(TaskEvent(event, event_id))
        for queue in tuple(self._subscribers[task_id]):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(TaskEvent(event, event_id))

    @asynccontextmanager
    async def subscribe(
        self,
        task_id: str,
        after_id: str | None = None,
    ) -> AsyncIterator[asyncio.Queue[TaskEvent]]:
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue(self.queue_size)
        try:
            last_seen = int(after_id or "0")
        except ValueError:
            last_seen = 0
        for event in self._history.get(task_id, ()):
            if int(event.event_id) > last_seen:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(TaskEvent(event, event.event_id))
        self._subscribers[task_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[task_id].discard(queue)
            if not self._subscribers[task_id]:
                self._subscribers.pop(task_id, None)
