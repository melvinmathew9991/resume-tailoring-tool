"""FastAPI dependencies.

The service is built once in the lifespan handler and stored on ``app.state``,
so every request shares one bank cache, one document store and one compile
semaphore. Reaching for a module-level global instead (what the original did
with ``_BANK_CACHE``) is what made that cache impossible to invalidate or test.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from resume_tailor.core.config import Settings, get_settings
from resume_tailor.core.errors import AppError
from resume_tailor.services.resume_service import ResumeService


class UnauthorizedError(AppError):
    code = "unauthorized"
    status_code = 401
    title = "Missing or invalid API key"


def get_service(request: Request) -> ResumeService:
    service: ResumeService = request.app.state.service
    return service


def get_app_settings(request: Request) -> Settings:
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    return settings


def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """No-op unless ``RT_API_KEY`` is configured.

    Off by default because this is a local single-user tool; available because
    "local tool" and "exposed on a LAN" are one ``--host 0.0.0.0`` apart.
    """
    settings = get_app_settings(request)
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise UnauthorizedError("a valid X-API-Key header is required")


ServiceDep = Annotated[ResumeService, Depends(get_service)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ApiKeyDep = Annotated[None, Depends(require_api_key)]
