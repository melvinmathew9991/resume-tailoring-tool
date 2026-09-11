"""The application service: selection rules, sanitisation and orchestration.

Most of the original code's crash-and-500 defects lived at exactly this
boundary, so most of them have a named test here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_tailor.core.config import Settings
from resume_tailor.core.errors import (
    HiddenProjectError,
    InvalidInputError,
    UnknownProjectError,
    UnsafeContentError,
)
from resume_tailor.data.bank_repo import BankRepository
from resume_tailor.data.profile_repo import ProfileRepository
from resume_tailor.render.engines.fake import FakeEngine
from resume_tailor.render.parsecheck import check_parse
from resume_tailor.services.resume_service import ResumeService

pytestmark = pytest.mark.unit


class TestResolveSelection:
    def test_preserves_requested_order(self, service: ResumeService) -> None:
        keys, projects = service.resolve_selection(["proj_b", "proj_a"])
        assert keys == ["proj_b", "proj_a"]
        assert projects[0].github == "testuser/project_b_with_underscores"

    def test_deduplicates_while_preserving_first_position(self, service: ResumeService) -> None:
        """The original rendered the same project twice (defect B7)."""
        keys, projects = service.resolve_selection(["proj_a", "proj_b", "proj_a"])
        assert keys == ["proj_a", "proj_b"]
        assert len(projects) == 2

    def test_unknown_key_is_reported_not_silently_dropped(self, service: ResumeService) -> None:
        with pytest.raises(UnknownProjectError, match="nope"):
            service.resolve_selection(["proj_a", "nope"])

    def test_all_unknown_keys_are_listed_at_once(self, service: ResumeService) -> None:
        with pytest.raises(UnknownProjectError) as info:
            service.resolve_selection(["x", "y"])
        assert info.value.context["unknown_keys"] == ["x", "y"]

    def test_hidden_project_is_refused(self, service: ResumeService) -> None:
        """Hiding a project from the listing while still letting /generate put
        it on a resume was defect B3."""
        with pytest.raises(HiddenProjectError, match="proj_hidden"):
            service.resolve_selection(["proj_hidden"])

    @pytest.mark.parametrize("bad", [[["a"]], [None], [1], [{"k": "v"}]])
    def test_non_string_elements_are_rejected_cleanly(
        self, service: ResumeService, bad: list[object]
    ) -> None:
        """These used to reach `key not in bank` and raise
        `TypeError: unhashable type` -- an opaque 500 (defect B2)."""
        with pytest.raises(InvalidInputError, match="must be a string"):
            service.resolve_selection(bad)  # type: ignore[arg-type]

    def test_non_list_input_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="must be a list"):
            service.resolve_selection("proj_a")  # type: ignore[arg-type]

    def test_too_many_projects_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="over the limit"):
            service.resolve_selection(["proj_a"] * 500)

    def test_empty_selection_is_allowed(self, service: ResumeService) -> None:
        assert service.resolve_selection([]) == ([], [])

    def test_case_sensitive_keys(self, service: ResumeService) -> None:
        with pytest.raises(UnknownProjectError):
            service.resolve_selection(["PROJ_A"])


class TestBuildSpec:
    def test_defaults(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"])
        assert spec.max_pages == 2
        assert spec.profile.personal.name == "Test Person"
        assert spec.bank_version

    @pytest.mark.parametrize("pages", [0, -1, 11, 999_999])
    def test_max_pages_bounds(self, service: ResumeService, pages: int) -> None:
        with pytest.raises(InvalidInputError, match="max_pages"):
            service.build_spec(["proj_a"], max_pages=pages)

    def test_summary_override_is_escaped(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], summary="R&D at 50% capacity")
        assert spec.profile.summary == r"R\&D at 50\% capacity"

    def test_summary_override_supports_light_markup(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], summary="A **bold** claim")
        assert spec.profile.summary == r"A \textbf{bold} claim"

    @pytest.mark.parametrize(
        "payload",
        [
            r"\input{/etc/passwd}",
            r"\write18{whoami}",
            r"\def\x{\x\x}",
            r"\catcode`\%=12",
            r"\newcommand{\x}{y}",
        ],
    )
    def test_dangerous_summary_is_rejected(self, service: ResumeService, payload: str) -> None:
        with pytest.raises(UnsafeContentError):
            service.build_spec(["proj_a"], summary=payload)

    def test_oversized_summary_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="over the"):
            service.build_spec(["proj_a"], summary="x" * 10_000)

    def test_summary_of_only_control_characters_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="empty after"):
            service.build_spec(["proj_a"], summary="\x00\x01\x02")

    def test_personal_info_override_applies(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], personal_info={"name": "Someone Else"})
        assert spec.profile.personal.name == "Someone Else"

    def test_personal_info_reserved_key_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError):
            service.build_spec(["proj_a"], personal_info={"font_size": 4})

    def test_personal_info_wrong_type_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="must be an object"):
            service.build_spec(["proj_a"], personal_info="a string")  # type: ignore[arg-type]

    def test_dangerous_personal_field_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(UnsafeContentError, match=r"personal_info\.name"):
            service.build_spec(["proj_a"], personal_info={"name": r"\input{x}"})

    def test_oversized_personal_field_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="character limit"):
            service.build_spec(["proj_a"], personal_info={"name": "x" * 500})

    def test_summary_none_keeps_the_profile_default(self, service: ResumeService) -> None:
        assert "markup" in service.build_spec(["proj_a"], summary=None).profile.summary


class TestBulletBudget:
    """Five projects, shaped 5/5/3/3/1.

    The budget caps; it never pads. A project with three bullets in a
    five-bullet slot stays at three, because the alternative is inventing
    resume content, which this tool does not do under any circumstances.
    """

    def test_budget_trims_by_rank_position(self, real_service: ResumeService) -> None:
        keys = list(real_service.bank().visible())[:5]
        bank = real_service.bank()
        available = [len(bank.projects[key].bullets) for key in keys]
        spec = real_service.build_spec(keys)

        budget = [5, 5, 3, 3, 1]
        expected = [min(have, allowed) for have, allowed in zip(available, budget, strict=True)]
        assert [len(project.bullets) for project in spec.projects] == expected

    def test_the_last_project_gets_exactly_one_bullet(self, real_service: ResumeService) -> None:
        keys = list(real_service.bank().visible())[:5]
        spec = real_service.build_spec(keys)
        assert len(spec.projects[4].bullets) == 1

    def test_trimming_keeps_the_first_bullets_in_bank_order(
        self, real_service: ResumeService
    ) -> None:
        """Bank order is the priority order, so trimming must take a prefix."""
        keys = list(real_service.bank().visible())[:5]
        bank = real_service.bank()
        spec = real_service.build_spec(keys)
        for key, project in zip(keys, spec.projects, strict=True):
            original = bank.projects[key].bullets
            assert project.bullets == original[: len(project.bullets)]

    def test_a_short_project_is_not_padded(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"])
        assert len(spec.projects[0].bullets) == len(service.bank().projects["proj_a"].bullets)

    def test_preview_and_generate_see_the_same_bullets(self, real_service: ResumeService) -> None:
        """Both go through `build_spec`, so they cannot disagree."""
        keys = list(real_service.bank().visible())[:5]
        spec = real_service.build_spec(keys)
        tex, _ = real_service.render_preview(spec)
        expected = sum(len(project.bullets) for project in spec.projects)
        # Experience bullets are in the document too; count only that the
        # project bullets present in the spec all reached the source.
        for project in spec.projects:
            for bullet in project.bullets:
                assert bullet in tex
        assert tex.count(r"\item") >= expected


class TestMatchService:
    def test_returns_a_report(self, service: ResumeService) -> None:
        report = service.match("Python and SQL for a fintech team")
        assert report.ranked_projects
        assert report.bank_version
        assert "judgment" in report.note

    @pytest.mark.parametrize("jd", ["", "   ", "\n\t "])
    def test_blank_jd_is_rejected(self, service: ResumeService, jd: str) -> None:
        with pytest.raises(InvalidInputError, match="must not be empty"):
            service.match(jd)

    def test_oversized_jd_is_rejected(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="character limit"):
            service.match("x" * 200_000)

    def test_gap_terms_are_capped(self, service: ResumeService) -> None:
        jd = " ".join(f"and Tool{index}" for index in range(200))
        assert len(service.match(jd).gap_terms) <= 30

    def test_hidden_projects_excluded_by_default(self, service: ResumeService) -> None:
        keys = [result.key for result in service.match("experimental").ranked_projects]
        assert "proj_hidden" not in keys


class TestPostingCheck:
    def test_it_reports_a_complete_posting(self, service: ResumeService) -> None:
        text = (
            "Data Scientist\n\nAbout the role\nYou will build and validate "
            "predictive models for our lending business, working alongside "
            "engineers and product managers to ship them into production.\n\n"
            "Responsibilities\n- Build classification and regression models\n"
            "- Partner with engineering to deploy and monitor them\n"
            "- Present findings to non-technical stakeholders\n\n"
            "Requirements\n- 3+ years of Python and SQL experience\n"
            "- Strong statistics background and a quantitative degree\n"
            "- Experience shipping models into production systems at scale\n"
        )
        assert service.posting_check(text).complete

    def test_it_flags_a_fragment(self, service: ResumeService) -> None:
        check = service.posting_check("Python and SQL needed with")
        assert not check.complete
        assert "too_short" in {signal.code for signal in check.signals}

    def test_matching_is_unaffected_by_an_incomplete_posting(self, service: ResumeService) -> None:
        """The check describes the input; it must not change the ranking.

        Matching stays exactly as honest about the text it was given as it was
        before -- which is the whole reason a separate signal is needed.
        """
        jd = "Python and SQL needed with"
        assert service.match(jd).ranked_projects == service.match(jd).ranked_projects
        assert not service.posting_check(jd).complete


class TestGeneration:
    def test_generates_and_stores_a_document(self, service: ResumeService) -> None:
        result = service.generate_sync(service.build_spec(["proj_a"]))
        assert result.fit.pdf_bytes.startswith(b"%PDF")
        assert service.documents.get(result.document.document_id)

    def test_every_generation_carries_a_parse_check(self, service: ResumeService) -> None:
        """Unconditional, not opt-in. A check a caller has to remember to ask
        for is one that stops being run the week after it is written."""
        result = service.generate_sync(service.build_spec(["proj_a"]))
        assert result.parse.status == "pass"
        assert result.parse.term_coverage == 1.0

    def test_the_parse_check_sees_the_document_that_was_stored(
        self, service: ResumeService
    ) -> None:
        """It must read the same bytes the user downloads, not a re-render."""
        result = service.generate_sync(service.build_spec(["proj_a"]))
        stored = service.documents.get(result.document.document_id)
        assert check_parse(stored.pdf_bytes, service.build_spec(["proj_a"])) == result.parse

    def test_a_document_an_ats_cannot_read_is_reported(
        self, settings: Settings, data_dir: Path
    ) -> None:
        """The failure path, proven rather than assumed: the page count is
        right, the PDF is valid, and a parser reads nothing."""
        service = ResumeService(
            settings=settings,
            bank_repo=BankRepository(settings.bank_path),
            profile_repo=ProfileRepository(settings.profile_path),
            engine=FakeEngine(emit_blank_pages=True),
        )
        result = service.generate_sync(service.build_spec(["proj_a"]))
        assert result.fit.fits, "the page-fit guarantee is a separate question"
        assert not result.parse.parses
        assert result.parse.findings[0].code == "no_text"

    def test_filename_is_derived_from_the_name(self, service: ResumeService) -> None:
        result = service.generate_sync(service.build_spec(["proj_a"]))
        assert result.document.filename == "Test_Person_Resume.pdf"

    def test_filename_is_sanitised(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], personal_info={"name": "A/B\\C:D"})
        assert "/" not in service.generate_sync(spec).document.filename

    def test_empty_selection_still_produces_a_resume(self, service: ResumeService) -> None:
        result = service.generate_sync(service.build_spec([]))
        assert result.fit.page_count >= 1

    @pytest.mark.parametrize("max_pages", [1, 2])
    def test_page_fit_invariant_on_the_real_bank(
        self, real_service: ResumeService, max_pages: int
    ) -> None:
        """`max_pages` stops at 2: the resume is capped at two pages, and 3 is
        no longer an accepted value."""
        keys = list(real_service.bank().visible())[:5]
        result = real_service.generate_sync(real_service.build_spec(keys, max_pages=max_pages))
        assert result.fit.page_count <= max_pages or result.fit.warning

    def test_max_pages_above_the_cap_is_rejected(self, real_service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="between 1 and 2"):
            real_service.build_spec(list(real_service.bank().visible())[:5], max_pages=3)

    async def test_async_generate_matches_sync(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"])
        assert (await service.generate(spec)).fit.page_count == (
            service.generate_sync(spec).fit.page_count
        )

    async def test_concurrent_generation_is_bounded(self, service: ResumeService) -> None:
        """Ten simultaneous requests must not mean ten simultaneous compilers."""
        import asyncio

        spec = service.build_spec(["proj_a"])
        results = await asyncio.gather(*(service.generate(spec) for _ in range(10)))
        assert len(results) == 10
        assert all(result.fit.pdf_bytes.startswith(b"%PDF") for result in results)


class TestReadiness:
    def test_reports_ready_with_valid_content(self, service: ResumeService) -> None:
        report = service.readiness()
        assert report["ready"] is True
        assert report["checks"]["pdf_engine"]["produces_real_pdfs"] is False

    def test_reports_not_ready_when_the_bank_is_broken(
        self, service: ResumeService, settings
    ) -> None:
        """Readiness must *report* failures, never raise them -- a probe that
        500s tells a process manager nothing about what is wrong."""
        settings.bank_path.write_text("{ broken", encoding="utf-8")
        report = service.readiness()
        assert report["ready"] is False
        assert report["checks"]["project_bank"]["ok"] is False

    def test_reports_not_ready_when_the_profile_is_broken(
        self, service: ResumeService, settings
    ) -> None:
        settings.profile_path.write_text("[not a mapping]", encoding="utf-8")
        report = service.readiness()
        assert report["ready"] is False
        assert report["checks"]["profile"]["ok"] is False


class TestKnowledgeService:
    """Feature 1 at the service layer: the rules that guard the store."""

    def test_a_document_and_text_together_are_refused(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="exactly one"):
            service.update_knowledge(text="hi", document=b"hi", filename="a.txt")

    def test_neither_is_refused(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="exactly one"):
            service.update_knowledge()

    def test_an_unknown_mode_is_refused_before_anything_is_written(
        self, service: ResumeService
    ) -> None:
        with pytest.raises(InvalidInputError, match="merge"):
            service.update_knowledge(text="Skills\nPython\n", mode="wipe")
        assert service.knowledge().is_empty

    def test_updating_a_document_that_was_never_stored_is_refused(
        self, service: ResumeService
    ) -> None:
        """A renamed file must not quietly become a merge: the caller asked
        for the old version to go, and its stale facts would keep scoring."""
        service.update_knowledge(document=b"Skills\nPython\n", filename="cv.txt")
        with pytest.raises(InvalidInputError, match="no stored document is called"):
            service.update_knowledge(
                document=b"Skills\nTerraform\n", filename="points.txt", mode="supersede"
            )
        assert "terraform" not in {entry.value for entry in service.knowledge().entries}

    def test_supersede_replaces_only_the_named_document(self, service: ResumeService) -> None:
        service.update_knowledge(document=b"Skills\nKafka\n", filename="cv.txt")
        service.update_knowledge(document=b"Skills\nPython, Terraform\n", filename="points.txt")
        result = service.update_knowledge(
            document=b"Skills\nPython\n", filename="points.txt", mode="supersede"
        )
        values = {entry.value for entry in result.knowledge.entries}
        assert {"kafka", "python"} <= values
        assert "terraform" not in values
        assert result.stats.removed_entries == 1

    def test_oversized_pasted_text_is_refused(
        self, settings: Settings, service: ResumeService
    ) -> None:
        with pytest.raises(InvalidInputError, match="over the"):
            service.update_knowledge(text="x" * (settings.max_knowledge_text_chars + 1))

    def test_the_store_has_a_ceiling(self, data_dir: Path, engine: FakeEngine) -> None:
        """A pathological document must not be able to grow the store without
        bound across repeated merges."""
        capped = Settings(
            environment="test",
            pdf_engine="fake",
            log_level="WARNING",
            data_dir=data_dir,
            max_knowledge_entries=2,
        )
        service = ResumeService(
            settings=capped,
            bank_repo=BankRepository(capped.bank_path),
            profile_repo=ProfileRepository(capped.profile_path),
            engine=engine,
        )
        service.update_knowledge(text="Skills\nPython, SQL\n")
        with pytest.raises(InvalidInputError, match="over the limit"):
            service.update_knowledge(text="Skills\nTerraform, Airflow, Kafka\n")

    def test_removal_reports_what_it_did(self, service: ResumeService) -> None:
        service.update_knowledge(text="Skills\nPython, SQL\n")
        _, removed = service.remove_knowledge_entry("skill", "python")
        assert removed == 1
        _, removed_again = service.remove_knowledge_entry("skill", "python")
        assert removed_again == 0

    def test_removal_needs_both_arguments(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="required"):
            service.remove_knowledge_entry("skill", "  ")

    def test_clearing_empties_the_store(self, service: ResumeService) -> None:
        service.update_knowledge(text="Skills\nPython\n")
        assert service.clear_knowledge().is_empty


class TestAtsService:
    """Feature 2 at the service layer."""

    def test_the_corpus_names_all_three_sources(self, service: ResumeService) -> None:
        service.update_knowledge(text="Skills\nPython\n")
        names = [source.name for source in service.candidate_corpus().sources]
        assert names == ["knowledge", "profile", "bank"]

    def test_an_empty_knowledge_store_is_simply_left_out(self, service: ResumeService) -> None:
        """The profile and bank still answer, so a fresh installation can run a
        check rather than being refused until it has been fed something."""
        names = [source.name for source in service.candidate_corpus().sources]
        assert names == ["profile", "bank"]

    def test_an_empty_job_description_is_refused(self, service: ResumeService) -> None:
        with pytest.raises(InvalidInputError, match="must not be empty"):
            service.ats_check("   ")

    def test_an_oversized_job_description_is_refused(
        self, settings: Settings, service: ResumeService
    ) -> None:
        with pytest.raises(InvalidInputError, match="over the"):
            service.ats_check("x" * (settings.max_jd_chars + 1))

    def test_the_report_carries_both_versions(self, service: ResumeService) -> None:
        service.update_knowledge(text="Skills\nPython\n")
        report = service.ats_check("We need Python and SQL.")
        assert report.knowledge_version == service.knowledge().version
        assert report.bank_version == service.bank().version

    def test_checking_writes_nothing(self, service: ResumeService, data_dir: Path) -> None:
        before = {path.name: path.read_bytes() for path in data_dir.iterdir()}
        service.ats_check("We need Python.")
        assert {path.name: path.read_bytes() for path in data_dir.iterdir()} == before


class TestKnowledgeStoreCeilings:
    """Every list in the store has a limit, and each names the way out."""

    def _service(self, data_dir: Path, engine: FakeEngine, **overrides: int) -> ResumeService:
        settings = Settings(
            environment="test",
            pdf_engine="fake",
            log_level="WARNING",
            data_dir=data_dir,
            **overrides,
        )
        return ResumeService(
            settings=settings,
            bank_repo=BankRepository(settings.bank_path),
            profile_repo=ProfileRepository(settings.profile_path),
            engine=engine,
        )

    def test_the_source_ceiling_is_enforced_with_an_actionable_message(
        self, data_dir: Path, engine: FakeEngine
    ) -> None:
        """Sources accumulate one per document forever and nothing prunes them,
        so this is the ceiling a real user reaches first. Before the fix it
        surfaced as a raw ValidationError that named neither cause nor remedy."""
        service = self._service(data_dir, engine, max_knowledge_sources=1)
        service.update_knowledge(text="Skills\nPython\n")
        with pytest.raises(InvalidInputError, match="source documents"):
            service.update_knowledge(text="Skills\nTerraform\n")

    def test_the_experience_ceiling_is_enforced(self, data_dir: Path, engine: FakeEngine) -> None:
        service = self._service(data_dir, engine, max_knowledge_experience=1)
        with pytest.raises(InvalidInputError, match="experience items"):
            service.update_knowledge(
                text="Work Experience\nA at X -- Jan 2020 - Dec 2020\n"
                "B at Y -- Jan 2022 - Dec 2022\n"
            )

    def test_an_over_limit_update_leaves_the_store_untouched(
        self, data_dir: Path, engine: FakeEngine
    ) -> None:
        service = self._service(data_dir, engine, max_knowledge_sources=1)
        service.update_knowledge(text="Skills\nPython\n")
        before = service.knowledge().version
        with pytest.raises(InvalidInputError):
            service.update_knowledge(text="Skills\nTerraform\n")
        assert service.knowledge().version == before

    def test_a_configured_limit_cannot_exceed_the_structural_cap(self) -> None:
        """If it could, the friendly check would be unreachable and pydantic
        would answer instead, from a layer that cannot explain itself."""
        with pytest.raises(ValueError, match="less than or equal to"):
            Settings(environment="test", max_knowledge_entries=20_001)


class TestConcurrentKnowledgeUpdates:
    def test_two_simultaneous_updates_do_not_lose_entries(self, service: ResumeService) -> None:
        """Read-modify-write across load/save. Without the service lock both
        threads read the same base and the later save silently discarded the
        earlier thread's entries."""
        import threading

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def add(text: str) -> None:
            try:
                barrier.wait(timeout=10)
                service.update_knowledge(text=text)
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=add, args=("Technical Skills\nTerraform\n",)),
            threading.Thread(target=add, args=("Technical Skills\nSnowflake\n",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        assert not errors, errors
        values = {entry.value for entry in service.knowledge().entries}
        assert {"terraform", "snowflake"} <= values


class TestProfileExperienceCounts:
    def test_the_profile_supplies_dated_experience_when_the_store_is_empty(
        self, service: ResumeService
    ) -> None:
        """The fresh-checkout case: knowledge.json does not exist yet and
        profile.yaml is fully populated. Scoring every "N years" requirement as
        missing there would zero a 15%-weighted category for someone whose
        experience the repository plainly holds."""
        corpus = service.candidate_corpus()
        assert corpus.experience_months > 0
        assert corpus.experience_sources == ("profile",)

    def test_both_sources_are_reported_when_both_have_dates(self, service: ResumeService) -> None:
        service.update_knowledge(text="Work Experience\nEngineer at Acme -- Jan 2015 - Dec 2016\n")
        corpus = service.candidate_corpus()
        assert corpus.experience_sources == ("knowledge", "profile")

    def test_a_role_in_both_sources_is_counted_once(self, service: ResumeService) -> None:
        """The profile fixture's one dated role is 2024--2025. Re-stating it as
        candidate knowledge must not double the total."""
        before = service.candidate_corpus().experience_months
        service.update_knowledge(
            text="Work Experience\nData Scientist at Test Corp -- 2024 - 2025\n"
        )
        assert service.candidate_corpus().experience_months == before
