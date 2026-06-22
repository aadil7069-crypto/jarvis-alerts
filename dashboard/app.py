import os
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import load_config
from models.schema import (
    PredictionMarket, MarketProbability, MarketEvent, MacroRiskState,
    PaperPortfolio, Trade, TradeIdea, Token, TokenVetting, Watchlist,
    Signal, Performance,
)

app = FastAPI(title="Jarvis 360° Dashboard", version="0.3.0")

config = load_config()
engine = create_engine(
    config["database"]["url"],
    connect_args={"check_same_thread": False},
)
_Session = sessionmaker(bind=engine)

# ── Session helper ────────────────────────────────────────────────────────────

@contextmanager
def _session():
    db = _Session()
    try:
        yield db
    finally:
        db.close()

# ── Authentication ────────────────────────────────────────────────────────────

_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _auth(key: str = Security(_key_header)) -> None:
    if _API_KEY and key != _API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


# ── Core ──────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"healthy": True}


@app.get("/", dependencies=[Security(_auth)])
def root():
    return {"status": "Jarvis 360° is running", "version": "0.3.0"}


@app.get("/status", dependencies=[Security(_auth)])
def status():
    return {
        "system": "Jarvis 360°",
        "mode": config["system"]["mode"].upper(),
        "agents": 15,
        "notifications_enabled": config.get("notifications", {}).get("enabled", False),
        "prediction_markets_enabled": config.get("prediction_markets", {}).get("enabled", True),
    }


# ── Portfolio ─────────────────────────────────────────────────────────────────

@app.get("/api/portfolio", dependencies=[Security(_auth)])
def get_portfolio():
    with _session() as db:
        snapshot = (
            db.query(PaperPortfolio)
            .order_by(PaperPortfolio.updated_at.desc())
            .first()
        )
        if not snapshot:
            return {"status": "no_data", "message": "No portfolio snapshots yet."}

        open_count = db.query(Trade).filter_by(status="open", is_paper=True).count()
        closed_count = db.query(Trade).filter_by(status="closed", is_paper=True).count()
        win_count = (
            db.query(Trade)
            .filter(Trade.is_paper == True, Trade.status == "closed", Trade.pnl_usd > 0)
            .count()
        )

        return {
            "updated_at": snapshot.updated_at.isoformat() if snapshot.updated_at else None,
            "cash_balance": snapshot.cash_balance,
            "total_invested": snapshot.total_invested,
            "total_value": snapshot.total_value,
            "all_time_pnl": snapshot.all_time_pnl,
            "all_time_pnl_pct": round(snapshot.all_time_pnl / max(snapshot.total_value - snapshot.all_time_pnl, 1) * 100, 2),
            "daily_pnl": snapshot.daily_pnl,
            "open_positions": open_count,
            "total_closed_trades": closed_count,
            "win_count": win_count,
            "win_rate": round(win_count / max(closed_count, 1) * 100, 1),
        }


# ── Trades ────────────────────────────────────────────────────────────────────

@app.get("/api/trades/open", dependencies=[Security(_auth)])
def get_open_trades():
    with _session() as db:
        trades = db.query(Trade).filter_by(status="open", is_paper=True).all()
        result = []
        for t in trades:
            token = db.query(Token).filter_by(id=t.token_id).first()
            result.append({
                "trade_id": t.id,
                "token_address": token.address if token else None,
                "symbol": token.symbol if token else None,
                "chain": token.chain if token else None,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "size_usd": t.size_usd,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            })
        return result


@app.get("/api/trades/history", dependencies=[Security(_auth)])
def get_trade_history(limit: int = 50):
    with _session() as db:
        trades = (
            db.query(Trade)
            .filter_by(status="closed", is_paper=True)
            .order_by(Trade.closed_at.desc())
            .limit(limit)
            .all()
        )
        result = []
        for t in trades:
            token = db.query(Token).filter_by(id=t.token_id).first()
            result.append({
                "trade_id": t.id,
                "symbol": token.symbol if token else None,
                "chain": token.chain if token else None,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "size_usd": t.size_usd,
                "pnl_usd": t.pnl_usd,
                "pnl_pct": round((t.pnl_pct or 0) * 100, 2),
                "exit_reason": t.exit_reason,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            })
        return result


# ── Performance ───────────────────────────────────────────────────────────────

