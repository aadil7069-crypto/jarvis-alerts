import os
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Security
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import load_config
from models.schema import (
    PredictionMarket, MarketProbability, MarketEvent, MacroRiskState,
    PaperPortfolio, Trade, TradeIdea, Token, TokenVetting, Watchlist,
    Signal, Performance, CalibrationWeight,
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


@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(content=_DASHBOARD_HTML)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis 360°</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }
  header { background: #161b22; border-bottom: 1px solid #30363d; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 18px; font-weight: 700; letter-spacing: 1px; color: #58a6ff; }
  .badges { display: flex; gap: 8px; }
  .badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; letter-spacing: .5px; }
  .badge-paper { background: #1f4068; color: #58a6ff; }
  .badge-regime { background: #1f3a1f; color: #3fb950; }
  .badge-fear { background: #3a1f1f; color: #f85149; }
  .refresh-info { font-size: 11px; color: #8b949e; }
  main { padding: 24px; max-width: 1400px; margin: 0 auto; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card-label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: .8px; margin-bottom: 6px; }
  .card-value { font-size: 22px; font-weight: 700; }
  .card-sub { font-size: 11px; color: #8b949e; margin-top: 4px; }
  .green { color: #3fb950; }
  .red { color: #f85149; }
  .neutral { color: #e6edf3; }
  section { margin-bottom: 28px; }
  section h2 { font-size: 13px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #21262d; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 11px; color: #8b949e; font-weight: 600; text-transform: uppercase; letter-spacing: .6px; padding: 8px 12px; border-bottom: 1px solid #21262d; }
  td { padding: 10px 12px; border-bottom: 1px solid #161b22; font-size: 13px; }
  tr:hover td { background: #1c2128; }
  .symbol { font-weight: 700; color: #58a6ff; }
  .chain-tag { font-size: 10px; padding: 2px 6px; border-radius: 4px; background: #21262d; color: #8b949e; }
  .exit-tag { font-size: 10px; padding: 2px 6px; border-radius: 4px; }
  .exit-stop_loss { background: #3a1f1f; color: #f85149; }
  .exit-take_profit { background: #1f3a1f; color: #3fb950; }
  .exit-trailing_stop { background: #1f3028; color: #58a6ff; }
  .exit-timeout { background: #2a2010; color: #d29922; }
  .empty { color: #8b949e; padding: 20px 12px; font-style: italic; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; animation: pulse 2s infinite; }
  .dot-green { background: #3fb950; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .loading { color: #8b949e; }
</style>
</head>
<body>
<header>
  <h1>⚡ JARVIS 360°</h1>
  <div class="badges">
    <span class="badge badge-paper">PAPER MODE</span>
    <span class="badge badge-regime" id="regime-badge">LOADING...</span>
    <span class="badge badge-fear" id="sentiment-badge">—</span>
  </div>
  <div class="refresh-info"><span class="dot dot-green"></span>Live · refreshes in <span id="countdown">15</span>s</div>
</header>
<main>
  <div class="cards" id="portfolio-cards">
    <div class="card"><div class="card-label">Total Value</div><div class="card-value loading" id="total-value">—</div></div>
    <div class="card"><div class="card-label">Cash</div><div class="card-value" id="cash">—</div></div>
    <div class="card"><div class="card-label">Invested</div><div class="card-value" id="invested">—</div></div>
    <div class="card"><div class="card-label">All-time P&L</div><div class="card-value" id="pnl">—</div><div class="card-sub" id="pnl-pct">—</div></div>
    <div class="card"><div class="card-label">Daily P&L</div><div class="card-value" id="daily-pnl">—</div></div>
    <div class="card"><div class="card-label">Win Rate</div><div class="card-value" id="win-rate">—</div><div class="card-sub" id="trade-count">—</div></div>
    <div class="card"><div class="card-label">Open Positions</div><div class="card-value" id="open-pos">—</div></div>
  </div>

  <section>
    <h2>Open Positions</h2>
    <table>
      <thead><tr><th>Token</th><th>Chain</th><th>Entry Price</th><th>Size</th><th>Opened</th></tr></thead>
      <tbody id="open-trades-body"><tr><td colspan="5" class="empty">Loading...</td></tr></tbody>
    </table>
  </section>

  <section>
    <h2>Closed Trades</h2>
    <table>
      <thead><tr><th>Token</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Exit Reason</th><th>Duration</th></tr></thead>
      <tbody id="closed-trades-body"><tr><td colspan="6" class="empty">Loading...</td></tr></tbody>
    </table>
  </section>

  <section>
    <h2>Recent Signals</h2>
    <table>
      <thead><tr><th>Token</th><th>Agent</th><th>Type</th><th>Strength</th><th>Reason</th></tr></thead>
      <tbody id="signals-body"><tr><td colspan="5" class="empty">Loading...</td></tr></tbody>
    </table>
  </section>
</main>

<script>
const fmt = (n, decimals=2) => n == null ? '—' : Number(n).toFixed(decimals);
const fmtUSD = n => n == null ? '—' : '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
const fmtPrice = n => {
  if (n == null) return '—';
  if (n < 0.0001) return '$' + Number(n).toExponential(3);
  if (n < 0.01) return '$' + Number(n).toFixed(6);
  if (n < 1) return '$' + Number(n).toFixed(4);
  return '$' + Number(n).toFixed(2);
};
const colorClass = n => n > 0 ? 'green' : n < 0 ? 'red' : 'neutral';
const timeAgo = iso => {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z'))) / 1000;
  if (diff < 60) return Math.round(diff) + 's ago';
  if (diff < 3600) return Math.round(diff/60) + 'm ago';
  if (diff < 86400) return Math.round(diff/3600) + 'h ago';
  return Math.round(diff/86400) + 'd ago';
};
const duration = (open, close) => {
  if (!open || !close) return '—';
  const diff = (new Date(close + (close.endsWith('Z')?'':'Z')) - new Date(open + (open.endsWith('Z')?'':'Z'))) / 1000;
  if (diff < 3600) return Math.round(diff/60) + 'm';
  if (diff < 86400) return Math.round(diff/3600) + 'h';
  return Math.round(diff/86400) + 'd';
};

async function fetchJSON(url) {
  try { const r = await fetch(url); return r.ok ? r.json() : null; }
  catch { return null; }
}

async function refresh() {
  const [portfolio, openTrades, history, signals] = await Promise.all([
    fetchJSON('/api/portfolio'),
    fetchJSON('/api/trades/open'),
    fetchJSON('/api/trades/history?limit=20'),
    fetchJSON('/api/signals/recent?limit=15'),
  ]);

  // Portfolio cards
  if (portfolio && !portfolio.status) {
    const pnl = portfolio.all_time_pnl || 0;
    const pnlPct = portfolio.all_time_pnl_pct || 0;
    const dailyPnl = portfolio.daily_pnl || 0;
    document.getElementById('total-value').textContent = fmtUSD(portfolio.total_value);
    document.getElementById('cash').textContent = fmtUSD(portfolio.cash_balance);
    document.getElementById('invested').textContent = fmtUSD(portfolio.total_invested);
    const pnlEl = document.getElementById('pnl');
    pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmtUSD(pnl);
    pnlEl.className = 'card-value ' + colorClass(pnl);
    document.getElementById('pnl-pct').textContent = (pnlPct >= 0 ? '+' : '') + fmt(pnlPct) + '%';
    const dpEl = document.getElementById('daily-pnl');
    dpEl.textContent = (dailyPnl >= 0 ? '+' : '') + fmtUSD(dailyPnl);
    dpEl.className = 'card-value ' + colorClass(dailyPnl);
    document.getElementById('win-rate').textContent = fmt(portfolio.win_rate, 1) + '%';
    document.getElementById('trade-count').textContent = (portfolio.win_count||0) + 'W / ' + ((portfolio.total_closed_trades||0)-(portfolio.win_count||0)) + 'L';
    document.getElementById('open-pos').textContent = portfolio.open_positions || 0;
  }

  // Open trades
  const otBody = document.getElementById('open-trades-body');
  if (openTrades && openTrades.length > 0) {
    otBody.innerHTML = openTrades.map(t => `
      <tr>
        <td><span class="symbol">${t.symbol || '???'}</span></td>
        <td><span class="chain-tag">${t.chain || '—'}</span></td>
        <td>${fmtPrice(t.entry_price)}</td>
        <td>${fmtUSD(t.size_usd)}</td>
        <td>${timeAgo(t.opened_at)}</td>
      </tr>`).join('');
  } else {
    otBody.innerHTML = '<tr><td colspan="5" class="empty">No open positions</td></tr>';
  }

  // Closed trades
  const ctBody = document.getElementById('closed-trades-body');
  if (history && history.length > 0) {
    ctBody.innerHTML = history.map(t => {
      const pnl = t.pnl_usd || 0;
      const exitClass = 'exit-tag exit-' + (t.exit_reason || 'unknown').replace(/ /g,'_');
      return `<tr>
        <td><span class="symbol">${t.symbol || '???'}</span></td>
        <td>${fmtPrice(t.entry_price)}</td>
        <td>${fmtPrice(t.exit_price)}</td>
        <td class="${colorClass(pnl)}">${pnl >= 0 ? '+' : ''}${fmtUSD(pnl)} <small>(${pnl >= 0?'+':''}${fmt(t.pnl_pct)}%)</small></td>
        <td><span class="${exitClass}">${t.exit_reason || '—'}</span></td>
        <td>${duration(t.opened_at, t.closed_at)}</td>
      </tr>`;
    }).join('');
  } else {
    ctBody.innerHTML = '<tr><td colspan="6" class="empty">No closed trades yet — positions will close via stop-loss, take-profit, or 48h timeout</td></tr>';
  }

  // Signals
  const sBody = document.getElementById('signals-body');
  if (signals && signals.length > 0) {
    sBody.innerHTML = signals.filter(s => s.symbol !== 'global' || s.signal_type === 'bullish').slice(0,10).map(s => {
      const strength = Math.round((s.strength || 0));
      const bar = '█'.repeat(Math.round(strength/10)) + '░'.repeat(10 - Math.round(strength/10));
      return `<tr>
        <td><span class="symbol">${s.symbol || 'global'}</span></td>
        <td>${s.agent || '—'}</td>
        <td class="${s.signal_type === 'bullish' ? 'green' : 'red'}">${s.signal_type}</td>
        <td title="${strength}/100"><span style="font-family:monospace;font-size:11px;color:#58a6ff">${bar}</span> ${strength}</td>
        <td style="color:#8b949e;font-size:12px">${(s.reason||'').slice(0,60)}</td>
      </tr>`;
    }).join('');
  } else {
    sBody.innerHTML = '<tr><td colspan="5" class="empty">No signals yet</td></tr>';
  }
}

// Countdown + auto-refresh
let count = 15;
setInterval(() => {
  count--;
  if (count <= 0) { count = 15; refresh(); }
  document.getElementById('countdown').textContent = count;
}, 1000);

refresh();
</script>
</body>
</html>"""


@app.get("/status", dependencies=[Security(_auth)])
def status():
    with _session() as db:
        portfolio = db.query(PaperPortfolio).order_by(PaperPortfolio.updated_at.desc()).first()
        open_positions = db.query(Trade).filter_by(status="open", is_paper=True).count()
        macro = db.query(MacroRiskState).order_by(MacroRiskState.recorded_at.desc()).first()
        calibration = db.query(CalibrationWeight).order_by(CalibrationWeight.calibrated_at.desc()).first()

    return {
        "system": "Jarvis 360°",
        "mode": config["system"]["mode"].upper(),
        "agents": 15,
        "notifications_enabled": config.get("notifications", {}).get("enabled", False),
        "prediction_markets_enabled": config.get("prediction_markets", {}).get("enabled", True),
        "portfolio": {
            "total_value": portfolio.total_value if portfolio else None,
            "all_time_pnl": portfolio.all_time_pnl if portfolio else None,
            "open_positions": open_positions,
        },
        "macro": {
            "safe_mode": macro.safe_mode if macro else "unknown",
            "sentiment": macro.sentiment if macro else "unknown",
            "confidence_modifier": macro.confidence_modifier if macro else 0,
        },
        "calibration": {
            "active": calibration is not None,
            "trade_count": calibration.trade_count if calibration else 0,
            "base_win_rate": round(calibration.base_win_rate * 100, 1) if calibration else None,
            "calibrated_at": calibration.calibrated_at.isoformat() if calibration else None,
        },
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


# ── Market Regime ─────────────────────────────────────────────────────────────

@app.get("/api/regime", dependencies=[Security(_auth)])
def get_regime():
    """Current market regime from RegimeAgent (BTC-derived)."""
    with _session() as db:
        # RegimeAgent stores regime in the Signal table under agent_name="regime"
        latest = (
            db.query(Signal)
            .filter(Signal.agent_name == "regime")
            .order_by(Signal.created_at.desc())
            .first()
        )
        if not latest:
            return {"status": "no_data", "message": "Regime not yet classified."}
        return {
            "regime": latest.reason,
            "recorded_at": latest.created_at.isoformat() if latest.created_at else None,
        }


# ── Calibration ───────────────────────────────────────────────────────────────

@app.get("/api/calibration", dependencies=[Security(_auth)])
def get_calibration():
    """Latest signal weight calibration from LearningAgent."""
    with _session() as db:
        latest = (
            db.query(CalibrationWeight)
            .order_by(CalibrationWeight.calibrated_at.desc())
            .first()
        )
        if not latest:
            return {
                "status": "pending",
                "message": f"Calibration needs at least 20 closed paper trades.",
                "using_base_weights": True,
            }
        return {
            "calibrated_at": latest.calibrated_at.isoformat(),
            "trade_count": latest.trade_count,
            "base_win_rate": round((latest.base_win_rate or 0) * 100, 1),
            "weights": {
                "smart_money_buy":    latest.smart_money_buy,
                "elite_trader":       latest.elite_trader,
                "vetting_pass":       latest.vetting_pass,
                "whale_accumulation": latest.whale_accumulation,
                "strategy_confirm":   latest.strategy_confirm,
                "positive_sentiment": latest.positive_sentiment,
            },
        }


# ── Trade ideas queue ─────────────────────────────────────────────────────────

@app.get("/api/trade-ideas/pending", dependencies=[Security(_auth)])
def get_pending_trade_ideas(limit: int = 20):
    """Trade ideas generated by Orchestrator waiting for execution."""
    with _session() as db:
        ideas = (
            db.query(TradeIdea)
            .filter_by(status="pending")
            .order_by(TradeIdea.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "idea_id": i.id,
                "token_id": i.token_id,
                "confidence_score": i.confidence_score,
                "agents_bullish": i.agents_bullish,
                "direction": i.direction,
                "suggested_size_pct": i.suggested_size_pct,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in ideas
        ]
