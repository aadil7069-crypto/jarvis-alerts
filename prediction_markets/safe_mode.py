import logging

logger = logging.getLogger("jarvis.prediction_markets.safe_mode")

NORMAL = "normal"
CAUTIOUS = "cautious"
SAFE = "safe"


class SafeModeController:
    """
    Monitors prediction market risk signals and manages exposure reduction
    when macro risk is elevated.

    Three levels:
      normal   — full position sizes, all tokens allowed
      cautious — positions at 50%, no new memecoins
      safe     — positions at 25%, no memecoins, alert sent to user
    """

    def __init__(self, config: dict):
        pm = config.get("prediction_markets", {})
        self.risk_off_threshold = pm.get("risk_off_threshold", 0.60)
        self.extreme_threshold = pm.get("extreme_risk_threshold", 0.80)
        self.position_multiplier_cautious = pm.get("safe_mode_position_multiplier", 0.50)
        self.block_memecoins = pm.get("block_memecoins_in_safe_mode", True)
        self.mode = NORMAL
        self.active_triggers: list = []

    def evaluate(self, risk_markets: list) -> dict:
        """
        Given risk-framing markets, determine the safe mode level.
        risk_markets: list of parsed market dicts where is_risk_framing is True.
        """
        extreme, cautious = [], []

        for m in risk_markets:
            prob = m.get("yes_probability", 0.0)
            q = m.get("question", "")
            if prob >= self.extreme_threshold:
                extreme.append({"question": q, "probability": prob})
            elif prob >= self.risk_off_threshold:
                cautious.append({"question": q, "probability": prob})

        prev_mode = self.mode

        if extreme:
            self.mode = SAFE
            self.active_triggers = extreme
            logger.warning(f"SAFE MODE — {len(extreme)} extreme risk trigger(s)")
        elif cautious:
            self.mode = CAUTIOUS
            self.active_triggers = cautious
            logger.info(f"CAUTIOUS MODE — {len(cautious)} elevated risk trigger(s)")
        else:
            if prev_mode != NORMAL:
                logger.info(f"Risk cleared — returning to NORMAL mode")
            self.mode = NORMAL
            self.active_triggers = []

        return self.status()

    def get_position_multiplier(self) -> float:
        if self.mode == SAFE:
            return self.position_multiplier_cautious * 0.5   # 25% of normal
        if self.mode == CAUTIOUS:
            return self.position_multiplier_cautious          # 50% of normal
        return 1.0

    def allows_memecoins(self) -> bool:
        return not (self.block_memecoins and self.mode in (SAFE, CAUTIOUS))

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "position_multiplier": self.get_position_multiplier(),
            "allows_memecoins": self.allows_memecoins(),
            "active_triggers": self.active_triggers,
            "trigger_count": len(self.active_triggers),
        }
