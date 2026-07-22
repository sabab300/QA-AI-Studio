"""
QA AI Studio
Enterprise Logging Framework
Version: 1.0
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from Core.config_manager import ConfigManager


class Logger:

    _logger = None

    def __init__(self):

        if Logger._logger is None:

            self._initialize()

    # --------------------------------------------------

    def _initialize(self):

        config = ConfigManager()

        log_directory = config.output_path / "Logs"

        log_directory.mkdir(
            parents=True,
            exist_ok=True
        )

        log_file = log_directory / "qa_ai_studio.log"

        logger = logging.getLogger("QA_AI_STUDIO")

        logger.setLevel(logging.INFO)

        if logger.handlers:

            Logger._logger = logger
            return

        formatter = logging.Formatter(

            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",

            "%Y-%m-%d %H:%M:%S"

        )

        # Console

        console_handler = logging.StreamHandler()

        console_handler.setFormatter(formatter)

        logger.addHandler(console_handler)

        # File

        file_handler = RotatingFileHandler(

            log_file,

            maxBytes=5 * 1024 * 1024,

            backupCount=5,

            encoding="utf-8"

        )

        file_handler.setFormatter(formatter)

        logger.addHandler(file_handler)

        Logger._logger = logger

    # --------------------------------------------------

    @classmethod
    def get_logger(cls):

        if cls._logger is None:

            Logger()

        return cls._logger