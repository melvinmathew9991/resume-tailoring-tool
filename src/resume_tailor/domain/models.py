"""Domain models.

Every value that crosses a layer boundary is one of these. The original code
passed raw ``dict`` objects from HTTP body straight into ``template.render()``,
which is what made the reserved-key crash (B1), the unhashable-key crash (B2)
and the un-typed ``personal_info`` crash (B6) all possible at once. A validated
model at the boundary makes that entire class of failure unrepresentable.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
#: ``owner/repo`` or a bare ``owner``. The bare form is a profile link rather
#: than a project link -- two entries in the current bank use it, so it is
#: valid, but :func:`resume_tailor.data.bank_repo.lint_bank` reports it as a
#: content warning because a project bullet pointing at a profile page is
#: almost always an oversight.
GITHUB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?$")

ProjectKey = Annotated[str, Field(pattern=KEY_RE.pattern, max_length=64)]


class StrictModel(BaseModel):
    """Base for every domain model: unknown fields are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


# --- project bank -----------------------------------------------------------


class Project(StrictModel):
    """One entry in the project bank.

    ``title`` and ``bullets`` hold *pre-verified, LaTeX-formatted* text and are
    used verbatim. They are never escaped and never rewritten -- that boundary
    is the whole point of the tool.
    """

    title: str = Field(min_length=1, max_length=300)
    github: str = Field(min_length=3, max_length=200)
    domain: list[str] = Field(default_factory=list, max_length=32)
    keywords: list[str] = Field(default_factory=list, max_length=128)
    bullets: list[str] = Field(min_length=1, max_length=40)
    hidden: bool = False
    """Excluded from listing, matching *and* generation.

    In the old code the single hidden project was a hardcoded string literal in
    the API layer, so /api/generate would happily put it on a resume anyway
    (defect B3). Hiddenness belongs to the data.
    """

    @field_validator("github")
    @classmethod
    def _normalise_github(cls, value: str) -> str:
        """Accept a full URL but store the canonical ``owner/repo`` form.

        The template builds ``\\href{https://github.com/<value>}``. An
        unvalidated value containing ``}``, ``%`` or ``#`` breaks out of the
        href argument or truncates the line (defect B12); constraining the
        shape here removes the possibility rather than escaping around it.
        """
        cleaned = value.strip()
        for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix) :]
                break
        # Only a *trailing* slash is a formatting variation worth absorbing. A
        # leading one means the value is malformed, and silently rewriting
        # "/repo" into the profile link for a user named "repo" would ship a
        # wrong URL onto a resume rather than reporting the mistake.
        cleaned = cleaned.rstrip("/")
        if not GITHUB_RE.match(cleaned):
            raise ValueError(f"github must be 'owner/repo' using [A-Za-z0-9._-], got {value!r}")
        return cleaned

    @field_validator("domain", "keywords")
    @classmethod
    def _normalise_terms(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for term in values:
            term = term.strip().lower()
            if not term:
                raise ValueError("keyword/domain entries must not be empty")
            if len(term) > 64:
                raise ValueError(f"keyword/domain entry too long: {term[:32]}...")
            if term not in cleaned:
                cleaned.append(term)
        return cleaned

    @field_validator("bullets")
    @classmethod
    def _validate_bullets(cls, values: list[str]) -> list[str]:
        for index, bullet in enumerate(values):
            if not bullet.strip():
                raise ValueError(f"bullet {index} is empty")
            if len(bullet) > 1000:
                raise ValueError(f"bullet {index} exceeds 1000 characters")
        return values

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.github}"


class ProjectBank(StrictModel):
    """The full bank, plus a content hash used for caching and reproducibility."""

    projects: dict[str, Project]
    version: str
    """SHA-256 (first 12 hex chars) of the canonical bank content. Surfaced in
    every API response so the UI can detect that the bank changed underneath a
    session, and so a generated PDF can be tied to exact input content."""

    @field_validator("projects")
    @classmethod
    def _validate_keys(cls, projects: dict[str, Project]) -> dict[str, Project]:
        for key in projects:
            if not KEY_RE.match(key):
                raise ValueError(f"project key {key!r} must be lowercase alphanumeric/underscore")
        return projects

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ProjectBank:
        projects = {key: Project.model_validate(value) for key, value in raw.items()}
        return cls(projects=projects, version=compute_version(raw))

    def visible(self) -> dict[str, Project]:
        return {key: project for key, project in self.projects.items() if not project.hidden}

    def __len__(self) -> int:
        return len(self.projects)


