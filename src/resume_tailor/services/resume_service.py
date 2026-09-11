"""Application service: the only entry point into the domain.

Both the FastAPI routes and the embedded-mode Streamlit UI call this object, so
there is exactly one implementation of "what happens when you generate a
resume" -- and therefore exactly one place where the selection rules, the
escaping rules and the concurrency limit are enforced.
"""

from __future__ import annotations

import asyncio
import re
import threading
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
from resume_tailor.data.knowledge_repo import KnowledgeRepository
from resume_tailor.data.profile_repo import ProfileRepository
from resume_tailor.domain.ats import AtsReport, CandidateCorpus, NamedCorpus, build_report
from resume_tailor.domain.extraction import (
    ExtractedDocument,
    extract_text,
    extract_text_from_paste,
)
from resume_tailor.domain.knowledge import (
    KnowledgeBase,
    MergeStats,
    extract_knowledge,
    merge_month_intervals,
    parse_experience_line,
)
from resume_tailor.domain.latex import (
    escape_user_text,
    find_dangerous_commands,
    latex_to_display_text,
)
from resume_tailor.domain.matching import (
    MATCH_NOTE,
    build_bank_haystack,
    find_gap_terms,
    match_bank,
    normalize,
)
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


@dataclass(frozen=True)
class ResumeAtsResult:
    """An ATS score for one assembled resume, plus what it left behind."""

    report: AtsReport
    covered_elsewhere: list[str]
    """Requirements this resume misses that the candidate nonetheless has,
    somewhere in their knowledge, profile or project bank. Not a gap in the
    person -- a gap between the person and the page, closable by choosing a
    different project."""


@dataclass(frozen=True)
class KnowledgeUpdateResult:
    """What one knowledge update did, in enough detail for the UI to explain it.

    ``stats`` is what makes a no-op visible: re-uploading a document the store
    already holds reports "0 added, 41 already known" rather than looking like
    a silent failure.
    """

    knowledge: KnowledgeBase
    stats: MergeStats
    document: ExtractedDocument
    mode: str


