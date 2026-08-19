"""Injection and hostile-input tests.

Every payload is driven through the real entry points -- the service and the
HTTP API -- rather than against the escaping function alone, because the
original defect was not a broken escaper. It was an escaper that was never
called on the path that mattered.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resume_tailor.core.errors import InvalidInputError, UnsafeContentError
from resume_tailor.domain.latex import (
    count_unbalanced_braces,
    escape_user_text,
    find_unescaped_specials,
    find_unknown_commands,
)
from resume_tailor.services.resume_service import ResumeService
from tests.security.payloads import ALL_PAYLOADS, LINK_TARGETS, MUST_REJECT, STRUCTURAL

pytestmark = pytest.mark.security


#: Marks the classes that build the FastAPI application.
#:
#: The mutation job deselects these, and it used to do so by matching class
#: names -- which is a convention, not a constraint. A class added later whose
#: name happens not to contain the magic substring drives the mutants tree
#: straight back into the namespace-package import failure the deselection
#: exists to avoid, and reports it as "failed to collect stats". A marker is
#: the same intent expressed structurally.
app_backed = pytest.mark.api


class TestEscapingNeutralisesEverything:
    @pytest.mark.parametrize("payload", ALL_PAYLOADS)
    def test_escaped_payload_is_inert(self, payload: str) -> None:
        """Whatever the input, the output contains no live LaTeX."""
        rendered = escape_user_text(payload)
        assert find_unknown_commands(rendered) == []
        assert find_unescaped_specials(rendered) == []
        assert count_unbalanced_braces(rendered) == 0

    @pytest.mark.parametrize("payload", MUST_REJECT)
    def test_no_command_survives_escaping(self, payload: str) -> None:
        rendered = escape_user_text(payload)
        for command in ("\\input", "\\write", "\\def", "\\catcode", "\\csname", "\\loop"):
            assert command not in rendered


class TestServiceRejectsDangerousInput:
    @pytest.mark.parametrize("payload", MUST_REJECT)
    def test_summary_payload_is_refused(self, service: ResumeService, payload: str) -> None:
        with pytest.raises(UnsafeContentError):
            service.build_spec(["proj_a"], summary=payload)

    @pytest.mark.parametrize("payload", MUST_REJECT)
    def test_personal_info_payload_is_refused(self, service: ResumeService, payload: str) -> None:
        with pytest.raises(UnsafeContentError):
            service.build_spec(["proj_a"], personal_info={"name": payload})

    @pytest.mark.parametrize("payload", STRUCTURAL)
    def test_structural_payloads_never_produce_a_broken_document(
        self, service: ResumeService, payload: str
    ) -> None:
        """These are legal characters, so they are escaped rather than
        rejected -- and the rendered document must still be well-formed."""
        try:
            spec = service.build_spec(["proj_a"], summary=payload)
        except (UnsafeContentError, InvalidInputError):
            return  # rejected outright is also an acceptable outcome
        tex, warnings = service.render_preview(spec)
        assert count_unbalanced_braces(tex) == 0
        assert warnings == []

    def test_a_percent_in_the_summary_cannot_comment_out_the_document(
        self, service: ResumeService
    ) -> None:
        """A bare % is the single most common way user text silently destroys a
        resume: it comments out the rest of the line and nothing errors."""
        spec = service.build_spec(["proj_a"], summary="Delivered 50% growth")
        tex, warnings = service.render_preview(spec)
        assert warnings == []
        assert r"50\%" in tex
        assert tex.rstrip().endswith(r"\end{document}")


@app_backed
class TestApiRejectsDangerousInput:
    @pytest.mark.parametrize("payload", MUST_REJECT)
    def test_summary_payload_returns_422_not_500(self, client: TestClient, payload: str) -> None:
        response = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a"], "summary": payload},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "unsafe_content"

    @pytest.mark.parametrize("payload", MUST_REJECT[:6])
    def test_personal_info_payload_returns_422(self, client: TestClient, payload: str) -> None:
        response = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a"], "personal_info": {"name": payload}},
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("payload", ALL_PAYLOADS)
    def test_no_payload_ever_causes_a_server_error(self, client: TestClient, payload: str) -> None:
        """The strongest statement in this file: whatever goes in, the answer is
        a considered status code, never an unhandled 500."""
        response = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a"], "summary": payload},
        )
        assert response.status_code < 500, response.text

    @pytest.mark.parametrize("payload", ALL_PAYLOADS)
    def test_match_endpoint_survives_every_payload(self, client: TestClient, payload: str) -> None:
        response = client.post("/api/v1/match", json={"jd_text": payload})
        assert response.status_code < 500, response.text

    def test_generated_pdf_never_contains_a_leaked_file_path(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/resume/generate",
            json={"selected_project_keys": ["proj_a"], "summary": "Normal summary text."},
        )
        pdf = client.get(response.json()["download_url"]).content
        assert b"/etc/passwd" not in pdf


@app_backed
class TestErrorResponsesDoNotLeak:
    def test_unknown_route_does_not_reveal_internals(self, client: TestClient) -> None:
        body = client.get("/api/v1/definitely-not-a-route").json()
        assert "Traceback" not in str(body)
        assert "resume_tailor" not in str(body)

    def test_bad_bank_yields_a_clean_500(self, client: TestClient, settings) -> None:
        """A server-side content error must be reported without echoing a
        filesystem path back to the caller."""
        settings.bank_path.write_text("{ broken", encoding="utf-8")
        response = client.get("/api/v1/projects")
        assert response.status_code == 500
        assert "Traceback" not in response.text


class TestLinkTargetsAreValidated:
    """The one value on a resume that cannot be escaped.

    A link target has to reach the document literally -- escaping it breaks the
    link -- so being the right shape is its only protection. The header email
    was the field that reached ``\\href{mailto:...}`` without that check, and it
    was invisible to every layer behind it: ``\\href`` is on the audit allowlist,
    and a payload that closes the brace it opens keeps the brace count at zero.
    """

    @pytest.mark.parametrize("payload", LINK_TARGETS)
    def test_service_refuses_an_unsafe_email(self, service: ResumeService, payload: str) -> None:
        with pytest.raises((InvalidInputError, UnsafeContentError)):
            service.build_spec(["proj_a"], personal_info={"email": payload})

    def test_every_link_in_a_rendered_document_is_accounted_for(
        self, service: ResumeService
    ) -> None:
        """No ``\\href`` may appear that the renderer did not build itself.

        The count is the assertion: an injected link raises the number of
        ``\\href`` occurrences above the number of link-bearing fields, which is
        what makes this catch a break-out that the allowlist and the brace
        counter both wave through.
        """
        spec = service.build_spec(["proj_a"], personal_info={"email": "me@example.com"})
        tex, _ = service.render_preview(spec)
        personal = spec.profile.personal
        expected = (
            bool(personal.email)
            + bool(personal.linkedin_url)
            + bool(personal.github_url)
            + len(spec.projects)
        )
        assert tex.count(r"\href") == expected


@app_backed
class TestApiRejectsUnsafeLinkTargets:
    """The same rule, at the HTTP boundary.

    Split from the service-level class rather than merged into it so that the
    app-building tests carry the marker honestly: a class that is half pure and
    half app-backed cannot be deselected correctly by either.
    """

    @pytest.mark.parametrize("payload", LINK_TARGETS)
    def test_api_refuses_an_unsafe_email(self, client: TestClient, payload: str) -> None:
        response = client.post(
            "/api/v1/resume/preview",
            json={"selected_project_keys": ["proj_a"], "personal_info": {"email": payload}},
        )
        assert response.status_code in (400, 422), response.text
        assert response.status_code < 500

    def test_the_original_exploit_no_longer_renders(self, client: TestClient) -> None:
        """The exact payload that used to return 200 with a live link.

        Stated as the outcome rather than the mechanism: the request is refused
        and no document comes back. The problem document does quote the offending
        value, which is the caller's own input and exactly what makes a
        validation error actionable -- what must not happen is that the value
        reaches a rendered ``.tex``.
        """
        response = client.post(
            "/api/v1/resume/preview",
            json={
                "selected_project_keys": ["proj_a"],
                "personal_info": {"email": r"x} \href{https://evil.invalid}{CLICK ME"},
            },
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "invalid_input"
        assert "tex" not in body

    @pytest.mark.parametrize(
        "email",
        [
            "first_last@example.com",
            "me+tag@example.co.uk",
            "a.b.c@sub.domain.example",
            "digits123@example.org",
        ],
    )
    def test_ordinary_addresses_still_work(self, client: TestClient, email: str) -> None:
        """The check must not cost anyone a legitimate email address."""
        response = client.post(
            "/api/v1/resume/preview",
            json={"selected_project_keys": ["proj_a"], "personal_info": {"email": email}},
        )
        assert response.status_code == 200, response.text
        assert f"mailto:{email}" in response.json()["tex"]
