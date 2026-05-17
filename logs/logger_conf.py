import sys
from logging import config as logging_config

import yaml


def _ensure_utf8_stdio() -> None:
    """Windows cp1251 konsolida emoji/unicode log xatosini oldini oladi."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not reconfigure:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logging(logging_config_path: str) -> None:
    _ensure_utf8_stdio()

    with open(logging_config_path, "r", encoding="utf-8") as stream:
        logging_config_yaml = yaml.load(stream, Loader=yaml.FullLoader)

    logging_config.dictConfig(logging_config_yaml)