class ResumeService:
    def __init__(
        self,
        settings: Settings,
        bank_repo: BankRepository,
        profile_repo: ProfileRepository,
        engine: PdfEngine,
        document_store: DocumentStore | None = None,
        knowledge_repo: KnowledgeRepository | None = None,
    ) -> None:
        self._settings = settings
        self._bank_repo = bank_repo
        self._profile_repo = profile_repo
        # Defaulted rather than required: every existing caller constructs this
        # service with three repositories, and making the fourth mandatory would
        # be a breaking change to a constructor for no gain -- the path is
        # derivable from settings, which is where every other path comes from.
        self._knowledge_repo = knowledge_repo or KnowledgeRepository(settings.knowledge_path)
        # Every knowledge mutation is a read-modify-write. The repository's own
        # lock makes each individual write atomic, which stops a torn file but
        # not a lost update: two concurrent merges would both load the same
        # base and the later save would drop the earlier one's entries. This
        # serialises the whole cycle.
        self._knowledge_lock = threading.Lock()
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

    def knowledge(self) -> KnowledgeBase:
        return self._knowledge_repo.load()

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
        except Exception as exc:
            report["ready"] = False
            report["checks"]["project_bank"] = {"ok": False, "error": str(exc)}

        try:
            profile = self.profile()
            report["checks"]["profile"] = {
                "ok": True,
                "experience_entries": len(profile.experience),
            }
        except Exception as exc:
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

    # -- candidate knowledge (feature 1) ------------------------------------

    def update_knowledge(
        self,
        *,
        text: str | None = None,
        document: bytes | None = None,
        filename: str | None = None,
        label: str | None = None,
        mode: str = "merge",
    ) -> KnowledgeUpdateResult:
        """Extract candidate knowledge from a document or pasted text and store it.

        ``merge`` (the default) adds only what is new and never touches what is
        already there. ``supersede`` swaps in a new version of one document,
        matched by name, and keeps every other document. ``replace`` rebuilds
        the store from this input alone. Both lossy modes have to be asked for
        by name -- a caller that fat-fingers the mode gets an error, and one
        that omits it gets the safe behaviour, which is the right way round for
        an operation whose failure mode is losing weeks of accumulated
        knowledge.
        """
        if mode not in ("merge", "supersede", "replace"):
            raise InvalidInputError(f"mode must be 'merge', 'supersede' or 'replace', got {mode!r}")
        if (text is None) == (document is None):
            raise InvalidInputError(
                "provide exactly one of text or document -- pasted text or an uploaded file"
            )

        if document is not None:
            extracted = extract_text(
                document,
                filename or "",
                max_bytes=self._settings.max_knowledge_document_bytes,
            )
        else:
            # `text is None` was excluded above; the local keeps mypy in step
            # without an assert, which would be compiled out under -O.
            pasted = text or ""
            if len(pasted) > self._settings.max_knowledge_text_chars:
                raise InvalidInputError(
                    f"the pasted text is {len(pasted)} characters, over the "
                    f"{self._settings.max_knowledge_text_chars} character limit"
                )
            extracted = extract_text_from_paste(pasted, label=label or "pasted text")

        addition = extract_knowledge(extracted, max_entries=self._settings.max_knowledge_entries)

        # Extraction happens outside the lock -- it is the slow part (a PDF can
        # take a moment) and it touches nothing shared. The lock covers only
        # load-merge-save, which is the part that is otherwise a lost update:
        # two concurrent uploads would both read the same base and the second
        # save would silently discard the first one's entries.
        with self._knowledge_lock:
            if mode == "replace":
                merged, stats = KnowledgeBase().merged_with(addition)
            elif mode == "supersede":
                current = self.knowledge()
                if not current.sources_labelled(extracted.label):
                    # Refused rather than quietly treated as a merge. The caller
                    # asked for the old version to go; if it silently stayed
                    # because the filename changed, its stale facts would keep
                    # scoring and nothing on screen would say so.
                    raise InvalidInputError(_no_earlier_version(extracted.label, current))
                merged, stats = current.superseded_by(addition, extracted.label)
            else:
                merged, stats = self.knowledge().merged_with(addition)
            self._check_knowledge_limits(merged)
            stored = self._knowledge_repo.save(merged)
        logger.info(
            "knowledge.updated",
            mode=mode,
            source=extracted.label,
            kind=extracted.kind,
            added=stats.added_entries,
            removed=stats.removed_entries,
            duplicates=stats.duplicate_entries,
            entries=len(stored.entries),
            version=stored.version,
        )
        return KnowledgeUpdateResult(knowledge=stored, stats=stats, document=extracted, mode=mode)

    def _check_knowledge_limits(self, knowledge: KnowledgeBase) -> None:
        """Refuse an over-limit store with a message that names the way out.

        All three lists are checked, not just ``entries``. Each has a hard cap
        on the model, so a store that outgrows one of them would otherwise fail
        inside pydantic on every subsequent merge -- locking the user out of
        adding anything, with an error that names neither the cause nor the
        remedy. Sources are the realistic one: they accumulate one per
        document, forever, and nothing else prunes them.
        """
        settings = self._settings
        for label, count, limit in (
            ("entries", len(knowledge.entries), settings.max_knowledge_entries),
            ("experience items", len(knowledge.experience), settings.max_knowledge_experience),
            ("source documents", len(knowledge.sources), settings.max_knowledge_sources),
        ):
            if count > limit:
                raise InvalidInputError(
                    f"this update would take the knowledge store to {count} {label}, over the "
                    f"limit of {limit}. Remove what you no longer need, or start again with "
                    "mode=replace."
                )

    def remove_knowledge_entry(self, category: str, value: str) -> tuple[KnowledgeBase, int]:
        """Drop one entry. Returns the new store and how many entries went.

        The count is returned rather than swallowed so a caller that asked to
        remove something the store never had is told, instead of being left to
        assume it worked.
        """
        if not category.strip() or not value.strip():
            raise InvalidInputError("both category and value are required to remove an entry")
        with self._knowledge_lock:
            updated, removed = self.knowledge().without(category.strip(), value)
            if removed:
                self._knowledge_repo.save(updated)
        return updated, removed

    def clear_knowledge(self) -> KnowledgeBase:
        """Empty the store. Explicit, and the only wholesale delete."""
        with self._knowledge_lock:
            return self._knowledge_repo.save(KnowledgeBase())

    # -- ats match check (feature 2) ----------------------------------------

    def candidate_corpus(self) -> CandidateCorpus:
        """The candidate side of an ATS check, with every source kept distinct.

        Three surfaces, because all three are things this system already knows
        about this person, and excluding two of them would report gaps that the
        repository plainly contradicts. Tagging them separately is what lets a
        match say *where* the evidence lives -- and it reuses the two loaders
        and the bank haystack builder that already exist rather than growing a
        fourth way to read the same files.
        """
        sources: list[NamedCorpus] = []
        # Dated ranges are pooled and merged *once*, not totalled per source: a
        # role recorded in both the knowledge store and the profile is one job,
        # and adding two separately-merged totals would count it twice.
        intervals: list[tuple[int, int]] = []
        dated_from: list[str] = []
        # Education and location feed the hard filters, which must not read
        # degree words out of prose -- so they are collected from the fields
        # that hold them, not from the matching corpus.
        education_lines: list[str] = []
        location = ""

        knowledge = self.knowledge()
        education_lines.extend(
            entry.display for entry in knowledge.entries if entry.category == "education"
        )
        if not knowledge.is_empty:
            sources.append(NamedCorpus(name="knowledge", text=knowledge.corpus_text()))
        knowledge_intervals = knowledge.month_intervals()
        if knowledge_intervals:
            intervals.extend(knowledge_intervals)
            dated_from.append("knowledge")

        # A broken profile or bank must not take the checker down with it. This
        # feature is independent of resume generation, and that has to include
        # being independent of resume content being loadable.
        try:
            profile = self.profile()
        except Exception as exc:
            logger.warning("ats.profile_unavailable", error=str(exc))
        else:
            sources.append(NamedCorpus(name="profile", text=_profile_corpus(profile)))
            education_lines.extend(_education_lines(profile))
            location = profile.personal.location
            # The profile's dated history counts too. Without it a fresh
            # checkout -- where `knowledge.json` does not exist yet and
            # `profile.yaml` is fully populated -- scores every "N years"
            # requirement as missing, zeroing a 15%-weighted category for
            # someone whose experience this repository plainly holds.
            profile_intervals = _profile_intervals(profile)
            if profile_intervals:
                intervals.extend(profile_intervals)
                dated_from.append("profile")

        try:
            bank = self.bank()
        except Exception as exc:
            logger.warning("ats.bank_unavailable", error=str(exc))
        else:
            sources.append(NamedCorpus(name="bank", text=build_bank_haystack(bank)))

        return CandidateCorpus(
            sources=tuple(sources),
            experience_months=merge_month_intervals(intervals),
            experience_sources=tuple(dated_from),
            education_text=normalize(" \n ".join(education_lines)),
            location=location,
        )

    def ats_check_resume(self, jd_text: str, spec: ResumeSpec) -> ResumeAtsResult:
        """Score one assembled resume against a job description.

        Distinct from :meth:`ats_check`, and the distinction is the whole point.
        ``ats_check`` scores *the candidate* -- everything the system knows
        about them -- and answers "should I apply?". This scores *the document*,
        and answers "will this PDF get through the screen?".

        So the candidate side here is only what is actually printed: the
        profile, and the selected projects' titles and bullets, already trimmed
        to the bullet budget by ``build_spec``. The bank's keywords and domain
        tags are deliberately excluded -- they are matching metadata that never
        appears on the page, and counting them would score a resume for words
        no screen can see, which is the exact flattery this tool exists to
        avoid.

        Still generates nothing: the spec is read, never rendered.
        """
        if not jd_text or not jd_text.strip():
            raise InvalidInputError("job description text must not be empty")
        if len(jd_text) > self._settings.max_jd_chars:
            raise InvalidInputError(
                f"job description is {len(jd_text)} characters, over the "
                f"{self._settings.max_jd_chars} character limit"
            )

        # The page prints the profile's dated roles, its education and its
        # location, so those are what a screen can hold the hard filters
        # against. Leaving the dates out -- as this once did -- reported every
        # "N years" requirement unverifiable for a resume that states them.
        page_intervals = _profile_intervals(spec.profile)
        page = CandidateCorpus(
            sources=(NamedCorpus(name="resume", text=_spec_corpus(spec)),),
            experience_months=merge_month_intervals(page_intervals),
            experience_sources=("resume",) if page_intervals else (),
            education_text=normalize(" \n ".join(_education_lines(spec.profile))),
            location=spec.profile.personal.location,
        )
        report = build_report(jd_text, page, bank_version=spec.bank_version)

        # The gap between the page and the person. A requirement the resume
        # misses but the candidate demonstrably has is the most actionable line
        # in the whole report: it is not a gap in their experience, it is a
        # project they did not select, and it is fixable in one click.
        wider = self.candidate_corpus()
        covered_elsewhere = [
            requirement.display
            for category in report.breakdown
            for requirement in category.requirements
            if requirement.status == "missing" and wider.find(requirement.term)
        ]

        logger.info(
            "ats.checked_resume",
            score=report.score,
            requirements=report.requirement_count,
            missing=len(report.missing_requirements),
            recoverable=len(covered_elsewhere),
            projects=len(spec.projects),
        )
        return ResumeAtsResult(report=report, covered_elsewhere=covered_elsewhere)

    def ats_check(self, jd_text: str) -> AtsReport:
        """Score a job description against stored candidate knowledge.

        Generates nothing. This method does not touch the renderer, the
        template or the PDF engine, and there is no code path from here to a
        document -- the independence the PRD asks for is structural rather than
        a matter of the caller being careful.
        """
        if not jd_text or not jd_text.strip():
            raise InvalidInputError("job description text must not be empty")
        if len(jd_text) > self._settings.max_jd_chars:
            raise InvalidInputError(
                f"job description is {len(jd_text)} characters, over the "
                f"{self._settings.max_jd_chars} character limit"
            )

        corpus = self.candidate_corpus()
        if corpus.is_empty:
            raise InvalidInputError(
                "there is nothing to match against yet. Add your resume or profile under "
                "Profile knowledge first."
            )

        knowledge = self.knowledge()
        try:
            bank_version = self.bank().version
        except Exception:
            bank_version = ""

        report = build_report(
            jd_text,
            corpus,
            knowledge_version=knowledge.version,
            bank_version=bank_version,
        )
        logger.info(
            "ats.checked",
            score=report.score,
            requirements=report.requirement_count,
            missing=len(report.missing_requirements),
            sources=[source.name for source in corpus.sources],
        )
        return report

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
            projects=self._apply_bullet_budget(projects),
            project_keys=ordered_keys,
            max_pages=max_pages,
            bank_version=self.bank().version,
        )

    def _apply_bullet_budget(self, projects: list[Project]) -> list[Project]:
        """Trim each project's bullets to the budget for its rank position.

        Applied here rather than in the template because `build_spec` is the
        only route to the renderer, so preview and generate cannot disagree
        about what the resume contains -- a preview that showed five bullets
        and a PDF that printed three would be worse than either.

        Trimming takes the first N as written in the bank; bullet order in
        `project_bank.json` is the priority order. Projects beyond the budget
        cannot occur (`resolve_selection` enforces `max_selected_projects`, and
        a validator keeps the two in step), but `zip` would silently drop them
        if they did, so the budget is indexed explicitly and the last entry
        repeats rather than truncating the resume.
        """
        budget = self._settings.project_bullet_budget
        trimmed: list[Project] = []
        for position, project in enumerate(projects):
            allowed = budget[position] if position < len(budget) else budget[-1]
            if len(project.bullets) <= allowed:
                trimmed.append(project)
                continue
            trimmed.append(project.model_copy(update={"bullets": project.bullets[:allowed]}))
        return trimmed

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


