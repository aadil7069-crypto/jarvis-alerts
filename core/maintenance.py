import logging
from datetime import datetime, timedelta, timezone

from models.schema import MarketProbability, MacroRiskState, AgentMessage, Signal

logger = logging.getLogger("jarvis.maintenance")


def prune_old_data(session_factory, retention_days: int = 30) -> None:
    """
    Delete time-series records older than retention_days.
    Run once daily via the ReportingAgent to prevent unbounded table growth.

    At 200 prediction markets × 5-min interval, market_probabilities grows
    ~57k rows/day. Without pruning, queries slow to a crawl within weeks.
    """
    db = session_factory()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        mp = db.query(MarketProbability).filter(MarketProbability.recorded_at < cutoff).delete()
        mr = db.query(MacroRiskState).filter(MacroRiskState.recorded_at < cutoff).delete()
        am = db.query(AgentMessage).filter(AgentMessage.sent_at < cutoff).delete()
        sig = db.query(Signal).filter(Signal.created_at < cutoff).delete()

        db.commit()
        logger.info(
            f"Pruned records older than {retention_days}d: "
            f"{mp} probabilities, {mr} risk states, {am} messages, {sig} signals"
        )
    except Exception as e:
        logger.error(f"Data pruning failed: {e}")
        db.rollback()
    finally:
        db.close()
