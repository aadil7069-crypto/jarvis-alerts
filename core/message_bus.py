import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List

logger = logging.getLogger("jarvis.message_bus")


class MessageBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._queue: asyncio.Queue = asyncio.Queue()

    async def subscribe(self, agent_name: str, callback: Callable) -> None:
        if agent_name not in self._subscribers:
            self._subscribers[agent_name] = []
        self._subscribers[agent_name].append(callback)

    async def publish(self, from_agent: str, to_agent: str, message_type: str, payload: dict) -> None:
        message = {
            "from": from_agent,
            "to": to_agent,
            "type": message_type,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._queue.put(message)
        logger.debug(f"[BUS] {from_agent} -> {to_agent} | {message_type}")

    async def _deliver(self, message: dict) -> None:
        target = message["to"]

        if target == "all":
            recipients = [
                cb
                for name, cbs in self._subscribers.items()
                if name != message["from"]
                for cb in cbs
            ]
        elif target in self._subscribers:
            recipients = self._subscribers[target]
        else:
            recipients = []

        for callback in recipients:
            try:
                await callback(message)
            except Exception as e:
                logger.error(f"Error delivering message to {target}: {e}")

    async def run_forever(self) -> None:
        while True:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                await self._deliver(message)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.error(f"Message bus error: {e}")
