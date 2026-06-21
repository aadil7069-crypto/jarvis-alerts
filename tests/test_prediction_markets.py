"""
Tests for the Prediction Market Intelligence Engine.
All tests are pure logic — no real API calls made.
"""
import pytest
from prediction_markets.polymarket import parse_market
from prediction_markets.event_detector import detect_probability_change, classify_market_type
from prediction_markets.sentiment import market_to_sentiment, aggregate_sentiment, BULLISH, BEARISH, NEUTRAL, RISK_OFF
from prediction_markets.confidence import calculate_modifier
from prediction_markets.safe_mode import SafeModeController

MOCK_CONFIG = {
    "prediction_markets": {
        "enabled": True,
        "risk_off_threshold": 0.60,
        "extreme_risk_threshold": 0.80,
        "significant_change_pct": 0.10,
        "max_confidence_bonus": 10,
        "max_confidence_penalty": 25,
        "safe_mode_position_multiplier": 0.50,
        "block_memecoins_in_safe_mode": True,
        "crypto_keywords": ["bitcoin", "btc", "solana", "crypto"],
        "macro_keywords": ["federal reserve", "recession", "inflation"],
    }
}


# ── polymarket.parse_market ───────────────────────────────────────────────────

def test_parse_market_full():
    raw = {
        "id": "abc123",
        "question": "Will BTC hit $100k in 2025?",
        "category": "crypto",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.72", "0.28"],
        "volumeNum": 500000,
        "active": True,
        "closed": False,
    }
    m = parse_market(raw)
    assert m["id"] == "abc123"
    assert m["yes_probability"] == 0.72
    assert len(m["outcomes"]) == 2
    assert m["volume"] == 500000


def test_parse_market_string_json():
    raw = {
        "id": "xyz",
        "question": "Test?",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.55","0.45"]',
    }
    m = parse_market(raw)
    assert m["yes_probability"] == 0.55


def test_parse_market_empty():
    m = parse_market({})
    assert m["id"] == ""
    assert m["yes_probability"] == 0.0


# ── event_detector ────────────────────────────────────────────────────────────

def test_detect_significant_change():
    event = detect_probability_change(0.30, 0.45, threshold=0.10)
    assert event is not None
    assert event["direction"] == "up"
    assert event["magnitude"] == "significant"


def test_detect_major_change():
    event = detect_probability_change(0.20, 0.60, threshold=0.10)
    assert event is not None
    assert event["magnitude"] == "major"
    assert abs(event["change"] - 0.40) < 0.001


def test_no_change_below_threshold():
    event = detect_probability_change(0.50, 0.55, threshold=0.10)
    assert event is None


def test_classify_crypto_market():
    result = classify_market_type("Will Bitcoin hit $200k this year?", MOCK_CONFIG)
    assert result == "crypto"


def test_classify_macro_market():
    result = classify_market_type("Will the Federal Reserve cut rates in 2025?", MOCK_CONFIG)
    assert result == "macro"


def test_classify_other_market():
    result = classify_market_type("Who will win the Super Bowl?", MOCK_CONFIG)
    assert result == "other"


# ── sentiment ─────────────────────────────────────────────────────────────────

def test_bullish_positive_market():
    result = market_to_sentiment("Will BTC hit $100k?", 0.75)
    assert result["sentiment"] == BULLISH
    assert result["score"] > 0


def test_risk_off_risk_framing():
    result = market_to_sentiment("Will crypto market crash in 2025?", 0.70)
    assert result["sentiment"] == RISK_OFF
    assert result["score"] < 0
    assert result["is_risk_framing"] is True


def test_neutral_market():
    result = market_to_sentiment("Will ETH outperform BTC?", 0.50)
    assert result["sentiment"] == NEUTRAL
    assert result["score"] == 0


def test_aggregate_single_bullish():
    sentiments = [{"sentiment": BULLISH, "score": 20}]
    result = aggregate_sentiment(sentiments)
    assert result["sentiment"] == BULLISH


def test_aggregate_multiple_risk_off():
    sentiments = [
        {"sentiment": RISK_OFF, "score": -30},
        {"sentiment": RISK_OFF, "score": -20},
        {"sentiment": NEUTRAL, "score": 0},
    ]
    result = aggregate_sentiment(sentiments)
    assert result["sentiment"] == RISK_OFF
    assert result["risk_off_signals"] == 2


def test_aggregate_empty():
    result = aggregate_sentiment([])
    assert result["sentiment"] == NEUTRAL
    assert result["score"] == 0


# ── confidence modifier ───────────────────────────────────────────────────────

def test_modifier_risk_off():
    macro = {"sentiment": "risk_off", "score": -40}
    result = calculate_modifier(macro, MOCK_CONFIG)
    assert result["modifier"] == -25
    assert result["safe_mode_triggered"] is True


def test_modifier_bullish():
    macro = {"sentiment": "bullish", "score": 25}
    result = calculate_modifier(macro, MOCK_CONFIG)
    assert result["modifier"] > 0
    assert result["modifier"] <= 10


def test_modifier_neutral():
    macro = {"sentiment": "neutral", "score": 0}
    result = calculate_modifier(macro, MOCK_CONFIG)
    assert result["modifier"] == 0
    assert result["safe_mode_triggered"] is False


# ── safe mode controller ──────────────────────────────────────────────────────

def test_safe_mode_normal():
    sm = SafeModeController(MOCK_CONFIG)
    status = sm.evaluate([])
    assert status["mode"] == "normal"
    assert status["position_multiplier"] == 1.0
    assert status["allows_memecoins"] is True


def test_safe_mode_cautious():
    sm = SafeModeController(MOCK_CONFIG)
    risk_markets = [{"question": "Will crypto crash?", "yes_probability": 0.65}]
    status = sm.evaluate(risk_markets)
    assert status["mode"] == "cautious"
    assert status["position_multiplier"] == 0.50
    assert status["allows_memecoins"] is False


def test_safe_mode_extreme():
    sm = SafeModeController(MOCK_CONFIG)
    risk_markets = [{"question": "Will crypto crash?", "yes_probability": 0.85}]
    status = sm.evaluate(risk_markets)
    assert status["mode"] == "safe"
    assert status["position_multiplier"] == 0.25


def test_safe_mode_clears():
    sm = SafeModeController(MOCK_CONFIG)
    sm.evaluate([{"question": "Crash?", "yes_probability": 0.85}])
    assert sm.mode == "safe"
    sm.evaluate([])
    assert sm.mode == "normal"
