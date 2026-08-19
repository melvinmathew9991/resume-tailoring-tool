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
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from resume_tailor.core.errors import PayloadTooLargeError, RateLimitedError
from resume_tailor.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

RequestHandler = Callable[[Request], Awaitable[Response]]

PROBLEM_CONTENT_TYPE = "application/problem+json"

#: The rate-limit window, and how often the limiter drops clients that have
#: gone quiet. Sweeping on a timer rather than on every request keeps the hot
#: path O(1) -- a scan of every known client on each call would make the
#: limiter itself the expensive part of a cheap endpoint.
_WINDOW_S = 60.0
_SWEEP_INTERVAL_S = 300.0


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


class BodySizeLimitMiddleware:
    """Reject oversized bodies, without buffering them first.

    Two checks, because either one alone has a hole: the declared
    ``Content-Length`` is refused up front and costs nothing, but a chunked
    request can simply omit that header, so the bytes are also counted as they
    arrive and the request is refused the moment the running total crosses the
    limit.

    **Pure ASGI, deliberately not a** :class:`BaseHTTPMiddleware`. That is not a
    style preference, it is the only way to do this correctly. Starlette's
    ``_CachedRequest`` documents the constraint: inside a ``dispatch`` method,
    calling ``request.body()`` buffers the *entire* body in memory before the
    handler can look at it, and calling ``request.stream()`` instead makes
    downstream see an empty body. So a ``BaseHTTPMiddleware`` size limiter has
    exactly one option -- buffer everything, then measure -- which means an
    unbounded chunked upload is fully resident in memory by the time the limit
    is applied. That is the specific attack this middleware exists to stop, and
    the previous implementation's docstring claimed to stop it while doing the
    opposite.

    Wrapping ``receive`` sidesteps the whole problem: bytes are counted as the
    application pulls them, and the request is cut off at the first chunk that
    crosses the limit. The bound is therefore ``max_bytes`` plus one transport
    chunk, not ``max_bytes`` exactly -- what is bounded is that the process
    never holds an attacker-chosen amount.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                announced = int(declared)
            except ValueError:
                pass  # malformed header; the counting check below still applies
            else:
                if announced > self._max_bytes:
                    response = problem_response(self._error(announced), request)
                    await response(scope, receive, send)
                    return

        received = 0
        over_limit = False

        async def counting_receive() -> Message:
            nonlocal received, over_limit
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    # A disconnect rather than an exception. Raising here does
                    # not work: this runs inside the application's own
                    # `await request.body()`, and FastAPI wraps *anything* that
                    # comes out of that call into "there was an error parsing
                    # the body" -- a 400 that names neither the real problem nor
                    # the limit. Signalling a disconnect unwinds the handler
                    # without reading another byte, and the real answer is sent
                    # below.
                    over_limit = True
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message: Message) -> None:
            # Once the limit is breached the application's own response is
            # whatever it made of the truncated body. Drop it; ours is the
            # answer the client gets.
            if not over_limit:
                await send(message)

        await self.app(scope, counting_receive, limited_send)

        if over_limit:
            response = problem_response(self._error(received), request)
            await response(scope, receive, send)

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
        self._last_sweep = time.monotonic()

    def _sweep(self, now: float) -> None:
        """Drop clients that have gone quiet.

        Pruning inside ``dispatch`` only ever touches the key being used, so a
        client that makes one request and never returns leaves its entry behind
        permanently. One entry per distinct address is nothing for the localhost
        tool this is, and an unbounded dict the moment the service is bound to a
        network -- which is the case every other control here is written for.
        """
        if now - self._last_sweep < _SWEEP_INTERVAL_S:
            return
        self._last_sweep = now
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] > _WINDOW_S]
        for key in stale:
            del self._hits[key]

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
        self._sweep(now)
        hits = self._hits[key]
        while hits and now - hits[0] > _WINDOW_S:
            hits.popleft()

        if len(hits) >= limit:
            retry_after = max(1, int(_WINDOW_S - (now - hits[0])))
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
