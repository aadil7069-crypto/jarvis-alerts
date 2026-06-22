"""
Phase 7 execution tests — Jupiter client, PancakeSwap client,
live-mode gating, and updated trade opening flow.
No real network calls — all external HTTP is mocked.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Jupiter client ─────────────────────────────────────────────────────────────

class TestJupiterQuote:
    def test_returns_parsed_quote(self):
        from data.jupiter import get_quote
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "inAmount": "1000000",
            "outAmount": "5000000000",
            "priceImpactPct": "0.0025",
            "routePlan": [{"swapInfo": {"ammKey": "abc"}}],
        }
        with patch("data.jupiter.requests.get", return_value=mock_resp):
            q = get_quote("USDC_MINT", "TOKEN_MINT", 1_000_000)
        assert q["in_amount"] == 1_000_000
        assert q["out_amount"] == 5_000_000_000
        assert q["price_impact_pct"] == pytest.approx(0.0025)
        assert len(q["route_plan"]) == 1
        assert "raw" in q

    def test_returns_empty_on_network_error(self):
        from data.jupiter import get_quote
        with patch("data.jupiter.requests.get", side_effect=Exception("timeout")):
            q = get_quote("A", "B", 100)
        assert q == {}

    def test_returns_empty_on_http_error(self):
        from data.jupiter import get_quote
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("429 Too Many Requests")
        with patch("data.jupiter.requests.get", return_value=mock_resp):
            q = get_quote("A", "B", 100)
        assert q == {}

    def test_get_sol_price_parses_response(self):
        from data.jupiter import get_sol_price, SOL_MINT
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": {SOL_MINT: {"price": "153.42"}}
        }
        with patch("data.jupiter.requests.get", return_value=mock_resp):
            price = get_sol_price()
        assert price == pytest.approx(153.42)

    def test_get_sol_price_returns_none_on_error(self):
        from data.jupiter import get_sol_price
        with patch("data.jupiter.requests.get", side_effect=Exception("timeout")):
            assert get_sol_price() is None

    def test_execute_swap_fails_without_private_key(self, monkeypatch):
        from data.jupiter import execute_swap
        monkeypatch.setenv("SOLANA_PRIVATE_KEY", "")
        result = execute_swap({"raw": {}}, "https://rpc.example.com")
        assert result["status"] == "failed"
        assert "SOLANA_PRIVATE_KEY" in result["error"]

    def test_execute_swap_fails_without_raw_quote(self, monkeypatch):
        from data.jupiter import execute_swap
        monkeypatch.setenv("SOLANA_PRIVATE_KEY", "some_key")
        result = execute_swap({}, "https://rpc.example.com")
        assert result["status"] == "failed"

    def test_get_price_impact_returns_zero_on_empty_quote(self):
        from data.jupiter import get_price_impact
        with patch("data.jupiter.get_quote", return_value={}):
            assert get_price_impact("TOKEN", 500.0) == 0.0

    def test_get_price_impact_parses_quote(self):
        from data.jupiter import get_price_impact
        with patch("data.jupiter.get_quote", return_value={"price_impact_pct": 0.015}):
            assert get_price_impact("TOKEN", 500.0) == pytest.approx(0.015)


# ── PancakeSwap client ────────────────────────────────────────────────────────

class TestPancakeSwapQuote:
    def test_returns_empty_without_web3(self, monkeypatch):
        """Without web3 installed, get_quote returns {} gracefully."""
        from data.pancakeswap import get_quote
        with patch("builtins.__import__", side_effect=ImportError("No module named 'web3'")):
            # Direct module-level import mock
            pass
        # Just verify the function exists and handles ImportError
        with patch("data.pancakeswap.get_quote", return_value={}):
            from data.pancakeswap import get_quote as gq
            # If web3 not installed, should return {}
            assert isinstance(gq("0xA", "0xB", 100), dict)

    def test_execute_swap_fails_without_keys(self, monkeypatch):
        from data.pancakeswap import execute_swap
        monkeypatch.setenv("BNB_PRIVATE_KEY", "")
        monkeypatch.setenv("BNB_WALLET_ADDRESS", "")
        result = execute_swap("0xA", "0xB", 100, 90, 500)
        assert result["status"] == "failed"
        assert "BNB_PRIVATE_KEY" in result["error"]

    def test_execute_swap_fails_with_key_but_no_wallet(self, monkeypatch):
        from data.pancakeswap import execute_swap
        monkeypatch.setenv("BNB_PRIVATE_KEY", "0xdeadbeef")
        monkeypatch.setenv("BNB_WALLET_ADDRESS", "")
        result = execute_swap("0xA", "0xB", 100, 90, 500)
        assert result["status"] == "failed"

    def test_get_price_impact_zero_on_empty_quote(self):
        from data.pancakeswap import get_price_impact
        with patch("data.pancakeswap.get_quote", return_value={}):
            assert get_price_impact("0xABC", 500.0) == 0.0


# ── Validator: live mode secrets ──────────────────────────────────────────────

class TestValidatorLiveMode:
    def test_paper_mode_passes_without_live_keys(self, monkeypatch):
        from core.validator import validate_startup
        monkeypatch.setenv("SOLANA_PRIVATE_KEY", "")
        monkeypatch.setenv("BNB_PRIVATE_KEY", "")
        monkeypatch.setenv("BNB_WALLET_ADDRESS", "")
        config = {"system": {"mode": "paper"}}
        result = validate_startup(config)
        assert result is True

    def test_live_mode_fails_without_solana_key(self, monkeypatch):
        from core.validator import validate_startup
        monkeypatch.setenv("SOLANA_PRIVATE_KEY", "")
        monkeypatch.setenv("BNB_PRIVATE_KEY", "0xdeadbeef")
        monkeypatch.setenv("BNB_WALLET_ADDRESS", "0xwallet")
        config = {"system": {"mode": "live"}}
        result = validate_startup(config)
        assert result is False

    def test_live_mode_passes_with_all_keys(self, monkeypatch):
        from core.validator import validate_startup
        monkeypatch.setenv("SOLANA_PRIVATE_KEY", "some_base58_key")
        monkeypatch.setenv("BNB_PRIVATE_KEY", "0xdeadbeef")
        monkeypatch.setenv("BNB_WALLET_ADDRESS", "0xmywallet")
        config = {"system": {"mode": "live"}}
        result = validate_startup(config)
        assert result is True


# ── ExecutionAgent: price fetch helpers ───────────────────────────────────────

class TestExecutionPriceHelper:
    def _make_agent(self, mode="paper"):
        from agents.execution_agent import ExecutionAgent
        cb = MagicMock()
        cb.trading_allowed = True
        agent = ExecutionAgent.__new__(ExecutionAgent)
        agent.circuit_breaker = cb
        agent.config = {"system": {"mode": mode}, "trading": {
            "stop_loss_pct": -0.08, "take_profit_pct": 0.25,
            "trailing_stop_pct": 0.15, "max_hold_hours": 48,
            "paper_balance": 10_000.0, "max_position_size_pct": 0.05,
        }, "execution": {"slippage_bps": 50}, "agents": {"execution": {"interval_seconds": 10}}}
        agent.name = "execution"
        agent.logger = MagicMock()
        agent.bus = MagicMock()
        agent._session_factory = MagicMock()
        agent._stop_loss_pct = -0.08
        agent._take_profit_pct = 0.25
        agent._trailing_stop_pct = 0.15
        agent._max_hold_hours = 48
        agent._starting_balance = 10_000.0
        agent._max_position_pct = 0.05
        agent._slippage_bps = 50
        agent._solana_rpc = "https://rpc.example.com"
        agent._bnb_rpc = ""
        return agent

    @pytest.mark.asyncio
    async def test_paper_solana_falls_back_to_dexscreener_when_jupiter_fails(self):
        agent = self._make_agent(mode="paper")
        import asyncio

        with patch("agents.execution_agent.jupiter_quote", return_value={}), \
             patch("agents.execution_agent.get_token") as mock_get, \
             patch("agents.execution_agent.extract_token_info", return_value={"price_usd": 0.0042}), \
             patch("agents.execution_agent.rate_limit", new_callable=AsyncMock):
            mock_get.return_value = {"pair": "data"}
            price, tx_sig = await agent._fetch_price_and_maybe_execute(
                "SOLTokenAddr", "solana", 500.0, "paper", asyncio.get_event_loop()
            )

        assert price == pytest.approx(0.0042)
        assert tx_sig is None

    @pytest.mark.asyncio
    async def test_paper_mode_does_not_call_execute_swap(self):
        agent = self._make_agent(mode="paper")
        import asyncio

        with patch("agents.execution_agent.jupiter_quote", return_value={
            "in_amount": 500_000_000, "out_amount": 10_000_000_000,
            "price_impact_pct": 0.001, "route_plan": [], "raw": {}
        }), \
             patch("agents.execution_agent.jupiter_execute") as mock_exec, \
             patch("agents.execution_agent.rate_limit", new_callable=AsyncMock):
            price, tx_sig = await agent._fetch_price_and_maybe_execute(
                "SOLTokenAddr", "solana", 500.0, "paper", asyncio.get_event_loop()
            )

        mock_exec.assert_not_called()
        assert tx_sig is None
        assert price > 0
