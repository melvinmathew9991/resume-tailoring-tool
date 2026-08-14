"""Request and response models.

Two layers of limits on purpose. The caps here are static, generous and exist
to reject obvious abuse before any work happens; the precise, configurable
limits live in the service and produce the actionable error message. A schema
constant cannot depend on runtime settings without making the OpenAPI document
environment-dependent, and a stable published contract is worth more than
perfectly-matched numbers in two places.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Hard schema ceilings. The configurable limits are lower; see core/config.py.
MAX_JD_CHARS_HARD = 200_000
MAX_SUMMARY_CHARS_HARD = 20_000
MAX_SELECTED_PROJECTS_HARD = 100


class ApiModel(BaseModel):
    """Unknown fields are rejected, not ignored.

    A silently-ignored ``max_page`` typo means the caller believes they set a
    page limit that was never applied -- precisely the class of silent failure
    this tool exists to eliminate.
    """

    model_config = ConfigDict(extra="forbid")


# --- requests ---------------------------------------------------------------


class MatchRequest(ApiModel):
    jd_text: str = Field(min_length=1, max_length=MAX_JD_CHARS_HARD)
    include_hidden: bool = False

    @field_validator("jd_text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("jd_text must contain non-whitespace characters")
        return value


class PersonalInfoOverride(ApiModel):
    """All-optional overlay on the profile header.

    ``extra="forbid"`` is doing real work here: the original merged an arbitrary
    caller dict into the template keyword arguments, so a body containing
    ``{"personal_info": {"font_size": 1}}`` produced
    ``TypeError: got multiple values for keyword argument 'font_size'`` and a
    500 (defect B1).
    """

    name: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=254)
    linkedin_url: str | None = Field(default=None, max_length=500)
    linkedin_display: str | None = Field(default=None, max_length=200)
    github_url: str | None = Field(default=None, max_length=500)
    github_display: str | None = Field(default=None, max_length=200)


class ResumeRequest(ApiModel):
    selected_project_keys: list[str] = Field(
        default_factory=list, max_length=MAX_SELECTED_PROJECTS_HARD
    )
    max_pages: int = Field(default=2, ge=1, le=10)
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY_CHARS_HARD)
    personal_info: PersonalInfoOverride | None = None

    @field_validator("max_pages", mode="before")
    @classmethod
    def _reject_bool(cls, value: Any) -> Any:
        """``isinstance(True, int)`` is ``True`` in Python.

        The original's ``isinstance(max_pages, int)`` check therefore accepted
        ``"max_pages": true`` and silently turned it into a one-page limit
        (defect B5). Booleans are rejected outright rather than coerced.
        """
        if isinstance(value, bool):
            raise ValueError("max_pages must be an integer, not a boolean")
        return value


# --- responses --------------------------------------------------------------


class ProjectSummary(ApiModel):
    key: str
    title: str
    domain: list[str]
    keywords: list[str]
    bullet_count: int
    github: str
    github_url: str
    hidden: bool


class ProjectDetail(ProjectSummary):
    bullets: list[str]
    """Display text, not raw LaTeX -- regression target for the bug where
    ``\\&`` leaked into JSON responses meant for humans."""


class ProjectListResponse(ApiModel):
    projects: list[ProjectSummary]
    bank_version: str
    count: int


class KeywordHitOut(ApiModel):
    keyword: str
    occurrences: int


class MatchResultOut(ApiModel):
    key: str
    title: str
    score: int
    coverage: float
    matched_keywords: list[str]
    keyword_hits: list[KeywordHitOut]
    matched_domains: list[str]
    domain_match: bool


class MatchResponse(ApiModel):
    ranked_projects: list[MatchResultOut]
    gap_terms: list[str]
    bank_version: str
    note: str


class PreviewResponse(ApiModel):
    tex: str
    warnings: list[str]
    project_keys: list[str]
    bank_version: str
    character_count: int


class GenerateResponse(ApiModel):
    document_id: str
    """Fetch the bytes from ``GET /api/v1/resume/{document_id}``.

    The original returned the PDF base64-encoded inside the JSON body, which
    inflated it by a third and forced both ends to hold the whole document in
    memory as a string (defect S7)."""

    download_url: str
    filename: str
    page_count: int
    max_pages: int
    fits: bool
    font_size_used: float
    line_spacing_used: float
    compile_attempts: int
    engine: str
    warning: str = ""
    """Non-empty exactly when the page limit could not be met. The core safety
    guarantee: ``fits`` false implies this is set."""
    source_warnings: list[str] = Field(default_factory=list)
    bank_version: str


class EngineStatusOut(ApiModel):
    name: str
    available: bool
    detail: str
    version: str
    produces_real_pdfs: bool


class MetaResponse(ApiModel):
    app_version: str
    bank_version: str
    default_summary: str
    personal_info: dict[str, str]
    font_ladder: list[tuple[float, float]]
    max_pages_limit: int
    max_selected_projects: int
    max_jd_chars: int
    max_summary_chars: int
    engine: EngineStatusOut


class LivenessResponse(ApiModel):
    status: str
    app_version: str


class ReadinessResponse(ApiModel):
    ready: bool
    checks: dict[str, Any]


class Problem(ApiModel):
    """RFC 7807 problem document. The single error shape for the whole API."""

    type: str
    code: str
    title: str
    status: int
    detail: str
    instance: str | None = None
    context: dict[str, Any] | None = None
    log_tail: str | None = None
