import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from typing import Optional


class BaseAgent(ABC):
    def __init__(self, name: str, message_bus, session_factory, config: dict):
        self.name = name
        self.bus = message_bus
        self._session_factory = session_factory
        self.config = config
        self.logger = logging.getLogger(f"jarvis.{name}")
        self.running = False
        self.last_run: Optional[datetime] = None
        self._interval: int = (
            config.get("agents", {}).get(name, {}).get("interval_seconds", 60)
        )

    @contextmanager
    def get_db(self):
        """
        Context manager that provides an isolated database session.
        Always use this instead of a shared session — concurrent agents
        sharing one SQLAlchemy session cause lock errors on SQLite.

        Usage:
            with self.get_db() as db:
                db.query(...)
                db.commit()
        """
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def start(self) -> None:
        self.running = True
        self.logger.info(f"Agent started (interval: {self._interval}s)")
        await self.bus.subscribe(self.name, self.process_message)
        await self._run_loop()

    async def stop(self) -> None:
        self.running = False
        self.logger.info("Agent stopped")

    async def _run_loop(self) -> None:
        while self.running:
            try:
                await self.run()
                self.last_run = datetime.utcnow()
            except Exception as e:
                self.logger.error(f"Unhandled error in run(): {e}", exc_info=True)
            await asyncio.sleep(self._interval)

    @abstractmethod
    async def run(self) -> None:
        """Main agent logic — called on every interval tick."""

    @abstractmethod
    async def process_message(self, message: dict) -> None:
        """Handle a message delivered by the message bus."""

    async def publish(self, message_type: str, payload: dict, to: str = "all") -> None:
        await self.bus.publish(
            from_agent=self.name,
            to_agent=to,
            message_type=message_type,
            payload=payload,
        )
