"""
Arkham Intelligence client tests — response parsing, graceful degradation.
No real API calls.
"""
import pytest
from unittest.mock import patch, MagicMock
from data.arkham import get_entity, get_entity_label


# ── No API key — silent no-op ─────────────────────────────────────────────────

def test_returns_empty_when_no_key(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "")
    result = get_entity("SomeAddress")
    assert result == {}


def test_label_returns_none_when_no_key(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "")
    assert get_entity_label("SomeAddress") is None


# ── 404 — unknown address ─────────────────────────────────────────────────────

def test_returns_empty_on_404(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("data.arkham.requests.get", return_value=mock_resp):
        result = get_entity("UnknownAddress")
    assert result == {}


# ── Successful response — entity with name ────────────────────────────────────

def test_parses_entity_name(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "address": "abc123",
        "arkhamEntity": {
            "name": "Jump Trading",
            "type": "fund",
            "website": "https://jumpcrypto.com",
            "twitter": "JumpCryptoHQ",
        },
        "arkhamLabel": {
            "name": "Jump Trading - Market Maker",
        },
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.arkham.requests.get", return_value=mock_resp):
        result = get_entity("abc123")
    assert result["name"] == "Jump Trading"
    assert result["type"] == "fund"
    assert result["label"] == "Jump Trading - Market Maker"
    assert result["twitter"] == "JumpCryptoHQ"


def test_label_helper_returns_specific_label(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "arkhamEntity": {"name": "Wintermute", "type": "fund"},
        "arkhamLabel": {"name": "Wintermute - DeFi Desk"},
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.arkham.requests.get", return_value=mock_resp):
        label = get_entity_label("xyz789")
    assert label == "Wintermute - DeFi Desk"


# ── Entity without label object ───────────────────────────────────────────────

def test_falls_back_to_entity_name_when_no_label(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "arkhamEntity": {"name": "Alameda Research", "type": "fund"},
        "arkhamLabel": None,
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.arkham.requests.get", return_value=mock_resp):
        result = get_entity("addr")
    assert result["name"] == "Alameda Research"
    assert result["label"] == "Alameda Research"


# ── Empty entity and label ────────────────────────────────────────────────────

def test_returns_empty_when_both_entity_and_label_empty(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "arkhamEntity": {},
        "arkhamLabel": {},
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.arkham.requests.get", return_value=mock_resp):
        result = get_entity("addr")
    assert result == {}


# ── Network error — no crash ──────────────────────────────────────────────────

def test_returns_empty_on_network_error(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test_key")
    with patch("data.arkham.requests.get", side_effect=Exception("timeout")):
        result = get_entity("addr")
    assert result == {}


def test_label_returns_none_on_network_error(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test_key")
    with patch("data.arkham.requests.get", side_effect=Exception("timeout")):
        assert get_entity_label("addr") is None


# ── get_entity_label priority: label > name ───────────────────────────────────

def test_label_prefers_specific_label_over_entity_name(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "arkhamEntity": {"name": "Jump Trading"},
        "arkhamLabel": {"name": "Jump Trading - Solana Desk"},
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.arkham.requests.get", return_value=mock_resp):
        label = get_entity_label("addr")
    assert label == "Jump Trading - Solana Desk"
