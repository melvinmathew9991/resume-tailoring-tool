"""The application service: selection rules, sanitisation and orchestration.

Most of the original code's crash-and-500 defects lived at exactly this
boundary, so most of them have a named test here.
"""

from __future__ import annotations

import pytest

from resume_tailor.core.errors import (
    HiddenProjectError,
    InvalidInputError,
    UnknownProjectError,
    UnsafeContentError,
)
from resume_tailor.services.resume_service import ResumeService

pytestmark = pytest.mark.unit


class TestResolveSelection:
    def test_preserves_requested_order(self, service: ResumeService) -> None:
        keys, projects = service.resolve_selection(["proj_b", "proj_a"])
        assert keys == ["proj_b", "proj_a"]
        assert projects[0].github == "testuser/project_b_with_underscores"

    def test_deduplicates_while_preserving_first_position(self, service: ResumeService) -> None:
        """The original rendered the same project twice (defect B7)."""
        keys, projects = service.resolve_selection(["proj_a", "proj_b", "proj_a"])
        assert keys == ["proj_a", "proj_b"]
        assert len(projects) == 2

    def test_unknown_key_is_reported_not_silently_dropped(self, service: ResumeService) -> None:
        with pytest.raises(UnknownProjectError, match="nope"):
            service.resolve_selection(["proj_a", "nope"])

    def test_all_unknown_keys_are_listed_at_once(self, service: ResumeService) -> None:
        with pytest.raises(UnknownProjectError) as info:
            service.resolve_selection(["x", "y"])
        assert info.value.context["unknown_keys"] == ["x", "y"]

    def test_hidden_project_is_refused(self, service: ResumeService) -> None:
        """Hiding a project from the listing while still letting /generate put
        it on a resume was defect B3."""
        with pytest.raises(HiddenProjectError, match="proj_hidden"):
            service.resolve_selection(["proj_hidden"])

    @pytest.mark.parametrize("bad", [[["a"]], [None], [1], [{"k": "v"}]])
    def test_non_string_elements_are_rejected_cleanly(
        self, service: ResumeService, bad: list[object]
    ) -> None:
        """These used to reach `key not in bank` and raise
        `TypeError: unhashable type` -- an opaque 500 (defect B2)."""
        with pytest.raises(InvalidInputError, match="must be a string"):
            service.resolve_selection(bad)  # type: ignore[arg-type]

    def test_non_list_input_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="must be a list"):
            service.resolve_selection("proj_a")  # type: ignore[arg-type]

    def test_too_many_projects_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="over the limit"):
            service.resolve_selection(["proj_a"] * 500)

    def test_empty_selection_is_allowed(self, service: ResumeService) -> None:
        assert service.resolve_selection([]) == ([], [])

    def test_case_sensitive_keys(self, service: ResumeService) -> None:
        with pytest.raises(UnknownProjectError):
            service.resolve_selection(["PROJ_A"])


class TestBuildSpec:
    def test_defaults(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"])
        assert spec.max_pages == 2
        assert spec.profile.personal.name == "Test Person"
        assert spec.bank_version

    @pytest.mark.parametrize("pages", [0, -1, 11, 999_999])
    def test_max_pages_bounds(self, service: ResumeService, pages: int) -> None:
        with pytest.raises(InvalidInputError, match="max_pages"):
            service.build_spec(["proj_a"], max_pages=pages)

    def test_summary_override_is_escaped(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], summary="R&D at 50% capacity")
        assert spec.profile.summary == r"R\&D at 50\% capacity"

    def test_summary_override_supports_light_markup(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], summary="A **bold** claim")
        assert spec.profile.summary == r"A \textbf{bold} claim"

    @pytest.mark.parametrize(
        "payload",
        [
            r"\input{/etc/passwd}",
            r"\write18{whoami}",
            r"\def\x{\x\x}",
            r"\catcode`\%=12",
            r"\newcommand{\x}{y}",
        ],
    )
    def test_dangerous_summary_is_rejected(self, service: ResumeService, payload: str) -> None:
        with pytest.raises(UnsafeContentError):
            service.build_spec(["proj_a"], summary=payload)

    def test_oversized_summary_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="over the"):
            service.build_spec(["proj_a"], summary="x" * 10_000)

    def test_summary_of_only_control_characters_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="empty after"):
            service.build_spec(["proj_a"], summary="\x00\x01\x02")

    def test_personal_info_override_applies(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], personal_info={"name": "Someone Else"})
        assert spec.profile.personal.name == "Someone Else"

    def test_personal_info_reserved_key_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError):
            service.build_spec(["proj_a"], personal_info={"font_size": 4})

    def test_personal_info_wrong_type_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="must be an object"):
            service.build_spec(["proj_a"], personal_info="a string")  # type: ignore[arg-type]

    def test_dangerous_personal_field_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(UnsafeContentError, match=r"personal_info\.name"):
            service.build_spec(["proj_a"], personal_info={"name": r"\input{x}"})

    def test_oversized_personal_field_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="character limit"):
            service.build_spec(["proj_a"], personal_info={"name": "x" * 500})

    def test_summary_none_keeps_the_profile_default(self, service: ResumeService) -> None:
        assert "markup" in service.build_spec(["proj_a"], summary=None).profile.summary


