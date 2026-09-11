"""Version 1 of the HTTP API.

Versioned from the start so the contract can evolve without breaking a running
UI. The OpenAPI document for this router is snapshotted in the test suite, so
an unintended breaking change fails CI rather than surprising a caller.
"""

from fastapi import APIRouter

from resume_tailor.api.v1 import ats, health, knowledge, match, meta, projects, resume

router = APIRouter()
router.include_router(health.router)
router.include_router(meta.router, prefix="/api/v1")
router.include_router(projects.router, prefix="/api/v1")
router.include_router(match.router, prefix="/api/v1")
router.include_router(resume.router, prefix="/api/v1")
# The two standalone features. Separate routers, separate paths, no shared
# state with resume generation -- they read the same content files and nothing
# more, which is what keeps their workflows independent.
router.include_router(knowledge.router, prefix="/api/v1")
router.include_router(ats.router, prefix="/api/v1")

__all__ = ["router"]