@app.get("/api/performance", dependencies=[Security(_auth)])
def get_performance(limit: int = 30):
    with _session() as db:
        records = (
            db.query(Performance)
            .filter_by(is_paper=True)
            .order_by(Performance.date.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "date": r.date.isoformat() if r.date else None,
                "total_trades": r.total_trades,
                "winning_trades": r.winning_trades,
                "win_rate": r.win_rate,
                "total_pnl_usd": r.total_pnl_usd,
                "best_trade_pnl": r.best_trade_pnl,
                "worst_trade_pnl": r.worst_trade_pnl,
                "portfolio_value": r.portfolio_value,
            }
            for r in reversed(records)
        ]


# ── Watchlist ─────────────────────────────────────────────────────────────────

@app.get("/api/watchlist", dependencies=[Security(_auth)])
def get_watchlist():
    with _session() as db:
        entries = db.query(Watchlist).filter_by(status="watching").all()
        result = []
        for e in entries:
            token = db.query(Token).filter_by(id=e.token_id).first()
            vetting = (
                db.query(TokenVetting)
                .filter_by(token_id=e.token_id)
                .order_by(TokenVetting.checked_at.desc())
                .first()
            ) if token else None
            result.append({
                "token_id": e.token_id,
                "address": token.address if token else None,
                "symbol": token.symbol if token else None,
                "chain": token.chain if token else None,
                "added_at": e.added_at.isoformat() if e.added_at else None,
                "added_by": e.added_by,
                "vetting_passed": vetting.overall_pass if vetting else None,
                "liquidity_usd": vetting.liquidity_usd if vetting else None,
            })
        return result


# ── Signals ───────────────────────────────────────────────────────────────────

@app.get("/api/signals/recent", dependencies=[Security(_auth)])
def get_recent_signals(limit: int = 100):
    with _session() as db:
        signals = (
            db.query(Signal)
            .order_by(Signal.created_at.desc())
            .limit(limit)
            .all()
        )
        result = []
        for s in signals:
            token = db.query(Token).filter_by(id=s.token_id).first() if s.token_id else None
            result.append({
                "signal_id": s.id,
                "symbol": token.symbol if token else "global",
                "chain": token.chain if token else None,
                "agent": s.agent_name,
                "signal_type": s.signal_type,
                "strength": s.strength,
                "reason": s.reason,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            })
        return result


# ── Prediction Markets ────────────────────────────────────────────────────────

@app.get("/api/prediction-markets/active", dependencies=[Security(_auth)])
def get_active_markets():
    with _session() as db:
        markets = db.query(PredictionMarket).all()
        return [
            {
                "market_id": m.market_id,
                "question": m.question,
                "category": m.category,
                "market_type": m.market_type,
                "volume": m.volume,
                "end_date": m.end_date,
            }
            for m in markets
        ]


@app.get("/api/prediction-markets/{market_id}/history", dependencies=[Security(_auth)])
def get_market_history(market_id: str, limit: int = 100):
    with _session() as db:
        market = db.query(PredictionMarket).filter_by(market_id=market_id).first()
        if not market:
            raise HTTPException(status_code=404, detail="Market not found")
        history = (
            db.query(MarketProbability)
            .filter_by(market_id=market_id)
            .order_by(MarketProbability.recorded_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "market_id": market_id,
            "question": market.question,
            "history": [
                {
                    "recorded_at": h.recorded_at.isoformat(),
                    "yes_probability": h.yes_probability,
                    "volume": h.volume,
                }
                for h in reversed(history)
            ],
        }


@app.get("/api/prediction-markets/events", dependencies=[Security(_auth)])
def get_market_events(limit: int = 50):
    with _session() as db:
        events = (
            db.query(MarketEvent)
            .order_by(MarketEvent.detected_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "market_id": e.market_id,
                "detected_at": e.detected_at.isoformat(),
                "event_type": e.event_type,
                "old_probability": e.old_probability,
                "new_probability": e.new_probability,
                "change": e.change,
                "magnitude": e.magnitude,
                "description": e.description,
            }
            for e in events
        ]


@app.get("/api/prediction-markets/risk", dependencies=[Security(_auth)])
def get_macro_risk():
    with _session() as db:
        latest = (
            db.query(MacroRiskState)
            .order_by(MacroRiskState.recorded_at.desc())
            .first()
        )
        if not latest:
            return {"status": "no_data", "message": "No macro risk data yet."}
        return {
            "recorded_at": latest.recorded_at.isoformat(),
            "sentiment": latest.sentiment,
            "sentiment_score": latest.sentiment_score,
            "confidence_modifier": latest.confidence_modifier,
            "safe_mode": latest.safe_mode,
            "position_multiplier": latest.position_multiplier,
            "risk_trigger_count": latest.risk_trigger_count,
        }
