"""Request and response models.

Two layers of limits on purpose. The caps here are static, generous and exist
to reject obvious abuse before any work happens; the precise, configurable
limits live in the service and produce the actionable error message. A schema
constant cannot depend on runtime settings without making the OpenAPI document
environment-dependent, and a stable published contract is worth more than
perfectly-matched numbers in two places.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Hard schema ceilings. The configurable limits are lower; see core/config.py.
MAX_JD_CHARS_HARD = 200_000
MAX_SUMMARY_CHARS_HARD = 20_000
MAX_SELECTED_PROJECTS_HARD = 100
MAX_KNOWLEDGE_TEXT_HARD = 400_000
#: Base64 inflates by four thirds, so this is the hard ceiling on the *encoded*
#: string. The real limit on the decoded document is
#: ``Settings.max_knowledge_document_bytes``, which produces the actionable
#: message; this one only stops an absurd body before anything decodes it.
MAX_DOCUMENT_B64_HARD = 12_000_000


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


class KnowledgeUpdateRequest(ApiModel):
    """Add candidate knowledge from pasted text or an uploaded document.

    The document arrives base64-encoded in this JSON body rather than as a
    multipart upload. ``UploadFile`` would pull in ``python-multipart``, a new
    runtime dependency for one field; base64 costs a third more bytes on a
    payload that is already capped by ``BodySizeLimitMiddleware`` and keeps
    every failure on the same RFC 7807 path as the rest of the API. Embedded
    mode skips the encoding entirely and hands the service raw bytes.
    """

    text: str | None = Field(default=None, max_length=MAX_KNOWLEDGE_TEXT_HARD)
    document_b64: str | None = Field(default=None, max_length=MAX_DOCUMENT_B64_HARD)
    filename: str | None = Field(default=None, max_length=300)
    """Required with ``document_b64``: the extension selects the reader."""
    label: str | None = Field(default=None, max_length=300)
    """What to call pasted text in the source list. Defaults to "pasted text"."""
    mode: Literal["merge", "supersede", "replace"] = "merge"
    """``merge`` adds what is new and keeps everything else. ``supersede``
    replaces the earlier version of *this* document -- matched by filename, or
    by ``label`` for pasted text -- and keeps every other document; it is
    refused if no document by that name is stored. ``replace`` starts the store
    again from this document alone. Only the last two remove anything."""

    @model_validator(mode="after")
    def _exactly_one_input(self) -> KnowledgeUpdateRequest:
        if (self.text is None) == (self.document_b64 is None):
            raise ValueError("provide exactly one of text or document_b64")
        if self.document_b64 is not None and not (self.filename or "").strip():
            raise ValueError("filename is required with document_b64, to select the reader")
        if self.text is not None and not self.text.strip():
            raise ValueError("text must contain non-whitespace characters")
        return self


class AtsRequest(ApiModel):
    """A job description, and nothing else. No resume is generated."""

    jd_text: str = Field(min_length=1, max_length=MAX_JD_CHARS_HARD)

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


class ResumeAtsRequest(ResumeRequest):
    """A resume selection plus the job description to score it against.

    Subclasses :class:`ResumeRequest` rather than restating its fields, so the
    body that scores a resume is the body that generates one with ``jd_text``
    added -- and the selection rules, the page bound and the boolean guard on
    ``max_pages`` cannot drift between the two.
    """

    jd_text: str = Field(min_length=1, max_length=MAX_JD_CHARS_HARD)

    @field_validator("jd_text")
    @classmethod
    def _jd_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("jd_text must contain non-whitespace characters")
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


# --- candidate knowledge (feature 1) ----------------------------------------


class KnowledgeEntryOut(ApiModel):
    category: str
    value: str
    display: str
    evidence: str
    """The verbatim line this fact came from. Part of the contract, not a
    debugging aid: it is what lets a reader check that nothing was invented."""
    source_id: str


class KnowledgeSourceOut(ApiModel):
    source_id: str
    label: str
    kind: str
    characters: int
    added_at: str


class ExperienceFactOut(ApiModel):
    title: str
    organisation: str
    dates: str
    months: int
    evidence: str
    source_id: str


class KnowledgeResponse(ApiModel):
    entries: list[KnowledgeEntryOut]
    experience: list[ExperienceFactOut]
    sources: list[KnowledgeSourceOut]
    counts_by_category: dict[str, int]
    entry_count: int
    experience_months: int
    """Total dated experience, overlapping roles counted once."""
    version: str
    is_empty: bool
    warnings: list[str] = Field(default_factory=list)
    """Content warnings, the counterpart of the project bank's lint output.

    Present so a caller can tell a healthy store from a structurally valid but
    obviously wrong one -- entry counts alone cannot, since the failure mode
    that actually occurs (prose read as a skills list) looks like a thorough
    document."""


class KnowledgeUpdateResponse(ApiModel):
    knowledge: KnowledgeResponse
    mode: str
    source_label: str
    source_kind: str
    characters_read: int
    added_entries: int
    duplicate_entries: int
    added_experience: int
    duplicate_experience: int
    added_by_category: dict[str, int]
    removed_entries: int = 0
    """Entries the earlier version of this document held and this one does not.
    Zero except in ``supersede`` mode."""
    removed_experience: int = 0
    superseded_sources: int = 0
    """How many stored versions of this document were replaced."""
    source_already_known: bool
    """True when this exact document was ingested before. With
    ``added_entries`` at zero it is the difference between "nothing new" and
    "something went wrong", which the UI has no other way to tell."""
    message: str


class KnowledgeDeleteResponse(ApiModel):
    knowledge: KnowledgeResponse
    removed: int


# --- ats match check (feature 2) --------------------------------------------


class RequirementOut(ApiModel):
    term: str
    display: str
    status: str
    """``exact``, ``related`` or ``missing``."""
    matched_term: str
    matched_sources: list[str]
    occurrences: int
    """Mentions in the job description. Reported for context; never scored."""
    detail: str
    priority: str = "required"
    """``required`` or ``preferred`` (a nice-to-have). A preferred requirement
    counts for ``priority_weights["preferred"]`` of a required one."""


class CategoryBreakdownOut(ApiModel):
    name: str
    label: str
    weight: float
    """Base category weight, scaled down by the share of preferred requirements."""
    score: float
    exact_count: int
    related_count: int
    missing_count: int
    preferred_count: int = 0
    requirements: list[RequirementOut]


class GateOut(ApiModel):
    """A hard filter. Only ``fail`` caps the score; ``unverified`` means the
    stored record cannot answer the question."""

    name: str
    label: str
    status: str
    """``pass``, ``fail`` or ``unverified``."""
    required: str
    found: str
    detail: str


class AtsResponse(ApiModel):
    score: int
    band: str
    breakdown: list[CategoryBreakdownOut]
    missing_requirements: list[str]
    weak_requirements: list[str]
    matched_requirements: list[str]
    requirement_count: int
    weights: dict[str, float]
    """Published with every response so the score can be recomputed by hand."""
    priority_weights: dict[str, float] = Field(default_factory=dict)
    """Weight of one requirement within its category, by priority."""
    gates: list[GateOut] = Field(default_factory=list)
    """Degree, years and location, for whichever the posting states as required."""
    uncapped_score: int = 0
    """The weighted score before a failed gate capped it."""
    capped: bool = False
    gate_cap: int = 0
    knowledge_version: str
    bank_version: str
    note: str


class ResumeAtsResponse(ApiModel):
    """How the assembled resume scores -- not how the candidate scores."""

    score: int
    band: str
    breakdown: list[CategoryBreakdownOut]
    missing_requirements: list[str]
    """Absent from the page. Some of these the candidate may still have."""
    weak_requirements: list[str]
    matched_requirements: list[str]
    covered_elsewhere: list[str]
    """Missing from this resume but present in the candidate's knowledge,
    profile or project bank. The actionable subset: a different project
    selection would cover these."""
    requirement_count: int
    weights: dict[str, float]
    priority_weights: dict[str, float] = Field(default_factory=dict)
    gates: list[GateOut] = Field(default_factory=list)
    uncapped_score: int = 0
    capped: bool = False
    gate_cap: int = 0
    project_keys: list[str]
    """Which selection was scored, so a stale score is detectable."""
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