#: How many stored document names a "no earlier version" error lists.
_MAX_NAMES_IN_ERROR = 10


def _no_earlier_version(label: str, knowledge: KnowledgeBase) -> str:
    """The refusal for a supersede with nothing to supersede, naming the fix."""
    names = sorted({source.label for source in knowledge.sources})
    listed = ", ".join(repr(name) for name in names[:_MAX_NAMES_IN_ERROR]) or "none"
    if len(names) > _MAX_NAMES_IN_ERROR:
        listed += f" and {len(names) - _MAX_NAMES_IN_ERROR} more"
    return (
        f"no stored document is called {label!r}, so there is no earlier version to "
        f"update. Stored documents: {listed}. Rename the file to match the one it "
        "replaces, or use merge to add it as a new document."
    )


def _spec_corpus(spec: ResumeSpec) -> str:
    """Exactly the text a reader -- or a screen -- would find on the page.

    Titles and bullets only, converted from LaTeX to display text. Project
    keywords and domain tags are absent on purpose: they drive matching in the
    bank, they are not printed, and including them would inflate the score of a
    resume for words nobody can see.
    """
    parts: list[str] = [_profile_corpus(spec.profile)]
    for project in spec.projects:
        parts.append(latex_to_display_text(project.title))
        parts.extend(latex_to_display_text(bullet) for bullet in project.bullets)
    return normalize(" \n ".join(part for part in parts if part))


