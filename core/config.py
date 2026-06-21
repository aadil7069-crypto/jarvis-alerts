import os
import yaml
from dotenv import load_dotenv


def load_config(config_path: str = "config.yaml") -> dict:
    load_dotenv()
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)
