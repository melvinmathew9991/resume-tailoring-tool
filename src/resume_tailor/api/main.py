"""FastAPI application factory.

Everything expensive or stateful (bank cache, profile cache, PDF engine,
document store, compile semaphore) is built once in the lifespan handler and
attached to ``app.state``. Tests build their own app with an injected fake
engine through :func:`create_app`, which is why the whole API suite runs in
milliseconds with no TeX toolchain present.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from resume_tailor import __version__
from resume_tailor.api.middleware import (
    PROBLEM_CONTENT_TYPE,
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from resume_tailor.api.v1 import router as v1_router
from resume_tailor.core.config import Settings, get_settings
from resume_tailor.core.errors import AppError, InvalidInputError, NotFoundError
from resume_tailor.core.logging import configure_logging, get_logger
from resume_tailor.data.bank_repo import BankRepository
from resume_tailor.data.profile_repo import ProfileRepository
from resume_tailor.render.engines.base import PdfEngine
from resume_tailor.render.engines.registry import select_engine
from resume_tailor.services.resume_service import ResumeService

logger = get_logger(__name__)

DESCRIPTION = """
Tailor a resume against a job description, from a bank of **pre-verified**
project content.

This service never generates new resume prose. Every bullet it can place on a
page was written and fact-checked by a human in advance; the service selects,
orders and formats them. That boundary is deliberate: a tool that invents
resume claims cannot verify they are true.

**The page-fit guarantee.** `POST /api/v1/resume/generate` compiles at
successively smaller font sizes until the document fits the requested page
limit. If it never fits, you still get the PDF, but `fits` is `false` and
`warning` explains why. It will never quietly hand back an over-length resume.
"""


def build_service(settings: Settings, engine: PdfEngine | None = None) -> ResumeService:
    return ResumeService(
        settings=settings,
        bank_repo=BankRepository(settings.bank_path),
        profile_repo=ProfileRepository(settings.profile_path),
        engine=engine or select_engine(settings),
    )


def create_app(settings: Settings | None = None, engine: PdfEngine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    # Built eagerly rather than inside the lifespan handler. A lifespan-only
    # setup works under uvicorn but silently does not run when a TestClient is
    # used without a context manager, which turns every route into a confusing
    # AttributeError on app.state. Constructing here costs nothing (no I/O
    # happens until the first request) and removes the trap.
    service = build_service(settings, engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        report = app.state.service.readiness()
        logger.info(
            "app.startup",
            version=__version__,
            environment=settings.environment,
            engine=report["checks"]["pdf_engine"]["name"],
            ready=report["ready"],
        )
        if not report["ready"]:
            # Loud, but not fatal: /health/ready reports the detail, and a
            # missing PDF engine still leaves /match and /resume/preview fully
            # usable. Refusing to boot would remove working functionality.
            logger.warning("app.not_ready", checks=report["checks"])
        yield
        logger.info("app.shutdown")

    app = FastAPI(
        title="Resume Tailoring Tool",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.settings = settings
    app.state.service = service

    # Order matters: the outermost middleware runs first. Correlation ids wrap
    # everything so even a rejected request is traceable; the size check runs
    # before the rate limiter so a huge body is dropped without consuming quota.
    app.add_middleware(
        RateLimitMiddleware,
        default_per_minute=settings.rate_limit_per_minute,
        generate_per_minute=settings.generate_rate_limit_per_minute,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # an allowlist, never "*" (defect C3)
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Page-Count", "Content-Disposition"],
    )

    _register_error_handlers(app)
    app.include_router(v1_router)
    return app


def _problem(status_code: int, content: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=content, media_type=PROBLEM_CONTENT_TYPE)


def _register_error_handlers(app: FastAPI) -> None:
    """One error shape for every failure, including the ones we did not expect."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        log = logger.warning if exc.status_code < 500 else logger.error
        log("api.error", code=exc.code, status=exc.status_code, detail=exc.detail)
        return _problem(exc.status_code, exc.to_problem(instance=request.url.path))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'][1:]) or '<body>'}: {error['msg']}"
            for error in exc.errors()[:10]
        )
        error = InvalidInputError(details or "request body failed validation")
        return _problem(error.status_code, error.to_problem(instance=request.url.path))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        error: AppError = (
            NotFoundError(str(exc.detail))
            if exc.status_code == 404
            else InvalidInputError(str(exc.detail))
        )
        error.status_code = exc.status_code
        return _problem(exc.status_code, error.to_problem(instance=request.url.path))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # A bug, not an expected failure. Log the traceback; tell the client
        # nothing that could leak a path or a stack frame.
        logger.exception("api.unhandled", path=request.url.path, error=str(exc))
        error = AppError("an unexpected internal error occurred; see server logs")
        return _problem(500, error.to_problem(instance=request.url.path))


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Build the module-level ``app`` on first access, not on import.

    ``uvicorn resume_tailor.api.main:app`` resolves this attribute and gets a
    fully configured application. Tests that call :func:`create_app` with their
    own settings and engine never trigger it, so importing this module does not
    probe the filesystem for a TeX binary or emit a misleading
    "no PDF engine found" warning for an app nobody is going to run.
    """
    global _app
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if _app is None:
        _app = create_app()
    return _app


def run() -> None:  # pragma: no cover - console-script entry point
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "resume_tailor.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
    )
