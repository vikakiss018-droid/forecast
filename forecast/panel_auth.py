"""HTTP Basic Auth for dashboard and sensitive API routes."""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .paths import load_project_env

_security = HTTPBasic(auto_error=False)


def panel_auth_enabled() -> bool:
    load_project_env(force=True)
    return bool((os.getenv("PANEL_AUTH_PASSWORD") or "").strip())


def _expected_credentials() -> tuple[str, str]:
    load_project_env(force=True)
    user = (os.getenv("PANEL_AUTH_USER") or "admin").strip()
    password = (os.getenv("PANEL_AUTH_PASSWORD") or "").strip()
    return user, password


def verify_panel_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> bool:
    if not panel_auth_enabled():
        return True
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется логин и пароль",
            headers={"WWW-Authenticate": 'Basic realm="Forecast Panel"'},
        )
    expected_user, expected_password = _expected_credentials()
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        expected_user.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        expected_password.encode("utf-8"),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": 'Basic realm="Forecast Panel"'},
        )
    return True


PANEL_AUTH_DEPS = [Depends(verify_panel_auth)]
