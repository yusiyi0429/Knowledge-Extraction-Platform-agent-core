"""Run the standalone workbench server."""

import os

from aiohttp import web

from .app import create_app
from .config import Settings


def main() -> None:
    # agent-core 的 INFO 级 LLM 事件可能包含完整消息；本工作台只保留警告和错误。
    from openjiuwen.core.common.logging.log_config import (
        configure_log_config,
        get_log_config_snapshot,
    )

    log_config = get_log_config_snapshot()
    logger_levels = dict(log_config.get("loggers", {}))
    logger_levels.update({name: {"level": "WARNING"} for name in ("llm", "prompt", "prompt_builder")})
    log_config["loggers"] = logger_levels
    configure_log_config(log_config)
    settings = Settings.from_env()
    test_model = None
    if os.environ.get("WORKBENCH_TEST_MODEL") == "deterministic":
        from .pipeline import DeterministicTestModel

        test_model = DeterministicTestModel()
    web.run_app(create_app(settings, test_model=test_model), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
