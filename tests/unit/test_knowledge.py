"""Candidate knowledge: extraction, merge rules and persistence.

The load-bearing assertions here are the ones about *not* losing and *not*
inventing: a merge must never drop an existing entry, a re-upload must add
nothing, and every entry must be able to point at the line it came from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resume_tailor.core.errors import KnowledgeError
from resume_tailor.data.knowledge_repo import KnowledgeRepository, parse_knowledge
from resume_tailor.domain.extraction import extract_text_from_paste
from resume_tailor.domain.knowledge import (
    KnowledgeBase,
    KnowledgeEntry,
    extract_knowledge,
    lint_knowledge,
    merge_month_intervals,
    parse_experience_line,
)

pytestmark = pytest.mark.unit

RESUME = """Jane Doe
Data Scientist

Professional Summary
Data scientist building forecasting models for fintech clients.

Technical Skills
Languages: Python, SQL, R
Libraries: Pandas, PyTorch, Obscurelib
Cloud: AWS, Docker

Work Experience
Data Scientist at Northwind -- Jan 2022 - Dec 2023
Built anomaly detection pipelines and dashboards.
Analyst at Contoso | Mar 2020 - Dec 2021
Ran A/B testing and stakeholder communication.

Education
B.Tech in Computer Science, 2019

Certifications
AWS Certified Machine Learning Specialty
"""


def build(text: str = RESUME, label: str = "cv.txt") -> KnowledgeBase:
    return extract_knowledge(extract_text_from_paste(text, label=label))


@pytest.fixture
def knowledge() -> KnowledgeBase:
    return build()


# --- extraction -------------------------------------------------------------


class TestExtraction:
    def test_vocabulary_terms_are_found(self, knowledge: KnowledgeBase) -> None:
        values = {entry.value for entry in knowledge.entries}
        assert {"python", "sql", "pytorch", "aws", "docker"} <= values

    def test_terms_are_filed_by_category(self, knowledge: KnowledgeBase) -> None:
        by_value = {entry.value: entry.category for entry in knowledge.entries}
        assert by_value["python"] == "skill"
        assert by_value["pytorch"] == "tool"
        assert by_value["fintech"] == "domain"

    def test_a_declared_skill_outside_the_vocabulary_is_still_stored(
        self, knowledge: KnowledgeBase
    ) -> None:
        """Otherwise the curated vocabulary would silently cap what the system
        can learn about a person."""
        assert "obscurelib" in {entry.value for entry in knowledge.entries}

    def test_every_entry_cites_its_source_line(self, knowledge: KnowledgeBase) -> None:
        """The no-fabrication rule, asserted. An entry that cannot point at the
        text it came from is indistinguishable from an invented one."""
        for entry in knowledge.entries:
            assert entry.evidence, entry.value
            assert entry.source_id == knowledge.sources[0].source_id

    def test_display_keeps_the_spelling_the_document_used(self) -> None:
        knowledge = build("Skills\nI use PyTorch daily.\n")
        entry = next(e for e in knowledge.entries if e.value == "pytorch")
        assert entry.display == "PyTorch"

    def test_education_and_certifications_are_captured(self, knowledge: KnowledgeBase) -> None:
        categories = {entry.category for entry in knowledge.entries}
        assert "education" in categories
        assert "certification" in categories

    def test_extraction_is_deterministic(self) -> None:
        first = [(e.category, e.value) for e in build().entries]
        second = [(e.category, e.value) for e in build().entries]
        assert first == second

    def test_max_entries_is_respected(self) -> None:
        knowledge = extract_knowledge(extract_text_from_paste(RESUME), max_entries=3)
        assert len(knowledge.entries) == 3


# --- experience dates -------------------------------------------------------


class TestExperienceParsing:
    def test_month_year_range(self) -> None:
        fact = parse_experience_line("Data Scientist at Acme -- Jan 2022 - Dec 2023", "src1")
        assert fact is not None
        assert fact.months == 24
        assert fact.title == "Data Scientist"
        assert fact.organisation == "Acme"

    def test_numeric_dates(self) -> None:
        fact = parse_experience_line("Analyst 01/2020 - 06/2020", "src1")
        assert fact is not None
        assert fact.months == 6

    def test_present_is_resolved_once_and_stored(self) -> None:
        fact = parse_experience_line("Engineer at Acme, Jan 2020 - Present", "src1")
        assert fact is not None
        # Stored, not recomputed: a report generated next month must agree with
        # one generated today.
        assert fact.months > 0
        assert fact.end_month is not None

    def test_a_lone_year_is_not_a_job(self) -> None:
        """A graduation year is not a one-month role. Recording it as one would
        be an invention."""
        assert parse_experience_line("B.Tech Computer Science 2019", "src1") is None

    def test_a_line_with_no_dates_is_skipped(self) -> None:
        assert parse_experience_line("Built a dashboard", "src1") is None

    def test_experience_is_extracted_from_the_experience_section(
        self, knowledge: KnowledgeBase
    ) -> None:
        titles = {fact.title for fact in knowledge.experience}
        assert "Data Scientist" in titles
        assert "Analyst" in titles

    def test_overlapping_roles_are_counted_once(self) -> None:
        overlapping = build(
            "Work Experience\nRole A at X -- Jan 2020 - Dec 2021\n"
            "Role B at Y -- Jun 2020 - Dec 2021\n"
        )
        # Jan 2020 to Dec 2021 inclusive is 24 months; a naive sum would be 43.
        assert overlapping.total_experience_months() == 24

    def test_separate_ranges_are_added(self) -> None:
        separate = build(
            "Work Experience\nRole A at X -- Jan 2018 - Dec 2018\n"
            "Role B at Y -- Jan 2022 - Dec 2022\n"
        )
        assert separate.total_experience_months() == 24


# --- merge rules ------------------------------------------------------------


class TestMerge:
    def test_re_adding_the_same_document_adds_nothing(self) -> None:
        base = build()
        merged, stats = base.merged_with(build())
        assert stats.added_entries == 0
        assert stats.duplicate_entries == len(base.entries)
        assert stats.source_already_known is True
        assert len(merged.entries) == len(base.entries)

    def test_merge_preserves_existing_entries(self) -> None:
        base = build("Skills\nPython, SQL\n")
        merged, _ = base.merged_with(build("Skills\nTerraform\n"))
        values = {entry.value for entry in merged.entries}
        assert {"python", "sql", "terraform"} <= values

    def test_merge_reports_what_it_added_by_category(self) -> None:
        base = build("Skills\nPython\n")
        _, stats = base.merged_with(build("Skills\nTerraform\n"))
        assert stats.added_by_category.get("tool", 0) >= 1
        assert stats.changed is True

    def test_a_no_op_merge_reports_no_change(self) -> None:
        base = build("Skills\nPython\n")
        _, stats = base.merged_with(build("Skills\nPython\n", label="again.txt"))
        assert stats.changed is False

    def test_removal_is_explicit_and_reports_the_count(self) -> None:
        base = build("Skills\nPython, SQL\n")
        updated, removed = base.without("skill", "Python")
        assert removed == 1
        assert "python" not in {entry.value for entry in updated.entries}

    def test_removing_something_absent_reports_zero(self) -> None:
        base = build("Skills\nPython\n")
        _, removed = base.without("skill", "cobol")
        assert removed == 0


class TestSupersede:
    """A changed source document replaces its earlier version.

    The source document always wins. Merge only adds, so a fact deleted from
    the document used to live on in the store and keep scoring.
    """

    def test_a_fact_removed_from_the_document_leaves_the_store(self) -> None:
        base = build("Skills\nPython, Terraform\n", label="points.docx")
        updated, stats = base.superseded_by(
            build("Skills\nPython\n", label="points.docx"), "points.docx"
        )
        values = {entry.value for entry in updated.entries}
        assert "terraform" not in values
        assert "python" in values
        assert stats.removed_entries == 1
        assert stats.superseded_sources == 1
        assert stats.changed is True

    def test_other_documents_are_untouched(self) -> None:
        base, _ = build("Skills\nPython\n", label="points.docx").merged_with(
            build("Skills\nKafka\n", label="cv.pdf")
        )
        updated, _ = base.superseded_by(build("Skills\nSQL\n", label="points.docx"), "points.docx")
        values = {entry.value for entry in updated.entries}
        assert {"kafka", "sql"} <= values
        assert "python" not in values
        assert sorted(source.label for source in updated.sources) == ["cv.pdf", "points.docx"]

    def test_an_unchanged_fact_is_not_reported_as_added(self) -> None:
        base = build("Skills\nPython, SQL\n", label="points.docx")
        _, stats = base.superseded_by(
            build("Skills\nPython, SQL, Kafka\n", label="points.docx"), "points.docx"
        )
        assert stats.added_entries == 1
        assert stats.added_by_category == {"tool": 1}
        assert stats.duplicate_entries == 2
        assert stats.removed_entries == 0

    def test_changed_dates_replace_the_old_role(self) -> None:
        base = build("Work Experience\nAnalyst at X -- Jan 2020 - Dec 2020\n", label="p.txt")
        updated, stats = base.superseded_by(
            build("Work Experience\nAnalyst at X -- Jan 2020 - Dec 2021\n", label="p.txt"), "p.txt"
        )
        assert (stats.removed_experience, stats.added_experience) == (1, 1)
        assert updated.total_experience_months() == 24

    def test_names_match_ignoring_case_and_spacing(self) -> None:
        base = build("Skills\nPython\n", label="Project  Points.docx")
        assert base.sources_labelled("project points.DOCX")
        assert not base.sources_labelled("project points v2.docx")


# --- views ------------------------------------------------------------------


class TestViews:
    def test_corpus_text_includes_evidence_lines(self) -> None:
        """A term the vocabulary has never heard of must still be findable, or
        the ATS checker reports a gap the resume plainly contradicts."""
        knowledge = build("Skills\nLanguages: Python, Zigzaglang\n")
        assert "zigzaglang" in knowledge.corpus_text()

    def test_by_category_is_ordered_and_omits_empties(self, knowledge: KnowledgeBase) -> None:
        grouped = knowledge.by_category()
        assert list(grouped) == [
            c
            for c in ("skill", "tool", "domain", "responsibility", "certification", "education")
            if c in grouped
        ]
        assert all(entries for entries in grouped.values())

    def test_version_changes_with_content(self) -> None:
        assert build("Skills\nPython\n").version != build("Skills\nPython, SQL\n").version

    def test_empty_base(self) -> None:
        empty = KnowledgeBase()
        assert empty.is_empty
        assert empty.total_experience_months() == 0
        assert empty.corpus_text() == ""


# --- persistence ------------------------------------------------------------


class TestRepository:
    def test_a_missing_file_is_an_empty_store_not_an_error(self, tmp_path: Path) -> None:
        """A fresh checkout has no knowledge.json. That is a starting point,
        not a failure -- unlike a missing project bank."""
        assert KnowledgeRepository(tmp_path / "absent.json").load().is_empty

    def test_save_then_load_round_trips(self, tmp_path: Path) -> None:
        repo = KnowledgeRepository(tmp_path / "knowledge.json")
        saved = repo.save(build())
        assert KnowledgeRepository(tmp_path / "knowledge.json").load().version == saved.version

    def test_save_creates_the_directory(self, tmp_path: Path) -> None:
        repo = KnowledgeRepository(tmp_path / "nested" / "knowledge.json")
        repo.save(build())
        assert (tmp_path / "nested" / "knowledge.json").is_file()

    def test_no_temporary_files_are_left_behind(self, tmp_path: Path) -> None:
        repo = KnowledgeRepository(tmp_path / "knowledge.json")
        repo.save(build())
        assert [p.name for p in tmp_path.iterdir()] == ["knowledge.json"]

    def test_an_edit_on_disk_is_picked_up(self, tmp_path: Path) -> None:
        path = tmp_path / "knowledge.json"
        repo = KnowledgeRepository(path)
        repo.save(build())
        path.write_text(json.dumps({"entries": [], "experience": [], "sources": []}))
        assert repo.load().is_empty

    def test_invalid_json_is_a_typed_error(self, tmp_path: Path) -> None:
        path = tmp_path / "knowledge.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(KnowledgeError, match="not valid knowledge JSON"):
            KnowledgeRepository(path).load()

    def test_a_json_array_is_refused(self) -> None:
        with pytest.raises(KnowledgeError, match="must contain a JSON object"):
            parse_knowledge("[]")

    def test_schema_violations_are_reported_with_the_field(self) -> None:
        payload = json.dumps({"entries": [{"category": "not-a-category"}]})
        with pytest.raises(KnowledgeError, match="failed validation"):
            parse_knowledge(payload)

    def test_unknown_fields_are_refused(self) -> None:
        payload = json.dumps({"entries": [], "experience": [], "sources": [], "extra": 1})
        with pytest.raises(KnowledgeError):
            parse_knowledge(payload)


class TestEntryValidation:
    def test_value_is_normalised(self) -> None:
        entry = KnowledgeEntry(
            category="skill", value="  Machine   Learning ", display="ML", source_id="abcd"
        )
        assert entry.value == "machine learning"
        assert entry.key == ("skill", "machine learning")

    def test_empty_value_is_refused(self) -> None:
        """``StrictModel`` strips whitespace before validation, so this is
        rejected by the length bound rather than by the normaliser -- either
        way an entry with no value cannot exist."""
        with pytest.raises(ValueError, match="at least 1 character"):
            KnowledgeEntry(category="skill", value="   ", display="x", source_id="abcd")

    def test_an_unknown_category_is_refused(self) -> None:
        with pytest.raises(ValueError, match="category"):
            KnowledgeEntry(
                category="invented",  # type: ignore[arg-type]
                value="python",
                display="Python",
                source_id="abcd",
            )


class TestImplausibleDates:
    """One odd line must not cost the user the whole upload."""

    def test_a_span_no_career_can_contain_is_not_a_date_range(self) -> None:
        line = "Analyst, Acme (founded 1919), Jan 2024 - Dec 2025"
        assert parse_experience_line(line, "src1") is None

    def test_such_a_line_does_not_abort_the_document(self) -> None:
        knowledge = build(
            "Work Experience\n"
            "Analyst, Acme (founded 1919), Jan 2024 - Dec 2025\n"
            "Engineer at Contoso -- Jan 2022 - Dec 2022\n"
            "Technical Skills\nPython\n"
        )
        assert "python" in {entry.value for entry in knowledge.entries}
        assert [fact.title for fact in knowledge.experience] == ["Engineer"]

    def test_a_plausible_long_range_is_still_kept(self) -> None:
        fact = parse_experience_line("Engineer at Acme -- Jan 2000 - Dec 2020", "src1")
        assert fact is not None
        assert fact.months == 252


class TestIntervalMerging:
    def test_overlaps_count_once(self) -> None:
        assert merge_month_intervals([(0, 11), (6, 17)]) == 18

    def test_adjacent_ranges_join(self) -> None:
        assert merge_month_intervals([(0, 11), (12, 23)]) == 24

    def test_disjoint_ranges_add(self) -> None:
        assert merge_month_intervals([(0, 11), (24, 35)]) == 24

    def test_order_does_not_matter(self) -> None:
        assert merge_month_intervals([(24, 35), (0, 11)]) == merge_month_intervals(
            [(0, 11), (24, 35)]
        )

    def test_empty(self) -> None:
        assert merge_month_intervals([]) == 0


class TestDeclaredSkillsAreNotProse:
    """A "Skills" heading captures every line until the next recognised
    heading, so a long document with no heading after it puts the whole file in
    that section. Comma-splitting then turns paragraphs into thousands of
    fragments -- observed for real: a 581 kB project-notes file produced 2,057
    "skills", 2,001 of them sentence fragments, URLs and stray numbers, which
    poisoned the ATS candidate corpus because every entry carries its evidence.
    """

    PROSE = "\n".join(
        [
            "Repository: https://github.com/someone/breast-cancer-survival",
            "1. What is this project?",
            "Applying Kaplan-Meier estimation, log-rank testing, radiotherapy,",
            "receptor status) variables were analysed across ~2,000 patients).",
            "Delivered reproducible analysis with 7 standalone .sql files,",
            "covering 129 admissions, length-of-stay and mortality breakdowns.",
        ]
        * 60
    )

    @pytest.fixture
    def polluted(self) -> KnowledgeBase:
        return build(
            "Technical Skills\n"
            "Languages: Python, SQL, R\n"
            "Libraries: pandas, scikit-learn, lifelines (survival analysis)\n" + self.PROSE,
            label="Project Points.docx",
        )

    def test_a_prose_document_does_not_explode_the_store(self, polluted: KnowledgeBase) -> None:
        assert len(polluted.entries) < 60

    @pytest.mark.parametrize(
        "fragment",
        [
            "https://github.com/someone/breast-cancer-survival",  # a link
            "1. what is this project?",  # a heading
            "~2",  # a number
            "000 patients)",  # torn out of a phrase
            "applying kaplan-meier estimation",  # a participle clause
            "covering 129 admissions",  # a measurement
            "repository",  # a label, not a skill
        ],
    )
    def test_prose_fragments_are_not_stored_as_skills(
        self, polluted: KnowledgeBase, fragment: str
    ) -> None:
        assert fragment not in {entry.value for entry in polluted.entries}

    @pytest.mark.parametrize("term", ["python", "sql", "pandas", "scikit-learn", "lifelines"])
    def test_the_real_skills_list_still_survives(self, polluted: KnowledgeBase, term: str) -> None:
        assert term in {entry.value for entry in polluted.entries}

    def test_a_parenthetical_gloss_is_dropped_not_the_term(self, polluted: KnowledgeBase) -> None:
        """ "lifelines (survival analysis)" records "lifelines"."""
        values = {entry.value for entry in polluted.entries}
        assert "lifelines" in values
        assert "lifelines (survival analysis)" not in values

    def test_declared_skills_are_capped_per_document(self) -> None:
        listed = ", ".join(f"skillname{index}" for index in range(400))
        knowledge = build(f"Technical Skills\n{listed}\n")
        declared = [e for e in knowledge.entries if e.value.startswith("skillname")]
        assert len(declared) <= 150

    def test_a_genuine_skills_block_is_untouched(self) -> None:
        """The filters must not cost a normal resume anything."""
        knowledge = build(
            "Technical Skills\n"
            "Languages: Python, SQL, R, Scala\n"
            "Cloud: AWS, Docker, Kubernetes, Terraform\n"
            "BI: Tableau, Power BI, Looker\n"
        )
        values = {entry.value for entry in knowledge.entries}
        expected = {
            "python",
            "sql",
            "r",
            "scala",
            "aws",
            "docker",
            "kubernetes",
            "terraform",
            "tableau",
            "power bi",
            "looker",
        }
        assert expected <= values


class TestEducationIsNotOrdinaryProse:
    """The education arm of the same pollution.

    "master", "bachelor", "diploma" and "degree" are ordinary English words. A
    document-wide scan for them filed two whole sentences of engineering notes
    under Education -- both from the same 581 kB file above -- because it
    mentioned a git branch that was not merged into master.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "HEAD: f08e7c2 on branch expand-question-set (PR not opened, not on master)",
            "The scrum master ran the retro on Tuesday.",
            "We report the degree of freedom for each chi-square test.",
            "Merged the feature branch into master last week.",
        ],
    )
    def test_ordinary_prose_is_not_education(self, line: str) -> None:
        knowledge = build(f"Notes\n{line}\n")
        assert [e.value for e in knowledge.entries if e.category == "education"] == []

    @pytest.mark.parametrize(
        "line",
        [
            "M.Sc in Data Science, Anna University, 2019",
            "Master of Business Administration, 2018",
            "Bachelor's degree in Computer Science",
            "PhD in Statistics",
            "Diploma in Machine Learning",
            "B.Tech, Electronics and Communication",
        ],
    )
    def test_a_real_qualification_is_still_captured(self, line: str) -> None:
        """The precision fix must not cost a real resume its degree."""
        knowledge = build(f"Notes\n{line}\n")
        assert [e.value for e in knowledge.entries if e.category == "education"] == [line.lower()]

    def test_an_education_section_is_captured_verbatim_regardless(self) -> None:
        """The section scan is the reliable path; the regex above it is only
        the fallback for a degree named somewhere else, which is why it can
        afford to be strict."""
        knowledge = build("Education\nSt. Xavier's College, 2015-2018\n")
        values = {e.value for e in knowledge.entries if e.category == "education"}
        assert "st. xavier's college, 2015-2018" in values


