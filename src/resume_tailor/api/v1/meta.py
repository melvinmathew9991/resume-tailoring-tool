"""Defaults and limits, so a client never has to hardcode them.

The old frontend hardcoded ``max=5`` on the page-count input and had no way to
know the default summary at all. Every limit the server enforces is published
here, which is what lets the Streamlit UI validate before it sends.
"""

from __future__ import annotations

from fastapi import APIRouter

from resume_tailor import __version__
from resume_tailor.api.deps import ApiKeyDep, ServiceDep, SettingsDep
from resume_tailor.api.schemas import EngineStatusOut, MetaResponse
from resume_tailor.domain.latex import latex_to_display_text

router = APIRouter(tags=["meta"])


@router.get("/meta", response_model=MetaResponse, summary="Defaults, limits and engine status")
def get_meta(service: ServiceDep, settings: SettingsDep, _: ApiKeyDep = None) -> MetaResponse:
    profile = service.profile()
    status = service.engine_status()
    return MetaResponse(
        app_version=__version__,
        bank_version=service.bank().version,
        default_summary=latex_to_display_text(profile.summary),
        personal_info=profile.personal.model_dump(),
        font_ladder=list(settings.font_ladder),
        max_pages_limit=settings.max_pages_limit,
        max_selected_projects=settings.max_selected_projects,
        max_jd_chars=settings.max_jd_chars,
        max_summary_chars=settings.max_summary_chars,
        engine=EngineStatusOut(
            name=status.name,
            available=status.available,
            detail=status.detail,
            version=status.version,
            produces_real_pdfs=status.name != "fake",
        ),
    )
