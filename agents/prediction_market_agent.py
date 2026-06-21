import asyncio
from datetime import datetime, timezone

from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from prediction_markets.polymarket import get_active_markets, parse_market
from prediction_markets.event_detector import detect_probability_change, classify_market_type
from prediction_markets.sentiment import market_to_sentiment, aggregate_sentiment
from prediction_markets.confidence import calculate_modifier
from prediction_markets.safe_mode import SafeModeController
from models.schema import PredictionMarket, MarketProbability, MarketEvent, MacroRiskState


class PredictionMarketAgent(BaseAgent):
    def __init__(self, name, message_bus, session_factory, config):
        super().__init__(name, message_bus, session_factory, config)
        self.safe_mode = SafeModeController(config)
        self._last_probabilities: dict = {}

    async def run(self) -> None:
        if not self.config.get("prediction_markets", {}).get("enabled", True):
            self.logger.info("Prediction markets disabled in config — skipping")
            return

        self.logger.info("Fetching prediction market data from Polymarket...")
        loop = asyncio.get_running_loop()

        await rate_limit("gamma-api.polymarket.com")
        raw_markets = await loop.run_in_executor(None, lambda: get_active_markets(200))
        if not raw_markets:
            self.logger.warning("No prediction market data received")
            return

        self.logger.info(f"Processing {len(raw_markets)} active markets")

        relevant, risk_markets, sentiments = [], [], []

        for raw in raw_markets:
            market = parse_market(raw)
            market_type = classify_market_type(market["question"], self.config)
            market["market_type"] = market_type

            if market_type not in ("crypto", "macro", "regulatory"):
                continue

            relevant.append(market)
            self._upsert_market(market)
            self._record_probability(market)

            prev = self._last_probabilities.get(market["id"])
            threshold = self.config.get("prediction_markets", {}).get("significant_change_pct", 0.10)
            if prev is not None:
                event = detect_probability_change(prev, market["yes_probability"], threshold)
                if event:
                    await self._handle_event(market, event)

            self._last_probabilities[market["id"]] = market["yes_probability"]

            s = market_to_sentiment(market["question"], market["yes_probability"])
            s["market_id"] = market["id"]
            sentiments.append(s)

            if s["is_risk_framing"]:
                risk_markets.append(market)

        macro = aggregate_sentiment(sentiments)
        confidence_result = calculate_modifier(macro, self.config)
        safe_status = self.safe_mode.evaluate(risk_markets)
        self._record_macro_risk(macro, confidence_result, safe_status)

        self.logger.info(
            f"Macro: {macro['sentiment'].upper()} | "
            f"Score: {macro['score']:.1f} | "
            f"Confidence modifier: {confidence_result['modifier']:+d} | "
            f"Mode: {safe_status['mode'].upper()} | "
            f"Markets tracked: {len(relevant)}"
        )

        await self.publish("macro_sentiment", {
            "sentiment": macro,
            "confidence_modifier": confidence_result,
            "safe_mode": safe_status,
            "market_count": len(relevant),
        })

        if safe_status["mode"] != "normal":
            await self.publish("safe_mode_alert", {
                "mode": safe_status["mode"],
                "triggers": safe_status["active_triggers"],
                "position_multiplier": safe_status["position_multiplier"],
                "allows_memecoins": safe_status["allows_memecoins"],
            })

    async def _handle_event(self, market: dict, event: dict) -> None:
        self.logger.info(
            f"Probability shift: {market['question'][:70]} | "
            f"{event['old']:.1%} → {event['new']:.1%} ({event['change']:+.1%})"
        )
        self._record_event(market, event)
        await self.publish("market_event", {
            "market_id": market["id"],
            "question": market["question"],
            "market_type": market["market_type"],
            "event": event,
        })

    def _upsert_market(self, market: dict) -> None:
        try:
            with self.get_db() as db:
                row = db.query(PredictionMarket).filter_by(market_id=market["id"]).first()
                if not row:
                    db.add(PredictionMarket(
                        market_id=market["id"],
                        question=market["question"],
                        category=market.get("category", ""),
                        market_type=market["market_type"],
                        end_date=market.get("end_date"),
                        volume=market.get("volume", 0),
                    ))
                else:
                    row.volume = market.get("volume", 0)
                    row.last_updated = datetime.now(timezone.utc)
        except Exception as e:
            self.logger.error(f"DB upsert failed for market {market['id']}: {e}")

    def _record_probability(self, market: dict) -> None:
        try:
            with self.get_db() as db:
                db.add(MarketProbability(
                    market_id=market["id"],
                    yes_probability=market["yes_probability"],
                    volume=market.get("volume", 0),
                ))
        except Exception as e:
            self.logger.error(f"DB probability record failed: {e}")

    def _record_event(self, market: dict, event: dict) -> None:
        try:
            with self.get_db() as db:
                db.add(MarketEvent(
                    market_id=market["id"],
                    event_type="probability_shift",
                    old_probability=event["old"],
                    new_probability=event["new"],
                    change=event["change"],
                    magnitude=event["magnitude"],
                    description=f"{market['question'][:100]} | {event['change']:+.1%}",
                ))
        except Exception as e:
            self.logger.error(f"DB event record failed: {e}")

    def _record_macro_risk(self, sentiment: dict, confidence: dict, safe_mode: dict) -> None:
        try:
            with self.get_db() as db:
                db.add(MacroRiskState(
                    sentiment=sentiment["sentiment"],
                    sentiment_score=sentiment["score"],
                    confidence_modifier=confidence["modifier"],
                    safe_mode=safe_mode["mode"],
                    position_multiplier=safe_mode["position_multiplier"],
                    risk_trigger_count=safe_mode["trigger_count"],
                ))
        except Exception as e:
            self.logger.error(f"DB macro risk record failed: {e}")

    async def process_message(self, message: dict) -> None:
        pass
