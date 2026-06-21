import pytest
import asyncio
from core.config import load_config
from core.circuit_breaker import CircuitBreaker
from core.message_bus import MessageBus


def test_config_loads():
    config = load_config()
    assert "system" in config
    assert config["system"]["mode"] in ("paper", "live")


def test_circuit_breaker_starts_open():
    config = load_config()
    cb = CircuitBreaker(config)
    assert cb.trading_allowed is True
    assert cb._triggered is False


def test_circuit_breaker_triggers_on_loss():
    config = load_config()
    cb = CircuitBreaker(config)
    cb.check(daily_pnl_pct=-0.10)  # -10% loss; limit is -5%
    assert cb.trading_allowed is False
    assert cb._triggered is True


def test_circuit_breaker_resets():
    config = load_config()
    cb = CircuitBreaker(config)
    cb.trigger("test trigger")
    cb.reset("test")
    assert cb.trading_allowed is True


@pytest.mark.asyncio
async def test_message_bus_delivers():
    bus = MessageBus()
    received = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("test_agent", handler)
    await bus.publish("sender", "test_agent", "ping", {"value": 42})

    message = await bus._queue.get()
    await bus._deliver(message)

    assert len(received) == 1
    assert received[0]["payload"]["value"] == 42
