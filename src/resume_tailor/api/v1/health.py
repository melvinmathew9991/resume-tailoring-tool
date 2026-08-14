"""Liveness and readiness.

Two endpoints rather than one, because they answer different questions and a
process manager needs both. ``/health/live`` says the process is running;
``/health/ready`` says it can actually do its job -- bank parses, profile
parses, and a PDF engine is present. The original's single ``/api/health``
returned ``{"status": "ok"}`` unconditionally, so a server with an unreadable
project bank and no LaTeX installed reported itself perfectly healthy.

Neither endpoint requires the API key: a health probe that needs a secret is a
health probe that gets disabled.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from resume_tailor import __version__
from resume_tailor.api.deps import ServiceDep
from resume_tailor.api.schemas import LivenessResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=LivenessResponse, summary="Liveness probe")
def live() -> LivenessResponse:
    return LivenessResponse(status="ok", app_version=__version__)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"description": "A dependency is unusable; see `checks`."}},
)
def ready(service: ServiceDep, response: Response) -> ReadinessResponse:
    report = service.readiness()
    if not report["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(ready=report["ready"], checks=report["checks"])
