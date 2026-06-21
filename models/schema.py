from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Token(Base):
    __tablename__ = "tokens"
    id = Column(Integer, primary_key=True)
    address = Column(String, unique=True, nullable=False)
    symbol = Column(String)
    name = Column(String)
    chain = Column(String)  # solana | bnb
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow)
    vettings = relationship("TokenVetting", back_populates="token")
    signals = relationship("Signal", back_populates="token")


class TokenVetting(Base):
    __tablename__ = "token_vettings"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    checked_at = Column(DateTime, default=datetime.utcnow)
    contract_safe = Column(Boolean)
    is_honeypot = Column(Boolean)
    liquidity_usd = Column(Float)
    holder_concentration_pct = Column(Float)
    contract_age_hours = Column(Float)
    overall_pass = Column(Boolean)
    fail_reasons = Column(Text)  # JSON list of strings
    token = relationship("Token", back_populates="vettings")


class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    added_at = Column(DateTime, default=datetime.utcnow)
    added_by = Column(String)
    status = Column(String, default="watching")  # watching | promoted | rejected
    notes = Column(Text)


class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    agent_name = Column(String)
    signal_type = Column(String)  # bullish | bearish | neutral | warning
    strength = Column(Float)      # 0-100
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    token = relationship("Token", back_populates="signals")


class TradeIdea(Base):
    __tablename__ = "trade_ideas"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    confidence_score = Column(Float)   # 0-100
    agents_bullish = Column(Integer)
    score_breakdown = Column(Text)     # JSON
    direction = Column(String)         # buy | sell
    suggested_size_pct = Column(Float)
    status = Column(String, default="pending")  # pending | approved | rejected | executed


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    trade_idea_id = Column(Integer, ForeignKey("trade_ideas.id"), nullable=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    is_paper = Column(Boolean, default=True)
    direction = Column(String)          # buy | sell
    entry_price = Column(Float)
    exit_price = Column(Float, nullable=True)
    size_usd = Column(Float)
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    pnl_usd = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    status = Column(String, default="open")  # open | closed | cancelled
    exit_reason = Column(String, nullable=True)  # take_profit | stop_loss | manual | timeout


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    id = Column(Integer, primary_key=True)
    from_agent = Column(String)
    to_agent = Column(String)
    message_type = Column(String)
    payload = Column(Text)  # JSON
    sent_at = Column(DateTime, default=datetime.utcnow)
    processed = Column(Boolean, default=False)


class Performance(Base):
    __tablename__ = "performance"
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, default=datetime.utcnow)
    is_paper = Column(Boolean, default=True)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    total_pnl_usd = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    best_trade_pnl = Column(Float, nullable=True)
    worst_trade_pnl = Column(Float, nullable=True)
    portfolio_value = Column(Float)


class Lesson(Base):
    __tablename__ = "lessons"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=True)
    lesson_type = Column(String)  # win | loss | near_miss
    what_worked = Column(Text, nullable=True)
    what_failed = Column(Text, nullable=True)
    rule_update = Column(Text, nullable=True)
    applied = Column(Boolean, default=False)


# ── Prediction Market Intelligence Engine ─────────────────────────────────────

class PredictionMarket(Base):
    __tablename__ = "prediction_markets"
    id = Column(Integer, primary_key=True)
    market_id = Column(String, unique=True, nullable=False)  # Polymarket ID
    question = Column(Text, nullable=False)
    category = Column(String)
    market_type = Column(String)   # crypto | macro | regulatory | other
    end_date = Column(String, nullable=True)
    volume = Column(Float, default=0.0)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow)
    probabilities = relationship("MarketProbability", back_populates="market")
    events = relationship("MarketEvent", back_populates="market")


