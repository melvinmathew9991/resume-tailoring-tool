"""Transport-level behaviour: limits, throttling, CORS, auth and the contract.

None of this existed in the original, which ran a Flask dev server with
``CORS(app)`` on an endpoint that spawns a subprocess.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from resume_tailor.api.main import create_app
from resume_tailor.core.config import Settings

pytestmark = pytest.mark.api


class TestRequestShape:
    def test_malformed_json_is_400_not_500(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/match",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_wrong_content_type_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/match", content=b"jd_text=hello", headers={"Content-Type": "text/plain"}
        )
        assert response.status_code in {400, 415, 422}

    def test_empty_body_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/match", content=b"", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400

    def test_wrong_method_is_405(self, client: TestClient) -> None:
        assert client.get("/api/v1/match").status_code == 405

    def test_unknown_route_is_404_with_the_problem_shape(self, client: TestClient) -> None:
        response = client.get("/api/v1/nope")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "not_found"
        assert body["status"] == 404
        assert body["type"].startswith("https://")

    def test_every_error_uses_the_problem_media_type(self, client: TestClient) -> None:
        response = client.get("/api/v1/projects/nope")
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_request_id_is_echoed(self, client: TestClient) -> None:
        assert client.get("/health/live").headers["X-Request-ID"]

    def test_supplied_request_id_is_preserved(self, client: TestClient) -> None:
        response = client.get("/health/live", headers={"X-Request-ID": "abc123"})
        assert response.headers["X-Request-ID"] == "abc123"


class TestBodySizeLimit:
    def test_oversized_body_is_413(self, client: TestClient) -> None:
        payload = json.dumps({"jd_text": "x" * 2_000_000}).encode()
        response = client.post(
            "/api/v1/match", content=payload, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 413
        assert response.json()["code"] == "payload_too_large"

    def test_body_at_the_limit_is_accepted(self, settings: Settings, engine) -> None:
        small = settings.model_copy(update={"max_body_bytes": 2048})
        with TestClient(create_app(small, engine=engine)) as client:
            response = client.post("/api/v1/match", json={"jd_text": "x" * 1000})
            assert response.status_code == 200

    def test_an_oversized_body_is_caught_at_a_low_limit(self, settings: Settings, engine) -> None:
        """The declared-Content-Length path, at a limit well under the body.

        httpx sets Content-Length on a bytes body, so this exercises the header
        check rather than the streaming one -- the docstring here used to claim
        the opposite. The request that genuinely omits the header is in
        TestBodyLimitEnforcedWhileStreaming.
        """
        small = settings.model_copy(update={"max_body_bytes": 512})
        with TestClient(create_app(small, engine=engine)) as client:
            response = client.post(
                "/api/v1/match",
                content=json.dumps({"jd_text": "x" * 4000}).encode(),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 413


class TestRateLimiting:
    @pytest.fixture
    def throttled(self, settings: Settings, engine) -> Iterator[TestClient]:
        tight = settings.model_copy(
            update={"rate_limit_per_minute": 5, "generate_rate_limit_per_minute": 2}
        )
        with TestClient(create_app(tight, engine=engine)) as client:
            yield client

    def test_default_budget_is_enforced(self, throttled: TestClient) -> None:
        codes = [
            throttled.post("/api/v1/match", json={"jd_text": "python"}).status_code
            for _ in range(8)
        ]
        assert 429 in codes
        assert codes.count(200) == 5

    def test_generation_has_its_own_tighter_budget(self, throttled: TestClient) -> None:
        """Compilation costs orders of magnitude more than a read, so it must
        not share a bucket with them."""
        codes = [
            throttled.post(
                "/api/v1/resume/generate", json={"selected_project_keys": ["proj_a"]}
            ).status_code
            for _ in range(4)
        ]
        assert codes.count(200) == 2
        assert codes[-1] == 429

    def test_rate_limited_response_carries_retry_after(self, throttled: TestClient) -> None:
        for _ in range(6):
            response = throttled.post("/api/v1/match", json={"jd_text": "python"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) >= 1
        assert response.json()["code"] == "rate_limited"

    def test_health_checks_are_never_throttled(self, throttled: TestClient) -> None:
        """A liveness probe that gets rate limited takes the service down."""
        codes = {throttled.get("/health/live").status_code for _ in range(30)}
        assert codes == {200}


class TestCors:
    def test_allowed_origin_gets_cors_headers(self, client: TestClient) -> None:
        response = client.options(
            "/api/v1/match",
            headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:8501"

    def test_disallowed_origin_gets_no_grant(self, client: TestClient) -> None:
        """The API drives a subprocess; any open browser tab must not be able
        to reach it (defect C3, which shipped as `CORS(app)`)."""
        response = client.options(
            "/api/v1/match",
            headers={
                "Origin": "http://evil.invalid",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers.get("access-control-allow-origin") != "http://evil.invalid"

    def test_wildcard_is_never_configured(self, client: TestClient) -> None:
        response = client.get("/api/v1/projects", headers={"Origin": "http://localhost:8501"})
        assert response.headers.get("access-control-allow-origin") != "*"


class TestApiKey:
    @pytest.fixture
    def secured(self, settings: Settings, engine) -> Iterator[TestClient]:
        with TestClient(
            create_app(settings.model_copy(update={"api_key": "s3cret"}), engine=engine),
            raise_server_exceptions=False,
        ) as client:
            yield client

    def test_missing_key_is_401(self, secured: TestClient) -> None:
        assert secured.get("/api/v1/projects").status_code == 401

    def test_wrong_key_is_401(self, secured: TestClient) -> None:
        response = secured.get("/api/v1/projects", headers={"X-API-Key": "wrong"})
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    def test_correct_key_passes(self, secured: TestClient) -> None:
        response = secured.get("/api/v1/projects", headers={"X-API-Key": "s3cret"})
        assert response.status_code == 200

    def test_no_key_configured_means_no_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/projects").status_code == 200


class TestOpenApiContract:
    def test_document_is_served(self, client: TestClient) -> None:
        assert client.get("/openapi.json").status_code == 200

    def test_all_expected_routes_are_published(self, client: TestClient) -> None:
        """Snapshot of the public surface. A change here is a contract change
        and should be a deliberate decision, not a side effect."""
        paths = set(client.get("/openapi.json").json()["paths"])
        assert paths == {
            "/health/live",
            "/health/ready",
            "/api/v1/meta",
            "/api/v1/projects",
            "/api/v1/projects/{key}",
            "/api/v1/match",
            "/api/v1/resume/preview",
            "/api/v1/resume/generate",
            "/api/v1/resume/{document_id}",
        }

    def test_generate_response_documents_the_warning_field(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()["components"]["schemas"]
        assert "warning" in schema["GenerateResponse"]["properties"]
        assert "fits" in schema["GenerateResponse"]["properties"]

    def test_docs_pages_render(self, client: TestClient) -> None:
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200


class TestBodyLimitEnforcedWhileStreaming:
    """A chunked request can simply omit ``Content-Length``.

    This was the specific hole: the limiter buffered the whole body with
    ``await request.body()`` and measured it afterwards, so an unbounded upload
    was already resident in memory by the time it was refused. The docstring and
    the security doc both claimed the streaming check that was not there.
    """

    @pytest.fixture
    def tight(self, settings: Settings, engine: object) -> Iterator[TestClient]:
        app = create_app(settings.model_copy(update={"max_body_bytes": 2048}), engine=engine)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    @staticmethod
    def _chunks(payload: bytes, size: int = 256):
        for start in range(0, len(payload), size):
            yield payload[start : start + size]

    def test_declared_content_length_over_the_limit_is_413(self, tight: TestClient) -> None:
        response = tight.post("/api/v1/match", json={"jd_text": "x" * 5000})
        assert response.status_code == 413
        assert response.json()["code"] == "payload_too_large"

    def test_chunked_body_over_the_limit_is_413(self, tight: TestClient) -> None:
        """No Content-Length at all, so only the counting check can catch it."""
        payload = json.dumps({"jd_text": "x" * 5000}).encode()
        response = tight.post(
            "/api/v1/match",
            content=self._chunks(payload),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413, response.text
        assert response.json()["code"] == "payload_too_large"
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_chunked_body_under_the_limit_still_reaches_the_route(self, tight: TestClient) -> None:
        """The limiter must not eat the body it lets through."""
        payload = json.dumps({"jd_text": "python engineer"}).encode()
        response = tight.post(
            "/api/v1/match",
            content=self._chunks(payload, size=4),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["ranked_projects"]

    def test_a_body_at_the_limit_is_accepted(self, tight: TestClient) -> None:
        jd = "x" * (2048 - len(json.dumps({"jd_text": ""}).encode()))
        response = tight.post("/api/v1/match", json={"jd_text": jd})
        assert response.status_code == 200, response.text

    def test_get_requests_are_unaffected(self, tight: TestClient) -> None:
        assert tight.get("/api/v1/projects").status_code == 200


class TestRateLimiterDoesNotLeak:
    def test_quiet_clients_are_swept(self, settings: Settings, engine: object) -> None:
        """Pruning on the hot path only touches the key being used, so a client
        that makes one request and never returns used to stay in the dict for the
        life of the process."""
        from resume_tailor.api.middleware import RateLimitMiddleware

        limiter = RateLimitMiddleware(app=object(), default_per_minute=10, generate_per_minute=2)
        now = 1000.0
        for index in range(50):
            limiter._hits[(f"10.0.0.{index}", "default")].append(now)
        assert len(limiter._hits) == 50

        limiter._last_sweep = now
        limiter._sweep(now + 30.0)
        assert len(limiter._hits) == 50, "a sweep must not run more often than its interval"

        limiter._sweep(now + 3600.0)
        assert limiter._hits == {}

    def test_an_active_client_survives_a_sweep(self, settings: Settings) -> None:
        from resume_tailor.api.middleware import RateLimitMiddleware

        limiter = RateLimitMiddleware(app=object(), default_per_minute=10, generate_per_minute=2)
        now = 1000.0
        limiter._hits[("10.0.0.1", "default")].append(now)
        limiter._hits[("10.0.0.2", "default")].append(now - 600.0)
        limiter._last_sweep = now - 3600.0
        limiter._sweep(now)
        assert list(limiter._hits) == [("10.0.0.1", "default")]


class TestApiKeyComparison:
    """The comparison itself, as distinct from the policy tested above.

    ``secrets.compare_digest`` rather than ``!=``: a plain comparison returns on
    the first differing byte and leaks the shared-prefix length to anyone who can
    time the response, which is precisely the caller this key exists to keep out.
    """

    @pytest.fixture
    def secured(self, settings: Settings, engine: object) -> Iterator[TestClient]:
        app = create_app(settings.model_copy(update={"api_key": "s3cret"}), engine=engine)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    @pytest.mark.parametrize(
        "supplied",
        [
            "",
            "s",
            "s3cre",
            "s3cret ",
            "S3CRET",
            "s3cretlonger",
        ],
    )
    def test_every_wrong_key_is_401_never_500(self, secured: TestClient, supplied: str) -> None:
        response = secured.get("/api/v1/projects", headers={"X-API-Key": supplied})
        assert response.status_code == 401, response.text

    @pytest.mark.parametrize("raw", [b"cl\xe9", b"\xe9\xe8\xea", b"\xff" * 6])
    def test_a_non_ascii_key_is_401_not_500(self, secured: TestClient, raw: bytes) -> None:
        """Header bytes outside ASCII must not crash the comparison.

        Starlette decodes header values as latin-1, so a header can legitimately
        carry a str with characters above 127 -- and ``secrets.compare_digest``
        refuses a str containing any of them. Comparing as str would turn a
        failed auth attempt into an unhandled 500, a worse outcome than the
        timing leak the constant-time comparison exists to close. The header is
        sent as raw bytes because httpx will not encode a non-ASCII str into
        one, which is exactly why this case is easy to miss.
        """
        response = secured.get("/api/v1/projects", headers={"X-API-Key": raw})
        assert response.status_code == 401, response.text


class TestMalformedContentLength:
    def test_a_junk_content_length_header_does_not_bypass_the_limit(
        self, settings: Settings, engine
    ) -> None:
        """A header that will not parse must fall through to the counting check
        rather than being treated as a pass."""
        small = settings.model_copy(update={"max_body_bytes": 512})
        with TestClient(create_app(small, engine=engine), raise_server_exceptions=False) as client:
            payload = json.dumps({"jd_text": "x" * 4000}).encode()
            response = client.post(
                "/api/v1/match",
                content=payload,
                headers={"Content-Type": "application/json", "Content-Length": "not-a-number"},
            )
            assert response.status_code in (400, 413), response.text
            assert response.status_code < 500
