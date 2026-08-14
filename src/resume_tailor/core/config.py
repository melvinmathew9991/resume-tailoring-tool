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
    data_dir: Path = _REPO_ROOT / "data"
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

    font_ladder: list[tuple[float, float]] = Field(
        default_factory=lambda: [
            (9.6, 11.5),
            (9.4, 11.3),
            (9.2, 11.0),
            (9.0, 10.8),
            (8.8, 10.6),
        ]
    )
    """(font_size, line_spacing) pairs, largest first. 8.8pt is a deliberate
    readability floor -- below it a slightly longer resume is the better
    trade. Carried over unchanged from the original design."""

    # -- input limits -------------------------------------------------------
    max_body_bytes: int = Field(default=1_048_576, ge=1024)
    max_jd_chars: int = Field(default=100_000, ge=1)
    max_summary_chars: int = Field(default=5_000, ge=1)
    max_personal_field_chars: int = Field(default=200, ge=1)
    max_selected_projects: int = Field(default=40, ge=1)
    max_pages_limit: int = Field(default=10, ge=1, le=10)
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
