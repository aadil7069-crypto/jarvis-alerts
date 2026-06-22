"""
Phase 4 tests — paper trade lifecycle, exit condition logic, and daily performance stats.
No real API calls, DB connections, or wallet access.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from agents.execution_agent import ExecutionAgent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade(
    entry_price: float,
    direction: str = "buy",
    size_usd: float = 500.0,
    opened_hours_ago: float = 1.0,
    status: str = "open",
    high_price: float = None,
) -> MagicMock:
    trade = MagicMock()
    trade.id = 42
    trade.token_id = 1
    trade.entry_price = entry_price
    trade.direction = direction
    trade.size_usd = size_usd
    trade.status = status
    trade.is_paper = True
    trade.opened_at = datetime.now(timezone.utc) - timedelta(hours=opened_hours_ago)
    trade.high_price = high_price  # peak price seen since entry
    return trade


def _make_agent(stop_loss=-0.08, take_profit=0.25, max_hold_hours=48, trailing_stop=0.15) -> ExecutionAgent:
    cb = MagicMock()
    cb.trading_allowed = True
    bus = MagicMock()
    session_factory = MagicMock()
    config = {
        "system": {"mode": "paper"},
        "trading": {
            "stop_loss_pct": stop_loss,
            "take_profit_pct": take_profit,
            "trailing_stop_pct": trailing_stop,
            "max_hold_hours": max_hold_hours,
            "paper_balance": 10_000.0,
            "max_position_size_pct": 0.05,
        },
        "agents": {"execution": {"interval_seconds": 10}},
    }
    agent = ExecutionAgent.__new__(ExecutionAgent)
    agent.circuit_breaker = cb
    agent.config = config
    agent.name = "execution"
    agent.logger = MagicMock()
    agent.bus = bus
    agent._session_factory = session_factory
    agent._stop_loss_pct = stop_loss
    agent._take_profit_pct = take_profit
    agent._trailing_stop_pct = trailing_stop
    agent._max_hold_hours = max_hold_hours
    agent._starting_balance = 10_000.0
    agent._max_position_pct = 0.05
    return agent


# ── Exit conditions: stop-loss ────────────────────────────────────────────────

def test_exit_stop_loss_triggers():
    agent = _make_agent()
    trade = _make_trade(entry_price=1.00)
    reason = agent._check_exit_conditions(trade, current_price=0.91)
    assert reason == "stop_loss"


def test_exit_stop_loss_just_past_boundary():
    agent = _make_agent(stop_loss=-0.08)
    trade = _make_trade(entry_price=1.00)
    # One tick below -8%: -8.1%
    reason = agent._check_exit_conditions(trade, current_price=0.919)
    assert reason == "stop_loss"


def test_exit_stop_loss_not_triggered():
    agent = _make_agent()
    trade = _make_trade(entry_price=1.00)
    reason = agent._check_exit_conditions(trade, current_price=0.95)
    assert reason is None


# ── Exit conditions: take-profit ──────────────────────────────────────────────

def test_exit_take_profit_triggers():
    agent = _make_agent()
    trade = _make_trade(entry_price=1.00)
    reason = agent._check_exit_conditions(trade, current_price=1.30)
    assert reason == "take_profit"


def test_exit_take_profit_exact_boundary():
    agent = _make_agent(take_profit=0.25)
    trade = _make_trade(entry_price=1.00)
    reason = agent._check_exit_conditions(trade, current_price=1.25)
    assert reason == "take_profit"


def test_exit_take_profit_not_triggered():
    agent = _make_agent()
    trade = _make_trade(entry_price=1.00)
    reason = agent._check_exit_conditions(trade, current_price=1.10)
    assert reason is None


# ── Exit conditions: timeout ──────────────────────────────────────────────────

def test_exit_timeout_triggers():
    agent = _make_agent(max_hold_hours=24)
    trade = _make_trade(entry_price=1.00, opened_hours_ago=25.0)
    reason = agent._check_exit_conditions(trade, current_price=1.05)
    assert reason == "timeout"


def test_exit_timeout_not_triggered():
    agent = _make_agent(max_hold_hours=48)
    trade = _make_trade(entry_price=1.00, opened_hours_ago=10.0)
    reason = agent._check_exit_conditions(trade, current_price=1.05)
    assert reason is None


def test_exit_timeout_wins_over_no_price():
    """Timeout should still trigger even when current_price is unavailable."""
    agent = _make_agent(max_hold_hours=24)
    trade = _make_trade(entry_price=1.00, opened_hours_ago=50.0)
    reason = agent._check_exit_conditions(trade, current_price=None)
    assert reason == "timeout"


# ── Exit conditions: sell/short direction ──────────────────────────────────────

def test_exit_sell_direction_stop_loss():
    """For a sell position, price going UP triggers stop-loss."""
    agent = _make_agent()
    trade = _make_trade(entry_price=1.00, direction="sell")
    # Price rose 10% — short position loses 10% > 8% stop-loss
    reason = agent._check_exit_conditions(trade, current_price=1.10)
    assert reason == "stop_loss"


def test_exit_sell_direction_take_profit():
    """For a sell position, price going DOWN triggers take-profit."""
    agent = _make_agent()
    trade = _make_trade(entry_price=1.00, direction="sell")
    # Price fell 30% — short position gains 30% > 25% take-profit
    reason = agent._check_exit_conditions(trade, current_price=0.70)
    assert reason == "take_profit"


# ── No price available ────────────────────────────────────────────────────────

def test_exit_no_price_no_timeout_returns_none():
    agent = _make_agent()
    trade = _make_trade(entry_price=1.00, opened_hours_ago=1.0)
    reason = agent._check_exit_conditions(trade, current_price=None)
    assert reason is None


# ── P&L calculation ───────────────────────────────────────────────────────────

def test_pnl_calculation_profit():
    entry = 1.00
    exit_ = 1.25
    size = 500.0
    pnl_pct = (exit_ - entry) / entry
    pnl_usd = pnl_pct * size
    assert pnl_pct == pytest.approx(0.25)
    assert pnl_usd == pytest.approx(125.0)


def test_pnl_calculation_loss():
    entry = 1.00
    exit_ = 0.92
    size = 500.0
    pnl_pct = (exit_ - entry) / entry
    pnl_usd = pnl_pct * size
    assert pnl_pct == pytest.approx(-0.08)
    assert pnl_usd == pytest.approx(-40.0)


def test_pnl_calculation_breakeven():
    entry = 1.00
    exit_ = 1.00
    size = 500.0
    pnl_usd = (exit_ - entry) / entry * size
    assert pnl_usd == pytest.approx(0.0)


# ── Position sizing ───────────────────────────────────────────────────────────

def test_position_size_5pct_of_10k():
    """Default 5% of $10k = $500."""
    agent = _make_agent()
    agent._get_portfolio_value = lambda: 10_000.0
    idea = {"suggested_size_pct": 0.05}
    size = agent._compute_position_size(idea)
    assert size == pytest.approx(500.0)


def test_position_size_reduced_in_safe_mode():
    """2% (memecoin cap) of $10k = $200."""
    agent = _make_agent()
    agent._get_portfolio_value = lambda: 10_000.0
    idea = {"suggested_size_pct": 0.02}
    size = agent._compute_position_size(idea)
    assert size == pytest.approx(200.0)


# ── Reporting: daily stats calculation ───────────────────────────────────────

def test_daily_stats_win_rate():
    pnl_values = [100.0, -30.0, 50.0, -10.0, 80.0]
    wins = [p for p in pnl_values if p > 0]
    win_rate = len(wins) / len(pnl_values) * 100
    assert win_rate == pytest.approx(60.0)


def test_daily_stats_total_pnl():
    pnl_values = [100.0, -30.0, 50.0]
    total = sum(pnl_values)
    assert total == pytest.approx(120.0)


def test_daily_stats_best_worst():
    pnl_values = [100.0, -30.0, 50.0, -10.0]
    assert max(pnl_values) == 100.0
    assert min(pnl_values) == -30.0


# ── Trailing stop ─────────────────────────────────────────────────────────────

def test_trailing_stop_triggers_after_runup():
    """Token went 2x then dropped 17% from peak (past 15% trail) — trailing stop fires."""
    agent = _make_agent(trailing_stop=0.15)
    # entry=1.00, high=2.00, current=1.65 → dropped 17.5% from peak
    trade = _make_trade(entry_price=1.00, high_price=2.00)
    current = 1.65
    reason = agent._check_exit_conditions(trade, current_price=current)
    assert reason == "trailing_stop"


def test_trailing_stop_does_not_trigger_below_entry():
    """Trailing stop must not activate when position hasn't gone profitable yet.
    A flat/loss position should hit the fixed stop-loss, not the trailing stop."""
    agent = _make_agent(trailing_stop=0.15)
    # high_price == entry_price means the price never went above entry
    trade = _make_trade(entry_price=1.00, high_price=1.00)
    # Price dropped 10% — fixed stop-loss (-8%) should fire, not trailing stop
    reason = agent._check_exit_conditions(trade, current_price=0.90)
    assert reason == "stop_loss"


def test_trailing_stop_not_triggered_when_still_near_peak():
    """Only 4% below peak with 15% trailing stop — no exit, still riding."""
    agent = _make_agent(trailing_stop=0.15)
    trade = _make_trade(entry_price=1.00, high_price=2.00)
    current = 2.00 * 0.96   # 4% below peak, comfortably within trail
    reason = agent._check_exit_conditions(trade, current_price=current)
    assert reason is None


def test_trailing_stop_no_high_price_falls_back_to_entry():
    """When high_price is None, treat peak as entry — trailing stop not active yet."""
    agent = _make_agent(trailing_stop=0.15)
    trade = _make_trade(entry_price=1.00, high_price=None)
    # Slightly above entry — fixed take-profit not hit, trailing not active
    reason = agent._check_exit_conditions(trade, current_price=1.05)
    assert reason is None


def test_trailing_stop_fires_clearly_below_trail():
    """16% below peak with 15% trailing stop — trailing stop fires."""
    agent = _make_agent(trailing_stop=0.15)
    trade = _make_trade(entry_price=1.00, high_price=2.00)
    current = 2.00 * 0.84   # 16% below peak, past 15% trail
    reason = agent._check_exit_conditions(trade, current_price=current)
    assert reason == "trailing_stop"


def test_fixed_take_profit_fires_when_no_trailing_active():
    """Fixed take-profit still works when high_price == entry (no runup yet)."""
    agent = _make_agent(trailing_stop=0.15, take_profit=0.25)
    # high_price is at entry — no trailing stop active
    trade = _make_trade(entry_price=1.00, high_price=1.00)
    reason = agent._check_exit_conditions(trade, current_price=1.30)
    assert reason == "take_profit"


# ── Rug / vetting-failed emergency exit ───────────────────────────────────────

@pytest.mark.asyncio
async def test_emergency_exit_on_honeypot():
    """Hard rug failure (honeypot) triggers emergency close of open positions."""
    agent = _make_agent()
    agent._get_token_id = MagicMock(return_value=1)
    open_trade = _make_trade(entry_price=1.00)
    agent._get_open_trades_for_token = MagicMock(return_value=[open_trade])
    agent._close_trade = AsyncMock()

    payload = {
        "address": "TokenAddr123",
        "symbol": "SCAM",
        "passed": False,
        "fail_reasons": ["honeypot:goplus"],
    }
    await agent._emergency_exit_on_rug(payload)
    agent._close_trade.assert_awaited_once_with(open_trade, None, "rug_detected")


@pytest.mark.asyncio
async def test_emergency_exit_ignores_soft_failures():
    """Low liquidity (soft fail) does not trigger emergency close."""
    agent = _make_agent()
    agent._get_token_id = MagicMock(return_value=1)
    agent._get_open_trades_for_token = MagicMock(return_value=[_make_trade(1.00)])
    agent._close_trade = AsyncMock()

    payload = {
        "address": "TokenAddr123",
        "symbol": "LOWLIQ",
        "passed": False,
        "fail_reasons": ["low_liquidity:$20000"],
    }
    await agent._emergency_exit_on_rug(payload)
    agent._close_trade.assert_not_awaited()


@pytest.mark.asyncio
async def test_emergency_exit_skips_when_no_position_held():
    """Rug detected but we hold no position — no close attempted."""
    agent = _make_agent()
    agent._get_token_id = MagicMock(return_value=1)
    agent._get_open_trades_for_token = MagicMock(return_value=[])
    agent._close_trade = AsyncMock()

    payload = {
        "address": "TokenAddr123",
        "symbol": "SCAM",
        "passed": False,
        "fail_reasons": ["honeypot:goplus"],
    }
    await agent._emergency_exit_on_rug(payload)
    agent._close_trade.assert_not_awaited()