class TestOverlongValuesAreDropped:
    def test_a_paragraph_is_not_stored_as_an_entry(self) -> None:
        """`value` is the entry's identity -- the de-duplication key, what the
        ATS scorer matches on, and what DELETE takes. Truncating a sentence to
        120 characters lands mid-word and yields a key that matches nothing."""
        sentence = "Bachelor of Science in " + " ".join(["interdisciplinary"] * 12)
        assert len(sentence) > 120
        knowledge = build(f"Education\n{sentence}\n")
        for entry in knowledge.entries:
            assert len(entry.value) <= 120
            assert not sentence.lower().startswith(entry.value) or entry.value == sentence.lower()


class TestLintKnowledge:
    """The answer to "how do I know the upload worked?".

    Counts alone cannot tell you: a store built from a document whose prose was
    read as a skills list looks thorough and is wrong. These checks name that
    shape, because it is the failure that actually happened in the field.
    """

    def test_an_empty_store_says_so(self) -> None:
        assert lint_knowledge(KnowledgeBase())[0].startswith("nothing is stored")

    def test_a_healthy_store_has_no_warnings_about_prose(self) -> None:
        knowledge = build(
            "Technical Skills\n"
            "Languages: Python, SQL, R\n"
            "Cloud: AWS, Docker, Kubernetes, Airflow\n"
            "Work Experience\n"
            "Data Scientist at Acme -- Jan 2022 - Dec 2023\n"
        )
        assert not [w for w in lint_knowledge(knowledge) if "not recognised terms" in w]

    def test_a_prose_store_is_flagged_with_the_remedy(self) -> None:
        polluted = KnowledgeBase(
            entries=[
                KnowledgeEntry(
                    category="skill",
                    value=f"fragment number {index} of a sentence",
                    display="x",
                    source_id="abcd1234",
                )
                for index in range(60)
            ]
        )
        warnings = lint_knowledge(polluted)
        assert any("not recognised terms" in w for w in warnings)
        assert any("replace" in w for w in warnings)

    def test_link_shaped_entries_are_flagged(self) -> None:
        knowledge = KnowledgeBase(
            entries=[
                KnowledgeEntry(
                    category="skill",
                    value="https://github.com/someone/repo",
                    display="link",
                    source_id="abcd1234",
                )
            ]
        )
        assert any("links or numbers" in w for w in lint_knowledge(knowledge))

    def test_a_store_with_no_dated_roles_is_flagged(self) -> None:
        knowledge = build("Technical Skills\nPython, SQL\n")
        assert any("no dated roles" in w for w in lint_knowledge(knowledge))

    def test_warnings_are_never_fatal(self) -> None:
        """They describe content. A store that warns is still fully usable."""
        knowledge = build("Technical Skills\nPython\n")
        assert lint_knowledge(knowledge)
        assert not knowledge.is_empty
        assert knowledge.corpus_text()
