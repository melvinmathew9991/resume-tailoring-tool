"""Application service: the only entry point into the domain.

Both the FastAPI routes and the embedded-mode Streamlit UI call this object, so
there is exactly one implementation of "what happens when you generate a
resume" -- and therefore exactly one place where the selection rules, the
escaping rules and the concurrency limit are enforced.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import anyio

from resume_tailor.core.config import Settings
from resume_tailor.core.errors import (
    HiddenProjectError,
    InvalidInputError,
    UnknownProjectError,
    UnsafeContentError,
)
from resume_tailor.core.logging import get_logger
from resume_tailor.data.bank_repo import BankRepository, lint_bank
from resume_tailor.data.profile_repo import ProfileRepository
from resume_tailor.domain.latex import escape_user_text, find_dangerous_commands
from resume_tailor.domain.matching import MATCH_NOTE, find_gap_terms, match_bank
from resume_tailor.domain.models import (
    MatchReport,
    PersonalInfo,
    Profile,
    Project,
    ProjectBank,
    ResumeSpec,
)
from resume_tailor.render.engines.base import EngineStatus, PdfEngine
from resume_tailor.render.pagefit import FitResult, compile_with_page_fit
from resume_tailor.render.renderer import render_source
from resume_tailor.services.document_store import DocumentStore, StoredDocument

logger = get_logger(__name__)

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class GenerationResult:
    document: StoredDocument
    fit: FitResult
    bank_version: str


class ResumeService:
    def __init__(
        self,
        settings: Settings,
        bank_repo: BankRepository,
        profile_repo: ProfileRepository,
        engine: PdfEngine,
        document_store: DocumentStore | None = None,
    ) -> None:
        self._settings = settings
        self._bank_repo = bank_repo
        self._profile_repo = profile_repo
        self._engine = engine
        self._documents = document_store or DocumentStore(
            ttl_s=settings.document_ttl_s, max_documents=settings.max_stored_documents
        )
        # Bounds how many TeX processes can exist at once. Without it, N
        # concurrent requests meant N compilers competing for the same CPU
        # (defect C4).
        self._compile_semaphore = asyncio.Semaphore(settings.max_concurrent_compiles)

    # -- accessors ----------------------------------------------------------

    @property
    def documents(self) -> DocumentStore:
        return self._documents

    @property
    def engine(self) -> PdfEngine:
        return self._engine

    def bank(self) -> ProjectBank:
        return self._bank_repo.load()

    def profile(self) -> Profile:
        return self._profile_repo.load()

    def engine_status(self) -> EngineStatus:
        return self._engine.status()

    def readiness(self) -> dict[str, Any]:
        """Everything ``/health/ready`` needs, with failures reported not raised."""
        report: dict[str, Any] = {"ready": True, "checks": {}}

        try:
            bank = self.bank()
            report["checks"]["project_bank"] = {
                "ok": True,
                "projects": len(bank),
                "selectable": len(bank.visible()),
                "version": bank.version,
                "warnings": lint_bank(bank),
            }
        except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
            report["ready"] = False
            report["checks"]["project_bank"] = {"ok": False, "error": str(exc)}

        try:
            profile = self.profile()
            report["checks"]["profile"] = {
                "ok": True,
                "experience_entries": len(profile.experience),
            }
        except Exception as exc:  # noqa: BLE001
            report["ready"] = False
            report["checks"]["profile"] = {"ok": False, "error": str(exc)}

        status = self.engine_status()
        report["checks"]["pdf_engine"] = {
            "ok": status.available,
            "name": status.name,
            "detail": status.detail,
            "version": status.version,
            "produces_real_pdfs": status.name != "fake",
        }
        if not status.available:
            report["ready"] = False

        return report

    # -- matching -----------------------------------------------------------

    def match(self, jd_text: str, include_hidden: bool = False) -> MatchReport:
        if not jd_text or not jd_text.strip():
            raise InvalidInputError("job description text must not be empty")
        if len(jd_text) > self._settings.max_jd_chars:
            raise InvalidInputError(
                f"job description is {len(jd_text)} characters, over the "
                f"{self._settings.max_jd_chars} character limit"
            )

        bank = self.bank()
        return MatchReport(
            ranked_projects=match_bank(jd_text, bank, include_hidden=include_hidden),
            gap_terms=find_gap_terms(jd_text, bank, limit=self._settings.max_gap_terms),
            bank_version=bank.version,
            note=MATCH_NOTE,
        )

    # -- selection ----------------------------------------------------------

    def resolve_selection(self, keys: list[str]) -> tuple[list[str], list[Project]]:
        """Validate a project selection and return it in the requested order.

        Duplicates are collapsed while preserving first-seen order (the original
        rendered the same project twice, defect B7); an unknown key is an error
        rather than a silent drop; and a hidden project is refused here rather
        than only being filtered out of the listing (defect B3).
        """
        if not isinstance(keys, list):
            raise InvalidInputError(
                f"selected project keys must be a list, got {type(keys).__name__}"
            )
        if len(keys) > self._settings.max_selected_projects:
            raise InvalidInputError(
                f"{len(keys)} projects selected, over the limit of "
                f"{self._settings.max_selected_projects}"
            )

        # Element types are checked before anything hashes or indexes them. A
        # list element such as ``[["a"]]`` passed the original's
        # ``isinstance(list)`` check and then blew up on ``k not in bank`` with
        # ``TypeError: unhashable type`` -- an opaque 500 (defect B2).
        bad = [item for item in keys if not isinstance(item, str)]
        if bad:
            raise InvalidInputError(
                "every selected project key must be a string, got "
                + ", ".join(sorted({type(item).__name__ for item in bad}))
            )

        bank = self.bank()
        ordered: list[str] = list(dict.fromkeys(keys))

        unknown = [key for key in ordered if key not in bank.projects]
        if unknown:
            raise UnknownProjectError(
                f"unknown project key(s): {', '.join(sorted(unknown))}",
                unknown_keys=sorted(unknown),
            )

        hidden = [key for key in ordered if bank.projects[key].hidden]
        if hidden:
            raise HiddenProjectError(
                f"project(s) marked hidden cannot be put on a resume: {', '.join(sorted(hidden))}",
                hidden_keys=sorted(hidden),
            )

        return ordered, [bank.projects[key] for key in ordered]

    def build_spec(
        self,
        selected_keys: list[str],
        *,
        max_pages: int = 2,
        summary: str | None = None,
        personal_info: dict[str, Any] | PersonalInfo | None = None,
    ) -> ResumeSpec:
        """Assemble a validated ResumeSpec from a request."""
        if max_pages < 1 or max_pages > self._settings.max_pages_limit:
            raise InvalidInputError(
                f"max_pages must be between 1 and {self._settings.max_pages_limit}"
            )

        ordered_keys, projects = self.resolve_selection(selected_keys)
        profile = self.profile()

        if summary is not None:
            profile = profile.model_copy(update={"summary": self._sanitize_summary(summary)})
        if personal_info is not None:
            profile = profile.model_copy(
                update={"personal": self._sanitize_personal(profile.personal, personal_info)}
            )

        return ResumeSpec(
            profile=profile,
            projects=projects,
            project_keys=ordered_keys,
            max_pages=max_pages,
            bank_version=self.bank().version,
        )

    def _sanitize_summary(self, summary: str) -> str:
        """Escape a caller-supplied summary, then re-enable light markup.

        The original interpolated this string into the template untouched, which
        made ``\\input{/etc/passwd}`` a working file-read primitive (defect C1).
        """
        if len(summary) > self._settings.max_summary_chars:
            raise InvalidInputError(
                f"summary is {len(summary)} characters, over the "
                f"{self._settings.max_summary_chars} character limit"
            )
        dangerous = find_dangerous_commands(summary)
        if dangerous:
            raise UnsafeContentError(
                "the summary contains LaTeX commands that are not permitted: "
                + ", ".join(f"\\{name}" for name in dangerous)
                + ". Use **bold** and *italic* for formatting.",
                commands=dangerous,
            )
        cleaned = escape_user_text(summary)
        if not cleaned.strip():
            raise InvalidInputError("summary is empty after removing control characters")
        return cleaned

    def _sanitize_personal(
        self, base: PersonalInfo, override: dict[str, Any] | PersonalInfo
    ) -> PersonalInfo:
        """Validate and apply a personal-info override.

        ``PersonalInfo`` forbids unknown fields, so a caller can no longer smuggle
        a template variable name in here (defect B1), and a non-mapping value is
        a 400 rather than a ``TypeError`` 500 (defect B6).
        """
        if isinstance(override, PersonalInfo):
            patch: dict[str, Any] = override.model_dump(exclude_unset=True)
        elif isinstance(override, dict):
            patch = override
        else:
            raise InvalidInputError(
                f"personal_info must be an object, got {type(override).__name__}"
            )

        for field_name, value in patch.items():
            if isinstance(value, str):
                dangerous = find_dangerous_commands(value)
                if dangerous:
                    raise UnsafeContentError(
                        f"personal_info.{field_name} contains LaTeX commands that are "
                        "not permitted: " + ", ".join(f"\\{name}" for name in dangerous),
                        field=field_name,
                    )
                if len(value) > self._settings.max_personal_field_chars:
                    raise InvalidInputError(
                        f"personal_info.{field_name} is over the "
                        f"{self._settings.max_personal_field_chars} character limit"
                    )

        try:
            return base.merged_with(patch)
        except ValueError as exc:
            raise InvalidInputError(f"invalid personal_info: {exc}") from exc

    # -- rendering ----------------------------------------------------------

    def render_preview(self, spec: ResumeSpec) -> tuple[str, list[str]]:
        """Render the largest-font source without compiling.

        Fast, and -- importantly -- it works when no PDF engine is installed,
        so the tool still gives useful feedback on a bare machine.
        """
        font_size, line_spacing = self._settings.font_ladder[0]
        rendered = render_source(
            spec,
            font_size,
            line_spacing,
            template_dir=self._settings.template_dir,
            template_name=self._settings.template_name,
        )
        return rendered.tex, rendered.warnings

    def generate_sync(self, spec: ResumeSpec) -> GenerationResult:
        """Compile with the page-fit guarantee. Blocking; call off the event loop."""
        source_warnings: list[str] = []

        def render_fn(font_size: float, line_spacing: float) -> str:
            rendered = render_source(
                spec,
                font_size,
                line_spacing,
                template_dir=self._settings.template_dir,
                template_name=self._settings.template_name,
            )
            source_warnings[:] = rendered.warnings
            return rendered.tex

        fit = compile_with_page_fit(
            render_fn,
            self._engine,
            max_pages=spec.max_pages,
            font_ladder=self._settings.font_ladder,
            timeout_s=self._settings.compile_timeout_s,
        )
        fit = FitResult(
            pdf_bytes=fit.pdf_bytes,
            page_count=fit.page_count,
            font_size=fit.font_size,
            line_spacing=fit.line_spacing,
            attempts=fit.attempts,
            engine=fit.engine,
            warning=fit.warning,
            source_warnings=tuple(source_warnings),
        )

        document = self._documents.put(
            fit.pdf_bytes,
            filename=self._filename(spec),
            page_count=fit.page_count,
        )

        logger.info(
            "resume.generated",
            projects=len(spec.projects),
            pages=fit.page_count,
            font_size=fit.font_size,
            attempts=fit.attempts,
            engine=fit.engine,
            fits=fit.fits,
            bank_version=spec.bank_version,
        )
        return GenerationResult(document=document, fit=fit, bank_version=spec.bank_version)

    async def generate(self, spec: ResumeSpec) -> GenerationResult:
        """Async wrapper: bounded concurrency, off the event loop.

        Compilation is a multi-second blocking subprocess. Running it directly
        in an ``async def`` route would stall every other request on the server
        for its whole duration.
        """
        async with self._compile_semaphore:
            return await anyio.to_thread.run_sync(self.generate_sync, spec)

    @staticmethod
    def _filename(spec: ResumeSpec) -> str:
        name = _FILENAME_SAFE_RE.sub("_", spec.profile.personal.name).strip("_") or "resume"
        return f"{name}_Resume.pdf"
