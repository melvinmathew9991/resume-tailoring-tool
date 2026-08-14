"""Project bank listing.

Titles and bullets are returned as *display text*, never raw LaTeX. That is a
regression target: ``\\&`` and ``\\_`` leaking into JSON responses meant for
humans was a real bug in the original, and the test suite asserts it stays
fixed.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from resume_tailor.api.deps import ApiKeyDep, ServiceDep
from resume_tailor.api.schemas import ProjectDetail, ProjectListResponse, ProjectSummary
from resume_tailor.core.errors import UnknownProjectError
from resume_tailor.domain.latex import latex_to_display_text
from resume_tailor.domain.models import Project

router = APIRouter(tags=["projects"])


def _summary(key: str, project: Project) -> ProjectSummary:
    return ProjectSummary(
        key=key,
        title=latex_to_display_text(project.title),
        domain=list(project.domain),
        keywords=list(project.keywords),
        bullet_count=len(project.bullets),
        github=project.github,
        github_url=project.github_url,
        hidden=project.hidden,
    )


@router.get("/projects", response_model=ProjectListResponse, summary="List projects")
def list_projects(
    service: ServiceDep,
    include_hidden: bool = Query(
        default=False,
        description=(
            "Include projects marked hidden. Hidden projects are still refused "
            "by /resume/generate -- this flag is for inspecting the bank, not "
            "for putting them on a resume."
        ),
    ),
    _: ApiKeyDep = None,
) -> ProjectListResponse:
    bank = service.bank()
    projects = bank.projects if include_hidden else bank.visible()
    items = [_summary(key, project) for key, project in projects.items()]
    return ProjectListResponse(projects=items, bank_version=bank.version, count=len(items))


@router.get(
    "/projects/{key}",
    response_model=ProjectDetail,
    summary="One project, including its bullets",
    responses={404: {"description": "No project with that key."}},
)
def get_project(key: str, service: ServiceDep, _: ApiKeyDep = None) -> ProjectDetail:
    bank = service.bank()
    project = bank.projects.get(key)
    if project is None:
        raise UnknownProjectError(f"no project with key {key!r}", key=key)

    base = _summary(key, project)
    return ProjectDetail(
        **base.model_dump(),
        bullets=[latex_to_display_text(bullet) for bullet in project.bullets],
    )