def _education_lines(profile: Profile) -> list[str]:
    """Degree names from ``profile.yaml``. Institutions are left out: a name
    like "Master Institute of Technology" is not a master's degree."""
    return [entry.degree for entry in profile.education]


def _profile_intervals(profile: Profile) -> list[tuple[int, int]]:
    """Dated ranges from ``profile.yaml``, parsed the same way a document is.

    Reuses ``parse_experience_line`` rather than growing a second date parser:
    the profile writes its dates in exactly the form a resume does, and two
    parsers would eventually disagree about what "Aug 2024 -- Present" means.
    An entry whose ``dates`` field says nothing parseable is skipped, as an
    undated line is anywhere else.
    """
    intervals: list[tuple[int, int]] = []
    for entry in profile.experience:
        if not entry.dates.strip():
            continue
        fact = parse_experience_line(f"{entry.title} {entry.dates}", "profile")
        if fact is not None and fact.start_month is not None and fact.end_month is not None:
            intervals.append((fact.start_month, fact.end_month))
    return intervals


def _profile_corpus(profile: Profile) -> str:
    """The hand-maintained profile as one normalised haystack.

    Run through ``latex_to_display_text`` first: the profile stores
    ``\\textbf{Python}`` and ``50\\%``, and matching against the raw markup
    would miss terms a reader can plainly see on the page.
    """
    parts: list[str] = [latex_to_display_text(profile.summary)]
    for entry in profile.experience:
        parts.extend((entry.title, entry.company, latex_to_display_text(entry.subtitle)))
        parts.extend(latex_to_display_text(bullet) for bullet in entry.bullets)
    for skill in profile.skills:
        parts.append(f"{skill.name}: {latex_to_display_text(skill.content)}")
    for education in profile.education:
        parts.append(f"{education.degree} {education.institution} {education.dates}")
    return normalize(" \n ".join(part for part in parts if part))
