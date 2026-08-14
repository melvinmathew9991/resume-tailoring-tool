"""Version 1 of the HTTP API.

Versioned from the start so the contract can evolve without breaking a running
UI. The OpenAPI document for this router is snapshotted in the test suite, so
an unintended breaking change fails CI rather than surprising a caller.
"""

from fastapi import APIRouter

from resume_tailor.api.v1 import health, match, meta, projects, resume

router = APIRouter()
router.include_router(health.router)
router.include_router(meta.router, prefix="/api/v1")
router.include_router(projects.router, prefix="/api/v1")
router.include_router(match.router, prefix="/api/v1")
router.include_router(resume.router, prefix="/api/v1")

__all__ = ["router"]
