# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import logging
import re

import sentry_sdk
import structlog
from sentry_sdk.integrations.logging import LoggingIntegration

from parsec import __version__

_log_level = None


def get_log_level():
    global _log_level

    if not _log_level:
        _log_level = logging.getLogger().level
    return _log_level


def configure_logging(log_level=None, log_format=None, log_file=None, log_filter=None):
    global _log_level

    _log_level = None
    shared_processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
    ]

    if log_filter:
        log_filter = re.compile(log_filter)

        def dropper(logger, method_name, event_dict):
            if not log_filter.match(str(event_dict)):
                raise structlog.DropEvent
            return event_dict

        shared_processors.append(dropper)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            # structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    if not log_format:
        log_format = "JSON" if log_file else "CONSOLE"
    if log_format == "CONSOLE":
        formatter_renderer = structlog.dev.ConsoleRenderer
    elif log_format == "JSON":
        formatter_renderer = structlog.processors.JSONRenderer
    else:
        raise ValueError(f"Unknown log format `{log_format}`")

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=formatter_renderer(), foreign_pre_chain=shared_processors
    )

    if log_file:
        handler = logging.FileHandler(log_file)
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    if log_level is not None:
        root_logger.setLevel(log_level.upper())


def configure_sentry_logging(sentry_url):
    sentry_logging = LoggingIntegration(
        level=logging.WARNING,  # Capture warning and above as breadcrumbs
        event_level=logging.ERROR,  # Send errors as events
    )
    sentry_sdk.init(dsn=sentry_url, integrations=[sentry_logging], release=__version__)


def disable_sentry_logging():
    sentry_sdk.init(dsn=None)
