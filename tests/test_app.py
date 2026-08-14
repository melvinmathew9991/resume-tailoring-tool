"""
test_app.py -- tests the Flask API endpoints via Flask's test client
(no live server/process needed -- this is the standard, reliable way
to test a Flask app, and avoids background-process lifecycle issues
entirely).
"""
import base64
import json
import pytest


@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}


class TestProjectsEndpoint:
    def test_returns_project_list(self, client):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "projects" in data
        assert len(data["projects"]) > 0

    def test_excludes_transformer_scratch(self, client):
        resp = client.get("/api/projects")
        data = resp.get_json()
        keys = [p["key"] for p in data["projects"]]
        assert "transformer_scratch" not in keys

    def test_titles_are_clean_display_text_not_raw_latex(self, client):
        # Regression test for the exact bug caught during manual testing:
        # raw LaTeX escapes (\&, \_) leaking into the JSON response.
        resp = client.get("/api/projects")
        data = resp.get_json()
        for p in data["projects"]:
            assert "\\&" not in p["title"], f"Raw LaTeX leaked into title: {p['title']}"
            assert "\\_" not in p["title"], f"Raw LaTeX leaked into title: {p['title']}"
            assert "\\textbf" not in p["title"], f"Raw LaTeX leaked into title: {p['title']}"


class TestMatchEndpoint:
    def test_missing_body_returns_400(self, client):
        resp = client.post("/api/match", json={})
        assert resp.status_code == 400

    def test_missing_jd_text_field_returns_400(self, client):
        resp = client.post("/api/match", json={"wrong_field": "value"})
        assert resp.status_code == 400

    def test_empty_jd_text_returns_400(self, client):
        resp = client.post("/api/match", json={"jd_text": ""})
        assert resp.status_code == 400

    def test_whitespace_only_jd_text_returns_400(self, client):
        resp = client.post("/api/match", json={"jd_text": "   \n\t  "})
        assert resp.status_code == 400

    def test_valid_jd_returns_ranked_projects(self, client):
        resp = client.post("/api/match", json={
            "jd_text": "We need a Data Scientist with Python, SQL, fraud detection, and credit risk experience."
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ranked_projects" in data
        assert "gap_terms" in data
        assert "note" in data
        assert len(data["ranked_projects"]) > 0
        # credit_default and aml_fraud should rank highly for this JD
        top_keys = [p["key"] for p in data["ranked_projects"][:3]]
        assert "credit_default" in top_keys or "aml_fraud" in top_keys

    def test_non_json_body_returns_400(self, client):
        resp = client.post("/api/match", data="not json", content_type="text/plain")
        assert resp.status_code == 400

    def test_no_raw_latex_in_ranked_project_titles(self, client):
        resp = client.post("/api/match", json={"jd_text": "Python SQL machine learning"})
        data = resp.get_json()
        for p in data["ranked_projects"]:
            assert "\\&" not in p["title"]
            assert "\\_" not in p["title"]


class TestGenerateEndpoint:
    def test_missing_body_returns_400(self, client):
        resp = client.post("/api/generate", json={})
        assert resp.status_code == 400

    def test_missing_selected_keys_returns_400(self, client):
        resp = client.post("/api/generate", json={"max_pages": 2})
        assert resp.status_code == 400

    def test_selected_keys_not_a_list_returns_400(self, client):
        resp = client.post("/api/generate", json={"selected_project_keys": "not_a_list"})
        assert resp.status_code == 400

    def test_invalid_max_pages_returns_400(self, client):
        resp = client.post("/api/generate", json={
            "selected_project_keys": ["credit_default"],
            "max_pages": -1,
        })
        assert resp.status_code == 400

    def test_unknown_project_key_returns_400(self, client):
        resp = client.post("/api/generate", json={
            "selected_project_keys": ["this_does_not_exist"],
        })
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_valid_request_returns_pdf(self, client):
        resp = client.post("/api/generate", json={
            "selected_project_keys": ["credit_default", "aml_fraud", "medbot"],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "pdf_base64" in data
        assert data["page_count"] <= 2
        assert data["warning"] == ""

        # Confirm the base64 actually decodes to a valid PDF (starts with %PDF)
        pdf_bytes = base64.b64decode(data["pdf_base64"])
        assert pdf_bytes[:4] == b"%PDF"

    def test_generate_with_all_projects_returns_warning_not_silent_overflow(self, client):
        from resume_builder import load_project_bank
        all_keys = [k for k in load_project_bank().keys() if k != "transformer_scratch"]
        resp = client.post("/api/generate", json={
            "selected_project_keys": all_keys,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        if data["page_count"] > 2:
            assert data["warning"] != "", (
                "API returned an over-length resume with no warning -- "
                "this must never happen silently."
            )

    def test_custom_max_pages_respected(self, client):
        resp = client.post("/api/generate", json={
            "selected_project_keys": ["credit_default"],
            "max_pages": 1,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        if data["page_count"] > 1:
            assert data["warning"] != ""

    def test_custom_summary_accepted(self, client):
        resp = client.post("/api/generate", json={
            "selected_project_keys": ["medbot"],
            "summary": "A custom test summary with no special characters.",
        })
        assert resp.status_code == 200
