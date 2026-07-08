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
    html = _DASHBOARD_HTML.replace("__API_KEY__", _API_KEY)
    return HTMLResponse(content=html)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis 360°</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#080b10;--bg2:#0d1117;--bg3:#161b22;--bg4:#1c2128;
    --border:#21262d;--border2:#30363d;
    --text:#e6edf3;--muted:#8b949e;--dim:#484f58;
    --blue:#58a6ff;--blue-dim:#1f4068;
    --green:#3fb950;--green-dim:#1a3a1f;
    --red:#f85149;--red-dim:#3a1f1f;
    --yellow:#d29922;--yellow-dim:#2d2010;
    --purple:#bc8cff;--purple-dim:#2a1f4a;
  }
  html{height:100%}
  body{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;font-size:13px;min-height:100%;display:flex;flex-direction:column}

  /* ── Header ── */
  header{background:var(--bg2);border-bottom:1px solid var(--border2);padding:0 24px;height:56px;display:flex;align-items:center;gap:24px;flex-shrink:0;position:sticky;top:0;z-index:100}
  .logo{font-size:16px;font-weight:700;letter-spacing:2px;color:var(--blue);font-family:'JetBrains Mono',monospace;white-space:nowrap}
  .logo span{color:var(--purple)}
  .status-pills{display:flex;gap:8px;align-items:center}
  .pill{padding:3px 10px;border-radius:20px;font-size:10px;font-weight:600;letter-spacing:.8px;text-transform:uppercase;white-space:nowrap}
  .pill-paper{background:var(--blue-dim);color:var(--blue);border:1px solid #2a5090}
  .pill-regime{background:var(--green-dim);color:var(--green);border:1px solid #2a5a2a;transition:all .5s}
  .pill-sentiment{background:var(--bg3);color:var(--muted);border:1px solid var(--border)}
  .header-right{margin-left:auto;display:flex;align-items:center;gap:16px}
  .live-dot{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:blink 2s ease-in-out infinite}
  @keyframes blink{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(63,185,80,.4)}50%{opacity:.6;box-shadow:0 0 0 4px rgba(63,185,80,0)}}
  .clock{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--muted)}

  /* ── Layout ── */
  .layout{display:grid;grid-template-columns:1fr 340px;grid-template-rows:auto 1fr;gap:0;flex:1;overflow:hidden}
  .main-col{overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:20px}
  .side-col{border-left:1px solid var(--border);overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:20px;background:var(--bg2)}

  /* ── Stats bar ── */
  .stats{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;background:var(--border);border:1px solid var(--border);border-radius:10px;overflow:hidden}
  .stat{background:var(--bg3);padding:14px 16px;transition:background .2s}
  .stat:hover{background:var(--bg4)}
  .stat-label{font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}
  .stat-value{font-size:18px;font-weight:700;font-family:'JetBrains Mono',monospace;line-height:1}
  .stat-sub{font-size:10px;color:var(--muted);margin-top:4px}
  .green{color:var(--green)}
  .red{color:var(--red)}
  .blue{color:var(--blue)}
  .yellow{color:var(--yellow)}
  .muted{color:var(--muted)}

  /* ── Panels ── */
  .panel{background:var(--bg3);border:1px solid var(--border);border-radius:10px;overflow:hidden}
  .panel-header{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
  .panel-title{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1px;color:var(--muted)}
  .panel-count{font-size:10px;background:var(--bg4);border:1px solid var(--border2);padding:1px 7px;border-radius:10px;color:var(--muted)}

  /* ── Tables ── */
  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:var(--dim);padding:8px 16px;border-bottom:1px solid var(--border);white-space:nowrap}
  td{padding:11px 16px;border-bottom:1px solid var(--border);font-size:12px;vertical-align:middle}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:var(--bg4)}
  .empty-row td{color:var(--dim);text-align:center;padding:28px;font-style:italic;font-size:12px}

  /* ── Token/symbol ── */
  .sym{font-weight:700;color:var(--blue);font-family:'JetBrains Mono',monospace;font-size:13px}
  .chain{font-size:9px;padding:2px 5px;border-radius:4px;background:var(--bg4);color:var(--dim);font-weight:600;letter-spacing:.5px;text-transform:uppercase;margin-left:4px;vertical-align:middle}
  .chain-sol{background:#1a2a4a;color:#9abfff}
  .chain-bnb{background:#2a2a10;color:#d4c44a}

  /* ── P&L badges ── */
  .pnl-pos{color:var(--green);font-weight:600;font-family:'JetBrains Mono',monospace}
  .pnl-neg{color:var(--red);font-weight:600;font-family:'JetBrains Mono',monospace}
  .pnl-neu{color:var(--muted);font-family:'JetBrains Mono',monospace}
  .pct-badge{font-size:10px;padding:1px 6px;border-radius:4px;margin-left:4px;font-weight:600}
  .pct-pos{background:var(--green-dim);color:var(--green)}
  .pct-neg{background:var(--red-dim);color:var(--red)}

  /* ── Exit reason tags ── */
  .exit{font-size:10px;padding:2px 7px;border-radius:4px;font-weight:600;letter-spacing:.3px;white-space:nowrap}
  .exit-take_profit{background:var(--green-dim);color:var(--green)}
  .exit-stop_loss{background:var(--red-dim);color:var(--red)}
  .exit-trailing_stop{background:var(--blue-dim);color:var(--blue)}
  .exit-timeout{background:var(--yellow-dim);color:var(--yellow)}
  .exit-manual{background:var(--purple-dim);color:var(--purple)}
  .exit-liquidity_collapse{background:var(--red-dim);color:var(--red);border:1px solid var(--red)}
  .exit-rug_detected{background:var(--red-dim);color:var(--red);border:1px solid var(--red)}

  /* ── Signal feed ── */
  .signal-item{padding:10px 16px;border-bottom:1px solid var(--border);transition:background .15s}
  .signal-item:last-child{border-bottom:none}
  .signal-item:hover{background:var(--bg4)}
  .sig-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}
  .sig-sym{font-weight:700;color:var(--blue);font-family:'JetBrains Mono',monospace;font-size:12px}
  .sig-time{font-size:10px;color:var(--dim)}
  .sig-mid{display:flex;align-items:center;gap:6px;margin-bottom:4px}
  .sig-agent{font-size:10px;color:var(--muted);background:var(--bg4);padding:1px 6px;border-radius:4px}
  .sig-type{font-size:10px;font-weight:600;padding:1px 6px;border-radius:4px}
  .sig-bullish{background:var(--green-dim);color:var(--green)}
  .sig-bearish{background:var(--red-dim);color:var(--red)}
  .sig-neutral{background:var(--bg4);color:var(--muted)}
  .sig-bar-wrap{height:3px;background:var(--border2);border-radius:2px;overflow:hidden}
  .sig-bar{height:100%;border-radius:2px;transition:width .4s}
  .sig-bar-bull{background:linear-gradient(90deg,var(--green),#7ee787)}
  .sig-bar-bear{background:linear-gradient(90deg,var(--red),#ff7b72)}
  .sig-reason{font-size:11px;color:var(--muted);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

  /* ── Macro panel ── */
  .macro-row{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-bottom:1px solid var(--border)}
  .macro-row:last-child{border-bottom:none}
  .macro-key{font-size:11px;color:var(--muted)}
  .macro-val{font-size:12px;font-weight:600;font-family:'JetBrains Mono',monospace}

  /* ── Open position card (mobile-friendly) ── */
  .pos-row{display:flex;align-items:center;gap:10px;padding:11px 16px;border-bottom:1px solid var(--border)}
  .pos-row:last-child{border-bottom:none}
  .pos-row:hover{background:var(--bg4)}
  .pos-info{flex:1;min-width:0}
  .pos-meta{font-size:10px;color:var(--dim);margin-top:2px}
  .pos-right{text-align:right;white-space:nowrap}
  .pos-size{font-size:12px;font-weight:600;color:var(--text)}
  .pos-entry{font-size:10px;color:var(--muted);margin-top:2px;font-family:'JetBrains Mono',monospace}

  /* ── Scrollbar ── */
  ::-webkit-scrollbar{width:5px;height:5px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}
  ::-webkit-scrollbar-thumb:hover{background:var(--dim)}

  /* ── Responsive ── */
  @media(max-width:900px){
    .layout{grid-template-columns:1fr;grid-template-rows:auto}
    .side-col{border-left:none;border-top:1px solid var(--border)}
    .stats{grid-template-columns:repeat(4,1fr)}
  }
  @media(max-width:600px){
    .stats{grid-template-columns:repeat(2,1fr)}
    header{padding:0 12px}
    .main-col,.side-col{padding:12px}
  }
</style>
</head>
<body>

<header>
  <div class="logo">JARVIS<span>°</span> 360</div>
  <div class="status-pills">
    <span class="pill pill-paper">Paper</span>
    <span class="pill pill-regime" id="regime-pill">—</span>
    <span class="pill pill-sentiment" id="sentiment-pill">—</span>
  </div>
  <div class="header-right">
    <div class="live-dot"><span class="dot"></span>Live</div>
    <div class="clock" id="clock">--:--:--</div>
  </div>
</header>

<div class="layout">
  <div class="main-col">

    <!-- Stats -->
    <div class="stats">
      <div class="stat">
        <div class="stat-label">Portfolio</div>
        <div class="stat-value" id="s-total">—</div>
        <div class="stat-sub">total value</div>
      </div>
      <div class="stat">
        <div class="stat-label">Cash</div>
        <div class="stat-value muted" id="s-cash">—</div>
        <div class="stat-sub">available</div>
      </div>
      <div class="stat">
        <div class="stat-label">Invested</div>
        <div class="stat-value blue" id="s-invested">—</div>
        <div class="stat-sub">in positions</div>
      </div>
      <div class="stat">
        <div class="stat-label">All-time P&amp;L</div>
        <div class="stat-value" id="s-pnl">—</div>
        <div class="stat-sub" id="s-pnl-pct">—</div>
      </div>
      <div class="stat">
        <div class="stat-label">Today P&amp;L</div>
        <div class="stat-value" id="s-daily">—</div>
        <div class="stat-sub">daily</div>
      </div>
      <div class="stat">
        <div class="stat-label">Win Rate</div>
        <div class="stat-value" id="s-wr">—</div>
        <div class="stat-sub" id="s-wl">—</div>
      </div>
      <div class="stat">
        <div class="stat-label">Positions</div>
        <div class="stat-value blue" id="s-pos">—</div>
        <div class="stat-sub">open now</div>
      </div>
    </div>

    <!-- Open positions -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Open Positions</span>
        <span class="panel-count" id="open-count">0</span>
      </div>
      <div id="open-body"></div>
    </div>

    <!-- Closed trades -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Closed Trades</span>
        <span class="panel-count" id="closed-count">0</span>
      </div>
      <table>
        <thead>
          <tr><th>Token</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Reason</th><th>Held</th></tr>
        </thead>
        <tbody id="closed-body">
          <tr class="empty-row"><td colspan="6">Loading...</td></tr>
        </tbody>
      </table>
    </div>

  </div>

  <!-- Sidebar -->
  <div class="side-col">

    <!-- Macro / regime -->
    <div class="panel">
      <div class="panel-header"><span class="panel-title">Macro Risk</span></div>
      <div id="macro-body">
        <div class="macro-row"><span class="macro-key">Mode</span><span class="macro-val" id="m-mode">—</span></div>
        <div class="macro-row"><span class="macro-key">Sentiment</span><span class="macro-val" id="m-sent">—</span></div>
        <div class="macro-row"><span class="macro-key">Regime</span><span class="macro-val" id="m-regime">—</span></div>
        <div class="macro-row"><span class="macro-key">Confidence modifier</span><span class="macro-val" id="m-mod">—</span></div>
        <div class="macro-row"><span class="macro-key">Position multiplier</span><span class="macro-val" id="m-mult">—</span></div>
      </div>
    </div>

    <!-- Signal feed -->
    <div class="panel" style="flex:1">
      <div class="panel-header">
        <span class="panel-title">Signal Feed</span>
        <span class="panel-count" id="sig-count">0</span>
      </div>
      <div id="sig-body" style="max-height:600px;overflow-y:auto">
        <div class="signal-item"><div class="sig-reason">Loading signals...</div></div>
      </div>
    </div>

  </div>
</div>

<script>
'use strict';
const $ = id => document.getElementById(id);
const fmtUSD = n => n==null?'—':'$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtShort = n => {
  if(n==null)return'—';
  const a=Math.abs(n);
  if(a>=1000)return(n<0?'-':'')+'$'+Math.round(a/100)/10+'k';
  return fmtUSD(n);
};
const fmtPrice = n => {
  if(n==null||n===0)return'—';
  if(n<0.000001)return'$'+Number(n).toExponential(2);
  if(n<0.0001)return'$'+Number(n).toFixed(7);
  if(n<0.01)return'$'+Number(n).toFixed(5);
  if(n<1)return'$'+Number(n).toFixed(4);
  if(n<100)return'$'+Number(n).toFixed(3);
  return'$'+Number(n).toFixed(2);
};
const fmt2 = (n,d=2)=>n==null?'—':Number(n).toFixed(d);
const colorCls = n=>n>0?'green':n<0?'red':'muted';
const sign = n=>n>=0?'+':'';

const timeAgo = iso => {
  if(!iso)return'—';
  const s=(Date.now()-new Date(iso.endsWith('Z')?iso:iso+'Z'))/1000;
  if(s<60)return Math.round(s)+'s';
  if(s<3600)return Math.round(s/60)+'m';
  if(s<86400)return Math.round(s/3600)+'h';
  return Math.round(s/86400)+'d';
};
const held = (a,b)=>{
  if(!a||!b)return'—';
  const s=(new Date(b.endsWith('Z')?b:b+'Z')-new Date(a.endsWith('Z')?a:a+'Z'))/1000;
  if(s<3600)return Math.round(s/60)+'m';
  if(s<86400)return Math.round(s/3600)+'h';
  return Math.round(s/86400)+'d';
};
const chainCls = c=>(c||'').toLowerCase()==='solana'||c==='sol'?'chain-sol':c==='bnb'||c==='bsc'?'chain-bnb':'';

const _KEY='__API_KEY__';
async function api(path){
  try{const r=await fetch(path,{headers:_KEY?{'X-API-Key':_KEY}:{}});return r.ok?r.json():null}catch{return null}
}

async function refresh(){
  const [pf,open,hist,sigs,macro,regime]=await Promise.all([
    api('/api/portfolio'),
    api('/api/trades/open'),
    api('/api/trades/history?limit=30'),
    api('/api/signals/recent?limit=40'),
    api('/api/prediction-markets/risk'),
    api('/api/regime'),
  ]);

  // ── Stats ──
  if(pf&&!pf.status){
    const p=pf.all_time_pnl||0,pp=pf.all_time_pnl_pct||0,dp=pf.daily_pnl||0;
    $('s-total').textContent=fmtShort(pf.total_value);
    $('s-cash').textContent=fmtShort(pf.cash_balance);
    $('s-invested').textContent=fmtShort(pf.total_invested);
    const pe=$('s-pnl');
    pe.textContent=sign(p)+fmtShort(p);
    pe.className='stat-value '+(p>0?'green':p<0?'red':'muted');
    $('s-pnl-pct').textContent=sign(pp)+fmt2(pp)+'%';
    const de=$('s-daily');
    de.textContent=sign(dp)+fmtShort(dp);
    de.className='stat-value '+(dp>0?'green':dp<0?'red':'muted');
    const wr=pf.win_rate||0;
    $('s-wr').textContent=fmt2(wr,1)+'%';
    $('s-wr').className='stat-value '+(wr>=50?'green':wr>0?'yellow':'muted');
    $('s-wl').textContent=(pf.win_count||0)+'W / '+((pf.total_closed_trades||0)-(pf.win_count||0))+'L';
    $('s-pos').textContent=pf.open_positions||0;
  }

  // ── Open positions ──
  const ob=$('open-body');
  $('open-count').textContent=open?open.length:0;
  if(open&&open.length){
    ob.innerHTML=open.map(t=>`
      <div class="pos-row">
        <div class="pos-info">
          <span class="sym">${t.symbol||'???'}</span>
          <span class="chain ${chainCls(t.chain)}">${(t.chain||'').toUpperCase()}</span>
          <div class="pos-meta">opened ${timeAgo(t.opened_at)} ago</div>
        </div>
        <div class="pos-right">
          <div class="pos-size">${fmtUSD(t.size_usd)}</div>
          <div class="pos-entry">entry ${fmtPrice(t.entry_price)}</div>
        </div>
      </div>`).join('');
  } else {
    ob.innerHTML='<div class="pos-row" style="justify-content:center;color:var(--dim);font-style:italic">No open positions</div>';
  }

  // ── Closed trades ──
  const cb=$('closed-body');
  $('closed-count').textContent=hist?hist.length:0;
  if(hist&&hist.length){
    cb.innerHTML=hist.map(t=>{
      const p=t.pnl_usd||0,pp=t.pnl_pct||0;
      const ec='exit-'+(t.exit_reason||'manual').replace(/ /g,'_');
      return`<tr>
        <td><span class="sym">${t.symbol||'???'}</span><span class="chain ${chainCls(t.chain)}">${(t.chain||'').toUpperCase()}</span></td>
        <td style="font-family:'JetBrains Mono',monospace;font-size:11px">${fmtPrice(t.entry_price)}</td>
        <td style="font-family:'JetBrains Mono',monospace;font-size:11px">${fmtPrice(t.exit_price)}</td>
        <td><span class="${p>=0?'pnl-pos':'pnl-neg'}">${sign(p)}${fmtUSD(p)}</span><span class="pct-badge ${pp>=0?'pct-pos':'pct-neg'}">${sign(pp)}${fmt2(pp)}%</span></td>
        <td><span class="exit ${ec}">${t.exit_reason||'—'}</span></td>
        <td style="color:var(--muted)">${held(t.opened_at,t.closed_at)}</td>
      </tr>`;
    }).join('');
  } else {
    cb.innerHTML='<tr class="empty-row"><td colspan="6">No closed trades yet</td></tr>';
  }

  // ── Signals ──
  const sf=$('sig-body');
  const bullSigs=sigs?sigs.filter(s=>s.signal_type==='bullish'||s.signal_type==='bearish').slice(0,30):[];
  $('sig-count').textContent=bullSigs.length;
  if(bullSigs.length){
    sf.innerHTML=bullSigs.map(s=>{
      const st=s.signal_type||'neutral';
      const str=Math.min(100,Math.max(0,s.strength||0));
      const barCls=st==='bullish'?'sig-bar-bull':st==='bearish'?'sig-bar-bear':'';
      return`<div class="signal-item">
        <div class="sig-top">
          <span class="sig-sym">${s.symbol||'GLOBAL'}</span>
          <span class="sig-time">${timeAgo(s.created_at)} ago</span>
        </div>
        <div class="sig-mid">
          <span class="sig-agent">${(s.agent||'').replace('_agent','').replace('_',' ')}</span>
          <span class="sig-type sig-${st}">${st}</span>
          <span style="font-size:10px;color:var(--dim)">${Math.round(str)}</span>
        </div>
        <div class="sig-bar-wrap"><div class="sig-bar ${barCls}" style="width:${str}%"></div></div>
        ${s.reason?`<div class="sig-reason">${s.reason.slice(0,80)}</div>`:''}
      </div>`;
    }).join('');
  } else {
    sf.innerHTML='<div class="signal-item"><div class="sig-reason" style="text-align:center;color:var(--dim)">No signals yet — agents are scanning</div></div>';
  }

  // ── Macro ──
  if(macro&&!macro.status){
    const mode=macro.safe_mode||'normal';
    $('m-mode').textContent=mode.toUpperCase();
    $('m-mode').className='macro-val '+(mode==='normal'?'green':mode==='cautious'?'yellow':'red');
    $('m-sent').textContent=(macro.sentiment||'—').toUpperCase();
    $('m-mod').textContent=(macro.confidence_modifier>=0?'+':'')+macro.confidence_modifier;
    $('m-mod').className='macro-val '+(macro.confidence_modifier>0?'green':macro.confidence_modifier<0?'red':'muted');
    $('m-mult').textContent=Math.round((macro.position_multiplier||1)*100)+'%';
  }
  if(regime&&!regime.status){
    const r=regime.regime||'unknown';
    $('m-regime').textContent=r.toUpperCase().replace(/_/g,' ');
    $('m-regime').className='macro-val '+(r.includes('bull')?'green':r.includes('bear')?'red':'yellow');
    const rp=$('regime-pill');
    rp.textContent=r.replace(/_/g,' ').toUpperCase();
    rp.style.background=r.includes('bull')?'var(--green-dim)':r.includes('bear')?'var(--red-dim)':'var(--yellow-dim)';
    rp.style.color=r.includes('bull')?'var(--green)':r.includes('bear')?'var(--red)':'var(--yellow)';
  }
  if(macro&&!macro.status){
    const sent=macro.sentiment||'neutral';
    $('sentiment-pill').textContent=sent.toUpperCase();
  }
}

// Clock
setInterval(()=>{ $('clock').textContent=new Date().toUTCString().slice(17,25)+' UTC'; },1000);

// Auto-refresh every 15s
setInterval(refresh,15000);
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