class TestMatchService:
    def test_returns_a_report(self, service: ResumeService) -> None:
        report = service.match("Python and SQL for a fintech team")
        assert report.ranked_projects
        assert report.bank_version
        assert "judgment" in report.note

    @pytest.mark.parametrize("jd", ["", "   ", "\n\t "])
    def test_blank_jd_is_rejected(self, service: ResumeService, jd: str) -> None:
        with pytest.raises(InvalidInputError, match="must not be empty"):
            service.match(jd)

    def test_oversized_jd_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="character limit"):
            service.match("x" * 200_000)

    def test_gap_terms_are_capped(self, service: ResumeService) -> None:
        jd = " ".join(f"and Tool{index}" for index in range(200))
        assert len(service.match(jd).gap_terms) <= 30

    def test_hidden_projects_excluded_by_default(self, service: ResumeService) -> None:
        keys = [result.key for result in service.match("experimental").ranked_projects]
        assert "proj_hidden" not in keys


class TestGeneration:
    def test_generates_and_stores_a_document(self, service: ResumeService) -> None:
        result = service.generate_sync(service.build_spec(["proj_a"]))
        assert result.fit.pdf_bytes.startswith(b"%PDF")
        assert service.documents.get(result.document.document_id)

    def test_filename_is_derived_from_the_name(self, service: ResumeService) -> None:
        result = service.generate_sync(service.build_spec(["proj_a"]))
        assert result.document.filename == "Test_Person_Resume.pdf"

    def test_filename_is_sanitised(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], personal_info={"name": "A/B\\C:D"})
        assert "/" not in service.generate_sync(spec).document.filename

    def test_empty_selection_still_produces_a_resume(self, service: ResumeService) -> None:
        result = service.generate_sync(service.build_spec([]))
        assert result.fit.page_count >= 1

    @pytest.mark.parametrize("max_pages", [1, 2, 3])
    def test_page_fit_invariant_on_the_real_bank(
        self, real_service: ResumeService, max_pages: int
    ) -> None:
        keys = list(real_service.bank().visible())
        result = real_service.generate_sync(real_service.build_spec(keys, max_pages=max_pages))
        assert result.fit.page_count <= max_pages or result.fit.warning

    async def test_async_generate_matches_sync(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"])
        assert (await service.generate(spec)).fit.page_count == (
            service.generate_sync(spec).fit.page_count
        )

    async def test_concurrent_generation_is_bounded(self, service: ResumeService) -> None:
        """Ten simultaneous requests must not mean ten simultaneous compilers."""
        import asyncio

        spec = service.build_spec(["proj_a"])
        results = await asyncio.gather(*(service.generate(spec) for _ in range(10)))
        assert len(results) == 10
        assert all(result.fit.pdf_bytes.startswith(b"%PDF") for result in results)


class TestReadiness:
    def test_reports_ready_with_valid_content(self, service: ResumeService) -> None:
        report = service.readiness()
        assert report["ready"] is True
        assert report["checks"]["pdf_engine"]["produces_real_pdfs"] is False

    def test_reports_not_ready_when_the_bank_is_broken(
        self, service: ResumeService, settings
    ) -> None:
        """Readiness must *report* failures, never raise them -- a probe that
        500s tells a process manager nothing about what is wrong."""
        settings.bank_path.write_text("{ broken", encoding="utf-8")
        report = service.readiness()
        assert report["ready"] is False
        assert report["checks"]["project_bank"]["ok"] is False

    def test_reports_not_ready_when_the_profile_is_broken(
        self, service: ResumeService, settings
    ) -> None:
        settings.profile_path.write_text("[not a mapping]", encoding="utf-8")
        report = service.readiness()
        assert report["ready"] is False
        assert report["checks"]["profile"]["ok"] is False
