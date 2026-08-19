"""ResumeSpec -> LaTeX source.

Two responsibilities, and the split matters:

* **Building the render context.** The context is constructed here, from typed
  models, with a fixed set of keys. Caller-supplied data appears only as
  *values*. The original spread a user-controlled dict into
  ``template.render(**info, summary=..., font_size=...)``, so a request body
  containing ``{"personal_info": {"font_size": 1}}`` raised
  ``TypeError: got multiple values for keyword argument`` and returned a 500
  (defect B1). That is now unrepresentable rather than validated against.

* **Auditing the rendered source** before it reaches a compiler -- the last
  checkpoint where a problem is still cheap to diagnose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import TemplateError

from resume_tailor.core.errors import TemplateRenderError, UnsafeContentError
from resume_tailor.core.logging import get_logger
from resume_tailor.domain.latex import (
    count_unbalanced_braces,
    escape_latex,
    find_unescaped_specials,
    find_unknown_commands,
    require_href_safe,
)
from resume_tailor.domain.models import PersonalInfo, Project, ResumeSpec
from resume_tailor.render.template_env import get_environment

logger = get_logger(__name__)


@dataclass(frozen=True)
class RenderedSource:
    tex: str
    font_size: float
    line_spacing: float
    warnings: list[str] = field(default_factory=list)


def _href(target: str, label: str) -> str:
    """Build one ``\\href{}``, refusing a target that could break out of it.

    Every link on the resume goes through here, and the target is re-checked
    even though the models already validate it. That is not belt-and-braces for
    its own sake: a link *target* is the one thing on the page that cannot be
    escaped -- escaping breaks the URL -- so its only protection is being the
    right shape. When that check lived only on the model, adding a field that
    reached a link without one was a silent, one-line mistake, and the resulting
    injection was invisible downstream: ``\\href`` is on the audit allowlist,
    and a payload that closes the brace it opens passes the brace count too.
    Routing every link through a single chokepoint makes the check impossible to
    forget rather than merely easy to remember.
    """
    try:
        require_href_safe(target, "link target")
    except ValueError as exc:
        raise UnsafeContentError(str(exc), target=target[:120]) from exc
    return rf"\href{{{target}}}{{{label}}}"


def _contact_line(personal: PersonalInfo) -> str:
    """Header contact row, skipping fields the profile leaves blank."""
    parts: list[str] = []
    if personal.phone:
        parts.append(escape_latex(personal.phone))
    if personal.location:
        parts.append(escape_latex(personal.location))
    if personal.email:
        parts.append(_href(f"mailto:{personal.email}", escape_latex(personal.email)))
    return r" \quad ".join(parts)


def _profile_line(personal: PersonalInfo) -> str:
    parts: list[str] = []
    if personal.linkedin_url:
        display = escape_latex(personal.linkedin_display or personal.linkedin_url)
        parts.append(_href(personal.linkedin_url, f"LinkedIn: {display}"))
    if personal.github_url:
        display = escape_latex(personal.github_display or personal.github_url)
        parts.append(_href(personal.github_url, f"GitHub: {display}"))
    return r" \quad ".join(parts)


def _project_context(project: Project) -> dict[str, Any]:
    display = escape_latex(project.github)  # this one is typeset
    return {
        "title": project.title,  # pre-verified LaTeX markup, used verbatim
        "github_url": project.github_url,  # shape-validated by the model
        "github_display": display,
        # Composed here rather than in the template, so that this link goes
        # through the same chokepoint as every other one. A template that
        # assembles its own \href{} is a link the check cannot see.
        "github_link": _href(project.github_url, f"GitHub: {display}"),
        "bullets": list(project.bullets),
    }


def build_context(spec: ResumeSpec, font_size: float, line_spacing: float) -> dict[str, Any]:
    """The complete, fixed-key template context."""
    personal = spec.profile.personal
    return {
        "personal": {
            "name": escape_latex(personal.name),
            "title": escape_latex(personal.title),
        },
        "contact_line": _contact_line(personal),
        "profile_line": _profile_line(personal),
        "summary": spec.profile.summary,
        "experience": [
            {
                "title": escape_latex(job.title),
                "company": escape_latex(job.company),
                "dates": escape_latex(job.dates),
                "subtitle": escape_latex(job.subtitle),
                "bullets": list(job.bullets),
            }
            for job in spec.profile.experience
        ],
        "projects": [_project_context(project) for project in spec.projects],
        "skills": [
            {"name": category.name, "content": category.content} for category in spec.profile.skills
        ],
        "education": [
            {
                "degree": escape_latex(entry.degree),
                "institution": escape_latex(entry.institution),
                "dates": escape_latex(entry.dates),
            }
            for entry in spec.profile.education
        ],
        "font_size": f"{font_size:g}",
        "line_spacing": f"{line_spacing:g}",
    }


def audit_source(tex: str) -> list[str]:
    """Final safety check on fully rendered LaTeX.

    Raises :class:`UnsafeContentError` for anything that could execute or read
    files; returns non-fatal warnings for everything else. The allowlist means
    a construct nobody anticipated is rejected by default rather than permitted
    by default -- the whole point of choosing an allowlist over a denylist.
    """
    unknown = find_unknown_commands(tex)
    if unknown:
        raise UnsafeContentError(
            "rendered LaTeX contains control sequences that are not on the allowlist: "
            + ", ".join(f"\\{name}" for name in unknown[:10])
            + ". If this is legitimate new markup, add it to "
            "resume_tailor.domain.latex.ALLOWED_COMMANDS.",
            commands=unknown[:10],
        )

    imbalance = count_unbalanced_braces(tex)
    if imbalance != 0:
        raise UnsafeContentError(
            f"rendered LaTeX has {abs(imbalance)} unbalanced brace(s) "
            f"({'unclosed' if imbalance > 0 else 'unopened'}); it would not compile",
            imbalance=imbalance,
        )

    return find_unescaped_specials(tex)


def render_source(
    spec: ResumeSpec,
    font_size: float,
    line_spacing: float,
    *,
    template_dir: Path,
    template_name: str = "resume.tex.j2",
) -> RenderedSource:
    """Render the template and audit the result."""
    environment = get_environment(template_dir)
    try:
        template = environment.get_template(template_name)
        tex = template.render(**build_context(spec, font_size, line_spacing))
    except TemplateError as exc:
        raise TemplateRenderError(f"could not render {template_name}: {exc}") from exc

    warnings = audit_source(tex)
    if warnings:
        # Non-fatal, but never silent: this is the check that existed in the
        # original code and was never called from anywhere (defect B8).
        logger.warning("render.audit_warnings", count=len(warnings), first=warnings[0])

    return RenderedSource(
        tex=tex, font_size=font_size, line_spacing=line_spacing, warnings=warnings
    )
