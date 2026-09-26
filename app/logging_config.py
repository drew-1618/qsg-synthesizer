"""Central logging configuration for the QSG Synthesizer service.

Configures the root logger once at startup; every other module gets its own
logger via `logging.getLogger(__name__)`, so log lines are traceable to
their source and propagate up to this single configured handler. Level is
controlled by LOG_LEVEL (default INFO) so it can be turned up for debugging
without a code change.
"""

import logging
import os


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
