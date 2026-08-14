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

    def test_lying_content_length_is_still_caught(self, settings: Settings, engine) -> None:
        """A chunked request can omit Content-Length entirely, so the streamed
        body is measured too rather than trusting the header."""
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