class MarketProbability(Base):
    __tablename__ = "market_probabilities"
    id = Column(Integer, primary_key=True)
    market_id = Column(String, ForeignKey("prediction_markets.market_id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    yes_probability = Column(Float, nullable=False)
    volume = Column(Float, default=0.0)
    market = relationship("PredictionMarket", back_populates="probabilities")


class MarketEvent(Base):
    __tablename__ = "market_events"
    id = Column(Integer, primary_key=True)
    market_id = Column(String, ForeignKey("prediction_markets.market_id"), nullable=False)
    detected_at = Column(DateTime, default=datetime.utcnow)
    event_type = Column(String)        # probability_shift | approaching_resolution
    old_probability = Column(Float)
    new_probability = Column(Float)
    change = Column(Float)
    magnitude = Column(String)         # significant | major
    description = Column(Text)
    market = relationship("PredictionMarket", back_populates="events")


class MacroRiskState(Base):
    __tablename__ = "macro_risk_states"
    id = Column(Integer, primary_key=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    sentiment = Column(String)          # bullish | bearish | neutral | risk_off
    sentiment_score = Column(Float)
    confidence_modifier = Column(Integer)
    safe_mode = Column(String)          # normal | cautious | safe
    position_multiplier = Column(Float)
    risk_trigger_count = Column(Integer, default=0)


# ── Phase 3: Intelligence & Portfolio ────────────────────────────────────────

class PaperPortfolio(Base):
    __tablename__ = "paper_portfolio"
    id = Column(Integer, primary_key=True)
    updated_at = Column(DateTime, default=datetime.utcnow)
    cash_balance = Column(Float, nullable=False)
    total_invested = Column(Float, default=0.0)
    total_value = Column(Float, nullable=False)
    all_time_pnl = Column(Float, default=0.0)
    daily_pnl = Column(Float, default=0.0)
    trade_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)


class WalletScore(Base):
    __tablename__ = "wallet_scores"
    id = Column(Integer, primary_key=True)
    address = Column(String, unique=True, nullable=False)
    chain = Column(String)
    label = Column(String, nullable=True)         # smart_degen | kol | sniper | elite | manual
    source = Column(String, default="manual")      # gmgn | birdeye | manual
    win_rate = Column(Float, default=0.0)
    win_rate_7d = Column(Float, default=0.0)
    avg_return_pct = Column(Float, default=0.0)
    trade_count = Column(Integer, default=0)
    trade_count_7d = Column(Integer, default=0)
    realized_pnl_7d = Column(Float, default=0.0)  # USD profit in last 7 days
    avg_trade_size_usd = Column(Float, default=0.0)
    last_active = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)
    score = Column(Float, default=50.0)            # 0-100 computed quality score
    first_seen = Column(DateTime, default=datetime.utcnow)


class SmartMoneyBuy(Base):
    """
    Log of confirmed smart money buys on watchlist tokens.
    Enables coordinated buy detection and LearningAgent post-mortems.
    """
    __tablename__ = "smart_money_buys"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=False)
    wallet_address = Column(String, nullable=False)
    wallet_label = Column(String, nullable=True)   # smart_degen | kol | elite
    wallet_score = Column(Float, default=50.0)
    source = Column(String)                        # gmgn | helius | birdeye
    buy_amount_usd = Column(Float, nullable=True)
    detected_at = Column(DateTime, default=datetime.utcnow)
    tx_signature = Column(String, nullable=True)
    is_holding = Column(Boolean, default=True)     # still holding at detection time


# ── Indexes for time-series query performance ─────────────────────────────────
# Without these, dashboard queries do full table scans once rows reach ~100k+

Index("ix_market_probabilities_market_id",  MarketProbability.market_id)
Index("ix_market_probabilities_recorded_at", MarketProbability.recorded_at)
Index("ix_macro_risk_states_recorded_at",   MacroRiskState.recorded_at)
Index("ix_market_events_detected_at",       MarketEvent.detected_at)
Index("ix_signals_token_id",                Signal.token_id)
Index("ix_signals_created_at",              Signal.created_at)
Index("ix_trades_status",                   Trade.status)
Index("ix_trades_opened_at",                Trade.opened_at)
Index("ix_agent_messages_sent_at",          AgentMessage.sent_at)
Index("ix_token_vettings_token_id",         TokenVetting.token_id)
Index("ix_smart_money_buys_token_id",       SmartMoneyBuy.token_id)
Index("ix_smart_money_buys_detected_at",    SmartMoneyBuy.detected_at)
Index("ix_smart_money_buys_wallet",         SmartMoneyBuy.wallet_address)
Index("ix_wallet_scores_score",             WalletScore.score)
