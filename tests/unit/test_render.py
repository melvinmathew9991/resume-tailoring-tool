"""Template rendering, context construction and the source audit."""

from __future__ import annotations

import pytest

from resume_tailor.core.config import Settings
from resume_tailor.core.errors import TemplateRenderError, UnsafeContentError
from resume_tailor.domain.models import PersonalInfo, Profile, Project, ResumeSpec
from resume_tailor.render.renderer import audit_source, build_context, render_source
from resume_tailor.services.resume_service import ResumeService

pytestmark = pytest.mark.unit


def make_spec(service: ResumeService, **kwargs: object) -> ResumeSpec:
    return service.build_spec(["proj_a", "proj_b"], **kwargs)  # type: ignore[arg-type]


class TestBuildContext:
    def test_context_keys_are_fixed(self, service: ResumeService) -> None:
        """The set of template variables is decided here, not by the caller.

        This is the structural fix for the reserved-key crash: user data can
        only ever be a *value* in this dict, so no request body can collide
        with a template variable name (defect B1).
        """
        context = build_context(make_spec(service), 9.6, 11.5)
        assert set(context) == {
            "personal",
            "contact_line",
            "profile_line",
            "summary",
            "experience",
            "projects",
            "skills",
            "education",
            "font_size",
            "line_spacing",
        }

    def test_personal_fields_are_escaped(self, service: ResumeService) -> None:
        spec = make_spec(service, personal_info={"name": "R&D Person"})
        context = build_context(spec, 9.6, 11.5)
        assert context["personal"]["name"] == r"R\&D Person"  # type: ignore[index]

    def test_bullets_are_used_verbatim(self, service: ResumeService) -> None:
        context = build_context(make_spec(service), 9.6, 11.5)
        bullet = context["projects"][0]["bullets"][1]  # type: ignore[index]
        assert r"95\%" in bullet, "pre-verified markup must not be re-escaped"

    def test_github_display_is_escaped_but_url_is_not(self, service: ResumeService) -> None:
        context = build_context(make_spec(service), 9.6, 11.5)
        project = next(
            item
            for item in context["projects"]  # type: ignore[union-attr]
            if "underscores" in item["github_url"]
        )
        assert r"\_" in project["github_display"], "typeset text must be escaped"
        assert r"\_" not in project["github_url"], "the URL must stay literal"

    def test_font_size_is_formatted_without_trailing_zeros(self, service: ResumeService) -> None:
        context = build_context(make_spec(service), 9.0, 10.8)
        assert context["font_size"] == "9"


class TestRenderSource:
    def test_renders_valid_latex(self, service: ResumeService, settings: Settings) -> None:
        rendered = render_source(make_spec(service), 9.6, 11.5, template_dir=settings.template_dir)
        assert rendered.tex.startswith("\\documentclass")
        assert rendered.tex.rstrip().endswith(r"\end{document}")
        assert rendered.warnings == []

    def test_font_size_reaches_the_output(self, service: ResumeService, settings: Settings) -> None:
        rendered = render_source(make_spec(service), 8.8, 10.6, template_dir=settings.template_dir)
        assert r"\fontsize{8.8}{10.6}" in rendered.tex

    def test_selected_projects_appear_in_order(
        self, service: ResumeService, settings: Settings
    ) -> None:
        spec = service.build_spec(["proj_b", "proj_a"])
        tex = render_source(spec, 9.6, 11.5, template_dir=settings.template_dir).tex
        assert tex.index("project_b_with_underscores") < tex.index("project-a")

    def test_empty_selection_still_renders(
        self, service: ResumeService, settings: Settings
    ) -> None:
        """A resume with no projects is a valid document -- static sections
        only. It must not crash."""
        spec = service.build_spec([])
        tex = render_source(spec, 9.6, 11.5, template_dir=settings.template_dir).tex
        assert r"\section*{Projects}" not in tex
        assert r"\section*{Skills}" in tex

    def test_missing_template_raises_a_typed_error(
        self, service: ResumeService, settings: Settings
    ) -> None:
        with pytest.raises(TemplateRenderError):
            render_source(
                make_spec(service),
                9.6,
                11.5,
                template_dir=settings.template_dir,
                template_name="nope.tex.j2",
            )

    def test_optional_profile_fields_are_omitted_cleanly(
        self, service: ResumeService, settings: Settings
    ) -> None:
        profile = Profile.model_validate(
            {"personal": {"name": "Only A Name"}, "summary": "A summary."}
        )
        spec = ResumeSpec(
            profile=profile, projects=[], project_keys=[], max_pages=2, bank_version="x"
        )
        tex = render_source(spec, 9.6, 11.5, template_dir=settings.template_dir).tex
        assert "mailto:" not in tex
        assert r"\section*{Experience}" not in tex


class TestAuditSource:
    def test_clean_source_passes(self) -> None:
        assert audit_source(r"\textbf{ok} \item x") == []

    def test_unknown_command_is_rejected(self) -> None:
        with pytest.raises(UnsafeContentError, match="allowlist"):
            audit_source(r"\evilmacro{x}")

    def test_error_names_the_offending_command(self) -> None:
        with pytest.raises(UnsafeContentError) as info:
            audit_source(r"\mystery{x}")
        assert "mystery" in info.value.context["commands"]

    @pytest.mark.parametrize("source", [r"\textbf{unclosed", r"closed}"])
    def test_unbalanced_braces_are_rejected(self, source: str) -> None:
        with pytest.raises(UnsafeContentError, match="unbalanced"):
            audit_source(source)

    def test_unescaped_percent_is_a_warning_not_an_error(self) -> None:
        """Non-fatal but never silent -- this is the check the original code
        wrote and then never called from anywhere (defect B8)."""
        warnings = audit_source(r"\textbf{x} 50% done")
        assert len(warnings) == 1
        assert "unescaped '%'" in warnings[0]

    def test_real_render_is_audited(self, real_service: ResumeService) -> None:
        spec = real_service.build_spec(["credit_default", "medbot"])
        _, warnings = real_service.render_preview(spec)
        assert warnings == [], f"production content trips the audit: {warnings}"


class TestPersonalInfoRendering:
    def test_special_characters_in_a_name_do_not_break_the_document(
        self, service: ResumeService, settings: Settings
    ) -> None:
        spec = make_spec(service, personal_info={"name": "A & B_C #1 100%"})
        rendered = render_source(spec, 9.6, 11.5, template_dir=settings.template_dir)
        assert rendered.warnings == []

    def test_unicode_name_renders(self, service: ResumeService, settings: Settings) -> None:
        spec = make_spec(service, personal_info={"name": "Ana María"})
        assert "Ana María" in render_source(spec, 9.6, 11.5, template_dir=settings.template_dir).tex


def test_project_model_is_not_mutated_by_rendering(service: ResumeService) -> None:
    """Rendering must be a pure read of the bank; a mutated Project would
    corrupt every later request served from the same cache."""
    spec = make_spec(service)
    original = [Project.model_dump(project) for project in spec.projects]
    build_context(spec, 9.6, 11.5)
    assert [Project.model_dump(project) for project in spec.projects] == original


def test_personal_info_model_rejects_reserved_template_names() -> None:
    for reserved in ("summary", "projects", "skills", "font_size", "line_spacing"):
        with pytest.raises(ValueError, match=r"Extra inputs|extra"):
            PersonalInfo.model_validate({"name": "X", reserved: "boom"})
