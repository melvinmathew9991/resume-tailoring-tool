"""Endpoint behaviour, driven through the real app with the fake PDF engine."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api


class TestHealth:
    def test_live_is_cheap_and_always_ok(self, client: TestClient) -> None:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_ready_reports_the_engine_in_use(self, client: TestClient) -> None:
        body = client.get("/health/ready").json()
        assert body["ready"] is True
        assert body["checks"]["pdf_engine"]["name"] == "fake"
        assert body["checks"]["pdf_engine"]["produces_real_pdfs"] is False

    def test_ready_is_503_when_a_dependency_is_broken(self, client: TestClient, settings) -> None:
        """The original's single health endpoint answered "ok" unconditionally,
        so a server with an unreadable bank reported itself healthy."""
        settings.bank_path.write_text("{ broken", encoding="utf-8")
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False

    def test_live_stays_green_when_ready_is_red(self, client: TestClient, settings) -> None:
        settings.bank_path.write_text("{ broken", encoding="utf-8")
        assert client.get("/health/live").status_code == 200

    def test_health_needs_no_api_key(self, settings, engine) -> None:
        from resume_tailor.api.main import create_app

        secured = settings.model_copy(update={"api_key": "secret"})
        with TestClient(create_app(secured, engine=engine)) as secured_client:
            assert secured_client.get("/health/live").status_code == 200


class TestMeta:
    def test_publishes_limits_and_defaults(self, client: TestClient) -> None:
        body = client.get("/api/v1/meta").json()
        assert body["max_pages_limit"] == 10
        assert body["max_selected_projects"] > 0
        assert len(body["font_ladder"]) == 5
        assert body["bank_version"]

    def test_default_summary_is_display_text(self, client: TestClient) -> None:
        assert "\\textbf" not in client.get("/api/v1/meta").json()["default_summary"]


class TestProjects:
    def test_lists_visible_projects_only(self, client: TestClient) -> None:
        body = client.get("/api/v1/projects").json()
        keys = [project["key"] for project in body["projects"]]
        assert "proj_hidden" not in keys
        assert body["count"] == len(keys)

    def test_include_hidden_is_opt_in(self, client: TestClient) -> None:
        body = client.get("/api/v1/projects", params={"include_hidden": True}).json()
        assert "proj_hidden" in [project["key"] for project in body["projects"]]

    def test_titles_are_display_text_not_raw_latex(self, client: TestClient) -> None:
        """Regression: raw \\& and \\_ leaking into JSON meant for humans."""
        for project in client.get("/api/v1/projects").json()["projects"]:
            assert "\\&" not in project["title"]
            assert "\\_" not in project["title"]
            assert "\\textbf" not in project["title"]

    def test_detail_returns_display_bullets(self, client: TestClient) -> None:
        body = client.get("/api/v1/projects/proj_b").json()
        assert body["bullets"]
        assert all("\\%" not in bullet for bullet in body["bullets"])

    def test_unknown_key_is_404_with_a_stable_code(self, client: TestClient) -> None:
        response = client.get("/api/v1/projects/nope")
        assert response.status_code == 404
        assert response.json()["code"] == "unknown_project"

    def test_real_bank_titles_are_clean(self, real_client: TestClient) -> None:
        for project in real_client.get("/api/v1/projects").json()["projects"]:
            assert "\\" not in project["title"]


class TestMatch:
    def test_returns_ranked_projects_with_explanations(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/match", json={"jd_text": "We need Python and SQL for fintech."}
        ).json()
        top = body["ranked_projects"][0]
        assert top["key"] == "proj_a"
        assert top["keyword_hits"]
        assert body["note"]
        assert body["bank_version"]

    @pytest.mark.parametrize("jd", ["", "   ", "\n\t"])
    def test_blank_jd_is_400(self, client: TestClient, jd: str) -> None:
        assert client.post("/api/v1/match", json={"jd_text": jd}).status_code == 400

    def test_missing_field_is_400(self, client: TestClient) -> None:
        assert client.post("/api/v1/match", json={"wrong": "x"}).status_code == 400

    def test_unknown_field_is_rejected(self, client: TestClient) -> None:
        """A silently-ignored typo means the caller believes they set an option
        that was never applied."""
        response = client.post("/api/v1/match", json={"jd_text": "x", "typo": 1})
        assert response.status_code == 400

    def test_oversized_jd_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/v1/match", json={"jd_text": "x" * 300_000})
        assert response.status_code in {400, 413}

    @pytest.mark.parametrize(
        "jd",
        [
            "a",
            "日本語のジョブディスクリプション",
            "emoji 🎯 role",
            "‮reversed text",
            "x" * 5000,
            "Node.js and .NET and C++ and C# and CI/CD",
            "word " * 2000,
        ],
    )
    def test_unusual_but_legal_jds_succeed(self, client: TestClient, jd: str) -> None:
        assert client.post("/api/v1/match", json={"jd_text": jd}).status_code == 200

    def test_hidden_projects_are_absent_from_results(self, client: TestClient) -> None:
        body = client.post("/api/v1/match", json={"jd_text": "experimental"}).json()
        assert "proj_hidden" not in [r["key"] for r in body["ranked_projects"]]


class TestResumeGeneration:
    def test_generates_and_downloads(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/resume/generate", json={"selected_project_keys": ["proj_a"]}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["page_count"] >= 1
        assert body["engine"] == "fake"

        download = client.get(body["download_url"])
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/pdf"
        assert download.content.startswith(b"%PDF")
        assert body["filename"] in download.headers["content-disposition"]

    def test_response_carries_no_base64_blob(self, client: TestClient) -> None:
        """The PDF is streamed from its own endpoint; inlining it inflated
        every response by a third and buried the warning the user needed."""
        body = client.post(
            "/api/v1/resume/generate", json={"selected_project_keys": ["proj_a"]}
        ).json()
        assert "pdf_base64" not in body
        assert len(str(body)) < 2000

    def test_empty_selection_produces_a_static_resume(self, client: TestClient) -> None:
        response = client.post("/api/v1/resume/generate", json={"selected_project_keys": []})
        assert response.status_code == 200
        assert response.json()["page_count"] >= 1

    def test_duplicate_keys_are_collapsed(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a", "proj_a", "proj_a"]},
        ).json()
        single = client.post(
            "/api/v1/resume/generate", json={"selected_project_keys": ["proj_a"]}
        ).json()
        assert body["page_count"] == single["page_count"]

    def test_hidden_project_is_refused_at_generation(self, client: TestClient) -> None:
        """Filtering hidden projects out of the listing while still letting
        /generate use them was defect B3."""
        response = client.post(
            "/api/v1/resume/generate", json={"selected_project_keys": ["proj_hidden"]}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "hidden_project"

    def test_unknown_key_is_404(self, client: TestClient) -> None:
        response = client.post("/api/v1/resume/generate", json={"selected_project_keys": ["nope"]})
        assert response.status_code == 404

    @pytest.mark.parametrize("value", [True, False, 0, -1, 11, 10**9, 2.5, None, [2]])
    def test_invalid_max_pages_is_rejected(self, client: TestClient, value: object) -> None:
        response = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a"], "max_pages": value},
        )
        assert response.status_code == 400, f"{value!r} was accepted"

    @pytest.mark.parametrize("value", ["2", 2.0, 2])
    def test_unambiguous_max_pages_forms_are_accepted(
        self, client: TestClient, value: object
    ) -> None:
        """A numeric string or an integral float has exactly one sensible
        reading, so coercing it is helpful rather than surprising. A boolean
        does not -- which is why it is rejected in the test above rather than
        quietly becoming 1."""
        response = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a"], "max_pages": value},
        )
        assert response.status_code == 200
        assert response.json()["max_pages"] == 2

    def test_max_pages_bool_specifically(self, client: TestClient) -> None:
        """isinstance(True, int) is True in Python, so the original's type check
        accepted `true` and silently applied a one-page limit (defect B5)."""
        response = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a"], "max_pages": True},
        )
        assert response.status_code == 400
        assert "boolean" in response.json()["detail"]

    @pytest.mark.parametrize("value", [[["a"]], [None], [1], [{"a": 1}], "notalist", 5])
    def test_invalid_selection_shapes_are_rejected(self, client: TestClient, value: object) -> None:
        response = client.post("/api/v1/resume/generate", json={"selected_project_keys": value})
        assert response.status_code == 400, f"{value!r} was accepted"

    def test_too_many_projects_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/resume/generate", json={"selected_project_keys": ["proj_a"] * 200}
        )
        assert response.status_code == 400

    def test_personal_info_override_is_applied(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/resume/generate",
            json={
                "selected_project_keys": ["proj_a"],
                "personal_info": {"name": "Another Person"},
            },
        )
        assert response.status_code == 200
        assert response.json()["filename"] == "Another_Person_Resume.pdf"

    def test_personal_info_reserved_key_is_400_not_500(self, client: TestClient) -> None:
        """`template.render(**info, font_size=...)` raised TypeError and
        returned a 500 in the original (defect B1)."""
        response = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a"], "personal_info": {"font_size": 4}},
        )
        assert response.status_code == 400

    def test_page_fit_warning_is_set_when_content_overflows(self, real_client: TestClient) -> None:
        keys = [p["key"] for p in real_client.get("/api/v1/projects").json()["projects"]]
        body = real_client.post(
            "/api/v1/resume/generate", json={"selected_project_keys": keys, "max_pages": 1}
        ).json()
        assert body["page_count"] <= 1 or body["warning"]
        assert body["fits"] == (not body["warning"])

    def test_expired_or_unknown_document_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/resume/nope").status_code == 404


class TestPreview:
    def test_returns_latex_without_compiling(self, client: TestClient, engine) -> None:
        response = client.post("/api/v1/resume/preview", json={"selected_project_keys": ["proj_a"]})
        assert response.status_code == 200
        assert response.json()["tex"].startswith("\\documentclass")
        assert engine.compile_calls == [], "preview must not invoke the PDF engine"

    def test_preview_reports_the_same_validation_errors(self, client: TestClient) -> None:
        response = client.post("/api/v1/resume/preview", json={"selected_project_keys": ["nope"]})
        assert response.status_code == 404
