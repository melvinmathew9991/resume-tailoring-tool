"""Middleware: correlation, body size limits, and rate limiting.

All three are things the original had none of, on an endpoint that spawns a
subprocess. Together with the CORS allowlist in ``main.py`` they are what turn
"a Flask dev server with ``CORS(app)``" into something that can be pointed at a
network without flinching.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from resume_tailor.core.errors import PayloadTooLargeError, RateLimitedError
from resume_tailor.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

RequestHandler = Callable[[Request], Awaitable[Response]]

PROBLEM_CONTENT_TYPE = "application/problem+json"


def problem_response(
    exc: PayloadTooLargeError | RateLimitedError, request: Request
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_problem(instance=str(request.url.path)),
        media_type=PROBLEM_CONTENT_TYPE,
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, log the outcome, and echo the id in a header."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration_ms, 1),
            request_id=request_id,
        )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies before they are parsed.

    Checks the declared ``Content-Length`` first, then enforces the same limit
    while streaming, because a chunked request can simply omit the header.
    """

    def __init__(self, app: object, max_bytes: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    return problem_response(self._error(int(declared)), request)
            except ValueError:
                pass  # malformed header; the streaming check below still applies

        body = await request.body()
        if len(body) > self._max_bytes:
            return problem_response(self._error(len(body)), request)

        return await call_next(request)

    def _error(self, size: int) -> PayloadTooLargeError:
        return PayloadTooLargeError(
            f"request body is {size} bytes, over the {self._max_bytes} byte limit",
            limit_bytes=self._max_bytes,
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window-per-client limiter with a tighter budget for generation.

    Deliberately in-process and dependency-free: this is a single-user tool, so
    a shared Redis bucket would be ceremony. The point is that one runaway
    client (or one page with a retry loop) cannot queue a hundred TeX
    compilations.
    """

    def __init__(
        self,
        app: object,
        *,
        default_per_minute: int,
        generate_per_minute: int,
        generate_paths: tuple[str, ...] = ("/api/v1/resume/generate",),
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._default = default_per_minute
        self._generate = generate_per_minute
        self._generate_paths = generate_paths
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def _limit_for(self, path: str) -> tuple[str, int]:
        if any(path.startswith(prefix) for prefix in self._generate_paths):
            return "generate", self._generate
        return "default", self._default

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        if request.method == "OPTIONS" or request.url.path.startswith("/health"):
            return await call_next(request)

        bucket, limit = self._limit_for(request.url.path)
        client = request.client.host if request.client else "unknown"
        key = (client, bucket)

        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > 60.0:
            hits.popleft()

        if len(hits) >= limit:
            retry_after = max(1, int(60.0 - (now - hits[0])))
            logger.warning("http.rate_limited", path=request.url.path, bucket=bucket)
            response = problem_response(
                RateLimitedError(
                    f"rate limit of {limit} requests per minute exceeded for this endpoint",
                    limit=limit,
                    retry_after_s=retry_after,
                ),
                request,
            )
            response.headers["Retry-After"] = str(retry_after)
            return response

        hits.append(now)
        return await call_next(request)
