"""FastAPI dependencies.

The service is built once in :func:`resume_tailor.api.main.create_app` and
stored on ``app.state``, so every request shares one bank cache, one document
store and one compile semaphore. Reaching for a module-level global instead
(what the original did with ``_BANK_CACHE``) is what made that cache impossible
to invalidate or test.

Built in the factory rather than the lifespan handler on purpose -- a
lifespan-only setup runs under uvicorn but silently does not run under a
``TestClient`` used without a context manager, turning every route into an
``AttributeError`` on ``app.state``.
"""

from __future__ import annotations

import secrets
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
    # compare_digest, not `!=`. A plain comparison returns on the first
    # differing byte, which leaks the length of the shared prefix to anyone who
    # can time the response -- and the only reason this check exists at all is
    # the case where the service is reachable by someone who can.
    # Compared as bytes, not as str: `compare_digest` raises TypeError on a
    # str containing anything outside ASCII, and header values arrive
    # latin-1-decoded, so a header of "clé" would turn a failed auth attempt
    # into an unhandled 500.
    if x_api_key is None or not secrets.compare_digest(
        x_api_key.encode("utf-8"), settings.api_key.encode("utf-8")
    ):
        raise UnauthorizedError("a valid X-API-Key header is required")


ServiceDep = Annotated[ResumeService, Depends(get_service)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ApiKeyDep = Annotated[None, Depends(require_api_key)]
