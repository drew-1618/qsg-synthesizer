"""Central logging configuration for the QSG Synthesizer service.

Configures the root logger once at startup; every other module gets its own
logger via `logging.getLogger(__name__)`, so log lines are traceable to
their source and propagate up to this single configured handler. Level is
controlled by LOG_LEVEL (default INFO) so it can be turned up for debugging
without a code change.

Output is one JSON object per line, so logs are easy to pipe into a log
aggregator later without a reformatting step.
"""

import json
import logging
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler])