def compute_version(raw: dict[str, Any]) -> str:
    """Stable content hash: key order and whitespace must not change it."""
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# --- resume profile (the parts that are not projects) -----------------------


class PersonalInfo(StrictModel):
    """Header block. Every field is free text and is escaped before rendering.

    ``extra="forbid"`` (inherited) is what closes defect B1: a caller can no
    longer smuggle ``font_size`` or ``summary`` in here and crash the renderer
    with a duplicate keyword argument.
    """

    name: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=64)
    location: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    linkedin_url: str = Field(default="", max_length=500)
    linkedin_display: str = Field(default="", max_length=200)
    github_url: str = Field(default="", max_length=500)
    github_display: str = Field(default="", max_length=200)

    @field_validator("linkedin_url", "github_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("URLs must start with http:// or https://")
        if any(char in value for char in "{}%#\\ "):
            raise ValueError("URL contains characters that are unsafe inside \\href{}")
        return value

    def merged_with(self, override: PersonalInfo | dict[str, Any] | None) -> PersonalInfo:
        """Return a copy with non-empty override fields applied.

        Re-validated rather than ``model_copy(update=...)``: ``model_copy``
        skips validators entirely, so an override carrying a malformed URL
        would sail straight through into the ``\\href{}`` argument.
        """
        if override is None:
            return self
        data = override if isinstance(override, dict) else override.model_dump(exclude_unset=True)
        patch = {key: value for key, value in data.items() if value not in (None, "")}
        if not patch:
            return self
        return PersonalInfo.model_validate({**self.model_dump(), **patch})


class ExperienceEntry(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    dates: str = Field(default="", max_length=100)
    subtitle: str = Field(default="", max_length=500)
    bullets: list[str] = Field(default_factory=list, max_length=20)


class SkillCategory(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=2000)


class EducationEntry(StrictModel):
    degree: str = Field(min_length=1, max_length=200)
    institution: str = Field(min_length=1, max_length=200)
    dates: str = Field(default="", max_length=100)


class Profile(StrictModel):
    """Everything on the resume that is not a project.

    Previously hardcoded as module-level Python constants in
    ``resume_builder.py`` (defect S2) -- changing a phone number meant editing
    source. Now loaded from ``data/profile.yaml``.
    """

    personal: PersonalInfo
    summary: str = Field(min_length=1, max_length=5000)
    experience: list[ExperienceEntry] = Field(default_factory=list, max_length=20)
    skills: list[SkillCategory] = Field(default_factory=list, max_length=30)
    education: list[EducationEntry] = Field(default_factory=list, max_length=10)


# --- matching ---------------------------------------------------------------


class KeywordHit(StrictModel):
    keyword: str
    occurrences: int = Field(ge=1)


class MatchResult(StrictModel):
    key: str
    title: str
    score: int = Field(ge=0)
    matched_keywords: list[str] = Field(default_factory=list)
    keyword_hits: list[KeywordHit] = Field(default_factory=list)
    matched_domains: list[str] = Field(default_factory=list)
    domain_match: bool = False
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    """Fraction of the project's own keywords that the JD mentions. Score alone
    favours projects with long keyword lists; coverage does not."""


class MatchReport(StrictModel):
    ranked_projects: list[MatchResult]
    gap_terms: list[str]
    bank_version: str
    note: str


# --- resume specification ---------------------------------------------------


class ResumeSpec(StrictModel):
    """The validated, fully-resolved input to rendering.

    Building one of these is the *only* way to reach the renderer, so every
    render is guaranteed to have passed validation. ``selected_keys`` is already
    de-duplicated, order-preserved, known-good and non-hidden by the time it
    gets here (defects B2, B3, B7).
    """

    profile: Profile
    projects: list[Project]
    project_keys: list[str]
    max_pages: int = Field(ge=1, le=10)
    bank_version: str

    @model_validator(mode="after")
    def _keys_align(self) -> ResumeSpec:
        if len(self.projects) != len(self.project_keys):
            raise ValueError("projects and project_keys must be the same length")
        return self
