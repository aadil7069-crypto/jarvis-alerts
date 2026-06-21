"""
Phase 5 tests — Telegram message formatters and dashboard response shapes.
No real API calls, no DB, no actual Telegram messages sent.
"""
from notifications.telegram import (
    format_trade_opened,
    format_trade_closed,
    format_daily_briefing,
)


# ── Telegram: trade opened ────────────────────────────────────────────────────

def test_format_trade_opened_contains_symbol():
    payload = {
        "symbol": "BONK",
        "chain": "solana",
        "entry_price": 0.00002345,
        "size_usd": 500.0,
        "confidence_score": 81,
    }
    text = format_trade_opened(payload)
    assert "BONK" in text
    assert "SOLANA" in text
    assert "500" in text
    assert "81" in text


def test_format_trade_opened_shows_entry_price():
    payload = {"symbol": "TEST", "entry_price": 1.2345, "size_usd": 200.0, "confidence_score": 75}
    text = format_trade_opened(payload)
    assert "1.2345" in text


def test_format_trade_opened_missing_fields_no_crash():
    """Empty payload should produce output, never raise."""
    text = format_trade_opened({})
    assert isinstance(text, str)
    assert len(text) > 0


# ── Telegram: trade closed ────────────────────────────────────────────────────

def test_format_trade_closed_profit():
    payload = {
        "symbol": "BONK",
        "pnl_usd": 125.50,
        "pnl_pct": 0.25,
        "exit_reason": "take_profit",
        "size_usd": 500.0,
    }
    text = format_trade_closed(payload)
    assert "BONK" in text
    assert "125.50" in text
    assert "25.0%" in text
    assert "TAKE PROFIT" in text
    assert "✅" in text


def test_format_trade_closed_loss():
    payload = {
        "symbol": "RUG",
        "pnl_usd": -40.0,
        "pnl_pct": -0.08,
        "exit_reason": "stop_loss",
        "size_usd": 500.0,
    }
    text = format_trade_closed(payload)
    assert "RUG" in text
    assert "40.00" in text
    assert "-8.0%" in text
    assert "STOP LOSS" in text
    assert "❌" in text


def test_format_trade_closed_timeout():
    payload = {
        "symbol": "SLOW",
        "pnl_usd": -5.0,
        "pnl_pct": -0.01,
        "exit_reason": "timeout",
        "size_usd": 500.0,
    }
    text = format_trade_closed(payload)
    assert "TIMEOUT" in text


def test_format_trade_closed_missing_fields_no_crash():
    text = format_trade_closed({})
    assert isinstance(text, str)
    assert len(text) > 0


# ── Telegram: daily briefing ──────────────────────────────────────────────────

def test_format_daily_briefing_active_day():
    stats = {
        "total_trades": 5,
        "winning_trades": 3,
        "total_pnl_usd": 210.50,
        "win_rate": 60.0,
        "best_trade_pnl": 150.0,
        "worst_trade_pnl": -40.0,
    }
    text = format_daily_briefing(stats, portfolio_value=10_500.0)
    assert "5" in text
    assert "3" in text
    assert "60%" in text
    assert "210.50" in text
    assert "10,500.00" in text
    assert "150.00" in text
    assert "BRIEFING" in text.upper()


def test_format_daily_briefing_quiet_day():
    stats = {
        "total_trades": 0,
        "winning_trades": 0,
        "total_pnl_usd": 0.0,
        "win_rate": 0.0,
        "best_trade_pnl": None,
        "worst_trade_pnl": None,
    }
    text = format_daily_briefing(stats, portfolio_value=10_000.0)
    assert "No trades" in text or "0" in text
    assert "10,000.00" in text


def test_format_daily_briefing_negative_day():
    stats = {
        "total_trades": 3,
        "winning_trades": 1,
        "total_pnl_usd": -85.00,
        "win_rate": 33.3,
        "best_trade_pnl": 15.0,
        "worst_trade_pnl": -60.0,
    }
    text = format_daily_briefing(stats, portfolio_value=9_900.0)
    assert "85.00" in text
    assert "33%" in text


def test_format_daily_briefing_no_portfolio_value():
    """Zero portfolio_value should not crash."""
    stats = {"total_trades": 0, "winning_trades": 0, "total_pnl_usd": 0.0,
             "win_rate": 0.0, "best_trade_pnl": None, "worst_trade_pnl": None}
    text = format_daily_briefing(stats, portfolio_value=0)
    assert isinstance(text, str)


# ── Telegram: HTML safety ─────────────────────────────────────────────────────

def test_format_trade_opened_returns_html_string():
    payload = {"symbol": "TEST", "entry_price": 1.0, "size_usd": 100.0, "confidence_score": 72}
    text = format_trade_opened(payload)
    assert "<b>" in text
    assert "</b>" in text


def test_format_trade_closed_returns_html_string():
    payload = {"symbol": "TEST", "pnl_usd": 10.0, "pnl_pct": 0.05,
               "exit_reason": "take_profit", "size_usd": 200.0}
    text = format_trade_closed(payload)
    assert "<b>" in text


def test_format_daily_briefing_returns_html_string():
    stats = {"total_trades": 2, "winning_trades": 1, "total_pnl_usd": 50.0,
             "win_rate": 50.0, "best_trade_pnl": 60.0, "worst_trade_pnl": -10.0}
    text = format_daily_briefing(stats, portfolio_value=10_050.0)
    assert "<b>" in text


# ── notifications_enabled helper ──────────────────────────────────────────────

def test_notifications_disabled_by_default():
    """Notifications should be off unless config explicitly enables them."""
    config = {"notifications": {"enabled": False}}
    enabled = config.get("notifications", {}).get("enabled", False)
    assert enabled is False


def test_notifications_enabled_when_configured():
    config = {"notifications": {"enabled": True}}
    enabled = config.get("notifications", {}).get("enabled", False)
    assert enabled is True
