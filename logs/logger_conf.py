from logging import config as logging_config

import yaml


def setup_logging(logging_config_path: str) -> None:
    with open(logging_config_path, "r", encoding="utf-8") as stream:
        logging_config_yaml = yaml.load(stream, Loader=yaml.FullLoader)

    if not isinstance(logging_config_yaml, dict):
        raise ValueError(
            f"Logging config must be a YAML mapping (got {type(logging_config_yaml).__name__!r}). "
            f"Is {logging_config_path!r} empty or invalid?"
        )

    logging_config.dictConfig(logging_config_yaml)