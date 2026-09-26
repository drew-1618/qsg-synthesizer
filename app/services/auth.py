"""Optional HTTP Basic Auth gate for the dashboard and API.

Mirrors the project's existing optional-credential pattern (see
ANTHROPIC_API_KEY in app/services/orchestrator.py): if APP_USERNAME and
APP_PASSWORD aren't both set, the app stays open, which is what local dev
and the test suite rely on. Set both before deploying anywhere reachable
beyond localhost.
"""

import os
import secrets
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

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


def verify_credentials(credentials: Optional[HTTPBasicCredentials]) -> None:
    configured = _configured_credentials()
    if configured is None:
        return

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required.",
        headers={"WWW-Authenticate": "Basic"},
    )
    if credentials is None:
        raise unauthorized

    expected_username, expected_password = configured
    username_ok = secrets.compare_digest(credentials.username, expected_username)
    password_ok = secrets.compare_digest(credentials.password, expected_password)
    if not (username_ok and password_ok):
        raise unauthorized


def require_auth(
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
) -> None:
    verify_credentials(credentials)
