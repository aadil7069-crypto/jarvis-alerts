import logging
import os
from datetime import datetime


def setup_logger(config: dict) -> None:
    level_name = config.get("logging", {}).get("level", "INFO")
    level = getattr(logging, level_name, logging.INFO)

    os.makedirs("logs", exist_ok=True)
    log_file = os.path.join("logs", f"jarvis_{datetime.now().strftime('%Y%m%d')}.log")

    fmt = "%(asctime)s | %(levelname)-8s | %(name)-35s | %(message)s"

    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
