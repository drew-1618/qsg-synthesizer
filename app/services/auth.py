"""Optional HTTP Basic Auth gate for the dashboard and API.

Mirrors the project's existing optional-credential pattern (see
ANTHROPIC_API_KEY in app/services/orchestrator.py): if APP_USERNAME and
APP_PASSWORD aren't both set, the app stays open, which is what local dev
and the test suite rely on. Set both before deploying anywhere reachable
beyond localhost.
"""

import logging
import os
import secrets
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logger = logging.getLogger(__name__)

# Shared HTTPBasic scheme: used both as a FastAPI dependency (via require_auth,
# for normal routes) and called directly against a raw Request (in main.py's
# static-file middleware, which sits outside FastAPI's dependency injection).
security = HTTPBasic(auto_error=False)


def _configured_credentials() -> Optional[Tuple[str, str]]:
    username = os.environ.get("APP_USERNAME")
    password = os.environ.get("APP_PASSWORD")
    if not username or not password:
        return None
    return username, password


def verify_credentials(
    credentials: Optional[HTTPBasicCredentials], client_host: str = "unknown"
) -> None:
    configured = _configured_credentials()
    if configured is None:
        return

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required.",
        headers={"WWW-Authenticate": "Basic"},
    )
    if credentials is None:
        logger.warning("Rejected request from %s: no credentials provided", client_host)
        raise unauthorized

    expected_username, expected_password = configured
    username_ok = secrets.compare_digest(credentials.username, expected_username)
    password_ok = secrets.compare_digest(credentials.password, expected_password)
    if not (username_ok and password_ok):
        logger.warning(
            "Rejected request from %s: invalid credentials (user=%r)",
            client_host,
            credentials.username,
        )
        raise unauthorized


def require_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
) -> None:
    verify_credentials(
        credentials, client_host=request.client.host if request.client else "unknown"
    )
