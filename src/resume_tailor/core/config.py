"""Application configuration.

Everything tunable lives here, is environment-driven (``RT_`` prefix), and is
validated at import time. A bad value fails the process at startup with a clear
message rather than surfacing as a confusing runtime error twenty minutes later.

The old code had magic numbers scattered across four modules (font ladder in
``pdf_compiler``, port in ``app``, paths derived by walking ``__file__``).
Consolidating them is what makes the limits in ``docs/PLAN.md`` section 3.5
enforceable and testable.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EngineName = Literal["auto", "tectonic", "pdflatex", "fake"]

# src/resume_tailor/core/config.py -> src/resume_tailor -> src -> <repo root>
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PACKAGE_ROOT.parents[1]


def _default_data_dir() -> Path:
    """Where ``project_bank.json`` and ``profile.yaml`` live, by default.

    Walking up from ``__file__`` gives the repository root under
    ``pip install -e``, and something meaningless under a real wheel: from
    ``site-packages/resume_tailor`` the same two steps land on the environment
    root, which has no ``data/`` and never will. Both container images set
    ``RT_DATA_DIR`` and so never noticed, but a plain ``pip install`` of this
    package failed at the first request with a ``BankError`` quoting a path no
    one would recognise.

    So the checkout layout is used when it is actually there, and the working
    directory is the fallback -- which is the right answer for an installed
    package run from a directory that holds the content. ``RT_DATA_DIR``
    overrides both, and remains the answer for anything deployed.
    """
    packaged = _REPO_ROOT / "data"
    return packaged if packaged.is_dir() else Path.cwd() / "data"


class Settings(BaseSettings):
    """Runtime configuration, read from the environment or a ``.env`` file."""

    model_config = SettingsConfigDict(
        env_prefix="RT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # -- identity -----------------------------------------------------------
    app_name: str = "resume-tailor"
    environment: Literal["local", "docker", "test", "prod"] = "local"
    debug: bool = False

    # -- logging ------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False
    """Human-readable console logs locally; structured JSON in Docker/prod."""

    # -- http ---------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_base_url: str = "http://127.0.0.1:8000"
    """Where the Streamlit UI looks for the API when running in http mode."""

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8501"])
    """Allowlist, not ``*``. The API drives a subprocess; a drive-by request
    from any open browser tab must not be able to reach it (defect C3)."""

    api_key: str | None = None
    """Optional shared secret. When set, every /api route requires X-API-Key."""

    # -- paths --------------------------------------------------------------
    data_dir: Path = Field(default_factory=_default_data_dir)
    bank_filename: str = "project_bank.json"
    profile_filename: str = "profile.yaml"
    template_dir: Path = _PACKAGE_ROOT / "render" / "templates"
    template_name: str = "resume.tex.j2"

    # -- pdf engine ---------------------------------------------------------
    pdf_engine: EngineName = "auto"
    compile_timeout_s: float = Field(default=60.0, gt=0, le=600)
    max_concurrent_compiles: int = Field(default=2, ge=1, le=32)
    """Bounds CPU/RAM: N in-flight requests must not mean N TeX processes
    (defect C4)."""

    font_ladder: list[tuple[float, float]] = Field(default_factory=lambda: [(9.2, 11.0)])
    """(font_size, line_spacing) pairs, largest first.

    A single rung on purpose. This used to be a five-step ladder from 9.6 down
    to 8.8 that shrank the type until the document fit the page limit, which
    guaranteed a fit but meant two resumes generated a week apart could be set
    at different sizes. The format is now fixed at 9.2/11.0 and overflow is
    reported instead of absorbed: `compile_with_page_fit` walks whatever rungs
    exist, so one rung compiles exactly once and warns if the result is longer
    than `max_pages`.

    That warning is the feature. Silently shrinking to 8.8pt to hide a
    three-page resume solved the symptom; saying "this is 3 pages, cut
    something" addresses the cause, and the author is the only one who can
    decide what to cut.

    Adding rungs restores the old shrink-to-fit behaviour with no code change.
    """

    # -- input limits -------------------------------------------------------
    max_body_bytes: int = Field(default=1_048_576, ge=1024)
    max_jd_chars: int = Field(default=100_000, ge=1)
    max_summary_chars: int = Field(default=5_000, ge=1)
    max_personal_field_chars: int = Field(default=200, ge=1)
    max_selected_projects: int = Field(default=5, ge=1)
    """Kept in step with `project_bullet_budget` by a validator below."""

    project_bullet_budget: list[int] = Field(default_factory=lambda: [5, 5, 3, 3, 1])
    """Bullets kept per project, by rank position.

    The resume gives its strongest two projects five bullets, the next two
    three, and the last a single line. A project carrying ten good bullets in
    the bank still only spends what its slot allows, so the shape of the page
    is a property of the layout rather than of whichever projects happened to
    be selected.

    Trimming takes the first N as written in `project_bank.json`, so bullet
    order in the file *is* the priority order -- put the strongest first.
    """

    max_pages_limit: int = Field(default=2, ge=1, le=10)
    """Hard ceiling, matching ``ResumeSpec.max_pages``. An unbounded value was
    accepted by the original API (``max_pages: 999999999``), which turned the
    page-fit guarantee into a no-op (defect B5)."""
    max_gap_terms: int = Field(default=30, ge=1)

    # -- rate limiting ------------------------------------------------------
    rate_limit_per_minute: int = Field(default=120, ge=1)
    generate_rate_limit_per_minute: int = Field(default=12, ge=1)
    """Compilation is far more expensive than the read endpoints, so it gets
    its own, tighter budget."""

    # -- document store -----------------------------------------------------
    document_ttl_s: int = Field(default=900, ge=1)
    max_stored_documents: int = Field(default=32, ge=1)

    @property
    def bank_path(self) -> Path:
        return self.data_dir / self.bank_filename

    @property
    def profile_path(self) -> Path:
        return self.data_dir / self.profile_filename

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv_origins(cls, value: object) -> object:
        """Accept ``RT_CORS_ORIGINS=a,b`` as well as a JSON list, because the
        comma form is what people actually type into a compose file."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("project_bullet_budget")
    @classmethod
    def _validate_bullet_budget(cls, budget: list[int]) -> list[int]:
        if not budget:
            raise ValueError("project_bullet_budget must not be empty")
        if any(count < 1 for count in budget):
            raise ValueError(f"project_bullet_budget entries must be >= 1, got {budget}")
        return budget

    @model_validator(mode="after")
    def _budget_covers_every_slot(self) -> Settings:
        """Every selectable project must have a bullet budget.

        These two are independent settings that mean nothing apart: a cap of 5
        with a four-entry budget would leave the fifth project with no rule,
        and the failure would surface as a resume with a silently empty
        project rather than as a configuration error.
        """
        if len(self.project_bullet_budget) != self.max_selected_projects:
            raise ValueError(
                f"project_bullet_budget has {len(self.project_bullet_budget)} entries "
                f"but max_selected_projects is {self.max_selected_projects}; "
                "they must match"
            )
        return self

    @field_validator("font_ladder")
    @classmethod
    def _validate_font_ladder(cls, ladder: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not ladder:
            raise ValueError("font_ladder must contain at least one (size, spacing) pair")
        for size, spacing in ladder:
            if size <= 0 or spacing <= 0:
                raise ValueError(f"font_ladder entries must be positive, got ({size}, {spacing})")
            if spacing < size:
                raise ValueError(
                    f"line spacing {spacing} is smaller than font size {size}; lines would overlap"
                )
        sizes = [size for size, _ in ladder]
        if sizes != sorted(sizes, reverse=True):
            raise ValueError("font_ladder must be ordered largest font size first")
        return ladder

    @model_validator(mode="after")
    def _validate_deployment_posture(self) -> Settings:
        if self.environment == "prod":
            if "*" in self.cors_origins:
                raise ValueError("cors_origins must not contain '*' in a prod environment")
            if self.debug:
                raise ValueError("debug must be False in a prod environment")
        return self


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that reading configuration is free at call sites; tests clear the
    cache via :func:`reset_settings` rather than reaching into globals.
    """
    return Settings()


def reset_settings() -> None:
    """Drop the cached settings. Test-only hook."""
    get_settings.cache_clear()
