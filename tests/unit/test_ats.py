"""ATS scoring.

The three properties the PRD asks for are the three things asserted hardest
here: the report is deterministic, it cannot be inflated by repetition, and
every requirement carries the reason for its status.
"""

from __future__ import annotations

import pytest

from resume_tailor.domain.ats import (
    GATE_CAP,
    GENERIC_TOKENS,
    MAX_KEYWORD_REQUIREMENTS,
    PRIORITY_WEIGHTS,
    WEIGHTS,
    AtsReport,
    CandidateCorpus,
    Gate,
    NamedCorpus,
    Requirement,
    band_for,
    build_report,
    extract_requirements,
    extract_year_requirements,
    split_by_priority,
)
from resume_tailor.domain.matching import normalize

pytestmark = pytest.mark.unit

JD = """Senior Data Scientist -- Fintech

We are looking for someone with 4+ years of experience.
You will build models, deploy models and own stakeholder communication.
Required: Python, SQL, machine learning, TensorFlow, Snowflake and Airflow.
A bachelor degree is required. AWS certification is a plus.
Familiarity with Kubernetes and Zephyrbase is welcome.
"""


def corpus(
    text: str,
    *,
    months: int = 0,
    name: str = "knowledge",
    education: str = "",
    location: str = "",
) -> CandidateCorpus:
    return CandidateCorpus(
        sources=(NamedCorpus(name=name, text=normalize(text)),),
        experience_months=months,
        education_text=normalize(education),
        location=location,
    )


@pytest.fixture
def candidate() -> CandidateCorpus:
    return corpus(
        "python sql machine learning pytorch airflow bachelor of technology "
        "stakeholder communication fintech build models",
        months=60,
    )


# --- requirement extraction -------------------------------------------------


class TestRequirementExtraction:
    def test_requirements_are_grouped_by_category(self) -> None:
        specs = extract_requirements(JD)
        assert "python" in {spec.term for spec in specs["skills"]}
        assert "tensorflow" in {spec.term for spec in specs["tools"]}
        assert "fintech" in {spec.term for spec in specs["responsibilities"]}
        assert "bachelor" in {spec.term for spec in specs["education"]}

    def test_a_term_no_vocabulary_knows_still_becomes_a_requirement(self) -> None:
        """The catch-all. Without it the curated vocabulary would silently
        decide which parts of a posting count."""
        keywords = {spec.term for spec in extract_requirements(JD)["keywords"]}
        assert "zephyrbase" in keywords

    def test_a_term_is_not_counted_in_two_categories(self) -> None:
        specs = extract_requirements(JD)
        keywords = {spec.term for spec in specs["keywords"]}
        elsewhere = {
            spec.term for name, group in specs.items() if name != "keywords" for spec in group
        }
        assert not (keywords & elsewhere)

    def test_a_possessive_is_not_a_second_requirement(self) -> None:
        """ "Master's" is the education term "master"; it must not come back as
        a keyword and be scored a second time."""
        keywords = {spec.term for spec in extract_requirements("A Master's degree.")["keywords"]}
        assert "master's" not in keywords

    def test_the_location_line_is_not_scored_as_keywords(self) -> None:
        """The location gate owns that line. Scoring it as keywords counted the
        location twice and faulted the candidate for not writing "hybrid"."""
        specs = extract_requirements("Location: Bangalore, India (Hybrid)\nWe need Python.")
        keywords = {spec.term for spec in specs["keywords"]}
        assert not keywords & {"bangalore", "india", "hybrid"}

    def test_requirements_are_sorted_not_set_ordered(self) -> None:
        terms = [spec.term for spec in extract_requirements(JD)["skills"]]
        assert terms == sorted(terms)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("5+ years of experience", [5]),
            ("3-5 years of experience", [3]),
            ("3 to 5 years", [3]),
            ("at least 2 yrs", [2]),
            ("2 years in SQL and 5 years in Python", [2, 5]),
            ("founded in 2019", []),
            ("99 years", []),
        ],
    )
    def test_year_requirements(self, text: str, expected: list[int]) -> None:
        assert extract_year_requirements(text) == expected

    def test_a_range_does_not_also_register_its_upper_bound(self) -> None:
        """ "3-5 years" is one requirement for three, not also one for five."""
        assert extract_year_requirements("3-5 years of experience") == [3]


# --- classification ---------------------------------------------------------


class TestClassification:
    def test_a_term_the_candidate_has_is_exact(self, candidate: CandidateCorpus) -> None:
        report = build_report("We need Python.", candidate)
        requirement = _find(report, "python")
        assert requirement.status == "exact"
        assert requirement.matched_sources == ["knowledge"]

    def test_an_adjacent_term_is_related_not_exact(self, candidate: CandidateCorpus) -> None:
        """The candidate has PyTorch, the posting wants TensorFlow. That is
        neither a hit nor a blank, and reporting it as either would mislead."""
        report = build_report("We need TensorFlow.", candidate)
        requirement = _find(report, "tensorflow")
        assert requirement.status == "related"
        assert requirement.matched_term == "pytorch"
        assert "adjacent" in requirement.detail

    def test_an_absent_term_is_missing_with_no_evidence(self, candidate: CandidateCorpus) -> None:
        report = build_report("We need Snowflake.", candidate)
        requirement = _find(report, "snowflake")
        assert requirement.status == "missing"
        assert requirement.matched_term == ""
        assert requirement.matched_sources == []

    def test_a_phrase_with_one_distinctive_token_is_related(self) -> None:
        report = build_report("We need data governance.", corpus("governance frameworks"))
        assert _find(report, "data governance").status == "related"

    @pytest.mark.parametrize(
        ("jd", "term", "candidate_text"),
        [
            ("We need data governance.", "data governance", "data engineering pipelines"),
            ("Own model risk management.", "model risk management", "model monitoring"),
            ("Strong stakeholder management.", "stakeholder management", "project management"),
        ],
    )
    def test_a_generic_word_alone_earns_nothing(
        self, jd: str, term: str, candidate_text: str
    ) -> None:
        """ "data" is part of eighteen vocabulary phrases. Crediting it alone
        gave anyone with data experience half of "data governance" -- the score
        claiming evidence the candidate never showed."""
        assert _find(build_report(jd, corpus(candidate_text)), term).status == "missing"

    def test_generic_tokens_are_derived_from_the_vocabulary(self) -> None:
        assert {"data", "model", "management", "analysis"} <= GENERIC_TOKENS
        assert "governance" not in GENERIC_TOKENS

    def test_provenance_names_every_source_that_matched(self) -> None:
        both = CandidateCorpus(
            sources=(
                NamedCorpus(name="knowledge", text=normalize("python")),
                NamedCorpus(name="bank", text=normalize("python sql")),
            )
        )
        assert _find(build_report("Python please.", both), "python").matched_sources == [
            "knowledge",
            "bank",
        ]


class TestYearsClassification:
    def test_enough_experience_is_exact(self) -> None:
        report = build_report("5+ years of experience required.", corpus("python", months=72))
        assert _find(report, "5 years experience").status == "exact"

    def test_just_short_is_related_not_missing(self) -> None:
        """Four years and ten months against a five-year ask is not a blank,
        and scoring it as one would make the report less useful than the person
        reading it."""
        report = build_report("5+ years of experience required.", corpus("python", months=58))
        assert _find(report, "5 years experience").status == "related"

    def test_well_short_is_missing(self) -> None:
        report = build_report("5+ years of experience required.", corpus("python", months=12))
        assert _find(report, "5 years experience").status == "missing"

    def test_no_dated_experience_says_why(self) -> None:
        report = build_report("5+ years of experience required.", corpus("python", months=0))
        requirement = _find(report, "5 years experience")
        assert requirement.status == "missing"
        assert "no dated experience" in requirement.detail


# --- scoring ----------------------------------------------------------------


class TestScoring:
    def test_report_is_deterministic(self, candidate: CandidateCorpus) -> None:
        first = build_report(JD, candidate)
        second = build_report(JD, candidate)
        assert first.model_dump() == second.model_dump()

    def test_repetition_cannot_raise_the_score(self, candidate: CandidateCorpus) -> None:
        """The central anti-inflation property. A posting that says "Python"
        twenty times is not a better match than one that says it once."""
        once = build_report("We need Python and Snowflake.", candidate)
        many = build_report("We need Python and Snowflake. " + "Python. " * 20, candidate)
        assert many.score == once.score

    def test_occurrences_are_reported_even_though_they_are_not_scored(
        self, candidate: CandidateCorpus
    ) -> None:
        report = build_report("Python. Python. Python.", candidate)
        assert _find(report, "python").occurrences == 3

    def test_a_full_match_scores_100(self) -> None:
        report = build_report("We need Python.", corpus("python"))
        assert report.score == 100
        assert report.band == "Strong match"

    def test_no_match_scores_zero(self) -> None:
        report = build_report("We need Snowflake.", corpus("cobol"))
        assert report.score == 0
        assert report.missing_requirements == ["snowflake"]

    def test_a_related_match_is_worth_half(self) -> None:
        report = build_report("We need TensorFlow.", corpus("pytorch"))
        assert report.score == 50

    def test_absent_categories_are_omitted_and_their_weight_redistributed(self) -> None:
        """A posting that never mentions education is not evidence that the
        candidate has none, so it must not score as a zero."""
        report = build_report("We need Python.", corpus("python"))
        assert [category.name for category in report.breakdown] == ["skills"]
        assert report.score == 100

    def test_weights_are_published_and_sum_to_one(self, candidate: CandidateCorpus) -> None:
        report = build_report(JD, candidate)
        assert report.weights == WEIGHTS
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_score_can_be_recomputed_from_the_breakdown(self, candidate: CandidateCorpus) -> None:
        """The transparency claim, checked rather than asserted in prose."""
        report = build_report(JD, candidate)
        weighted = sum(c.weight * c.score for c in report.breakdown)
        total = sum(c.weight for c in report.breakdown)
        assert report.uncapped_score == round(100 * weighted / total)
        assert report.score == (GATE_CAP if report.capped else report.uncapped_score)

    def test_every_requirement_appears_in_exactly_one_summary_list(
        self, candidate: CandidateCorpus
    ) -> None:
        report = build_report(JD, candidate)
        listed = (
            len(report.matched_requirements)
            + len(report.weak_requirements)
            + len(report.missing_requirements)
        )
        assert listed == report.requirement_count

    def test_note_is_always_present(self, candidate: CandidateCorpus) -> None:
        assert "counted exactly once" in build_report(JD, candidate).note

    def test_versions_are_carried_through(self, candidate: CandidateCorpus) -> None:
        report = build_report(JD, candidate, knowledge_version="abc123", bank_version="def456")
        assert (report.knowledge_version, report.bank_version) == ("abc123", "def456")

    def test_a_job_description_with_no_recognisable_requirements_scores_zero(self) -> None:
        report = build_report("hello there", corpus("python"))
        assert report.score == 0
        assert report.requirement_count == 0


class TestPriority:
    """Required versus preferred. A gap under "Nice to have" must cost less
    than one under "Requirements", and anything ambiguous stays required --
    the direction that cannot inflate a score."""

    def test_a_nice_to_have_heading_marks_what_follows(self) -> None:
        report = build_report("Requirements:\nPython\nNice to have:\nScala\n", corpus("python"))
        assert _find(report, "python").priority == "required"
        assert _find(report, "scala").priority == "preferred"

    def test_a_later_heading_closes_the_preferred_section(self) -> None:
        report = build_report("Nice to have:\nScala\nResponsibilities:\nPython\n", corpus("python"))
        assert _find(report, "python").priority == "required"

    def test_an_inline_cue_marks_one_sentence(self) -> None:
        report = build_report("Python is required. Scala is a plus.", corpus("python"))
        assert _find(report, "python").priority == "required"
        assert _find(report, "scala").priority == "preferred"

    def test_an_inline_cue_is_not_a_heading(self) -> None:
        """ "Scala (preferred)" is one preferred item, not the start of a
        preferred section that swallows the rest of the posting."""
        report = build_report("Scala (preferred)\nPython\n", corpus("python"))
        assert _find(report, "scala").priority == "preferred"
        assert _find(report, "python").priority == "required"

    def test_a_term_asked_for_both_ways_is_required(self) -> None:
        report = build_report("Scala is a plus.\nWe require Scala.", corpus("python"))
        assert _find(report, "scala").priority == "required"

    def test_required_beats_preferred_in_one_sentence(self) -> None:
        report = build_report("Python is required, Scala preferred.", corpus("python"))
        assert _find(report, "scala").priority == "required"

    def test_a_preferred_gap_costs_less_than_a_required_one(self) -> None:
        required = build_report("Required: Python and Scala.", corpus("python"))
        preferred = build_report("Required: Python. Scala is a plus.", corpus("python"))
        assert required.score == 50
        # Full credit at weight 1.0, none at weight 0.25: 1 / 1.25.
        assert preferred.score == 80

    def test_a_preferred_only_category_carries_less_weight(self) -> None:
        """Without this, a category holding nothing but wish-list items would
        still count at full weight, and sink the score through the back door."""
        report = build_report("Required: Python.\nNice to have:\nKubernetes\n", corpus("python"))
        tools = next(c for c in report.breakdown if c.name == "tools")
        assert tools.weight == pytest.approx(WEIGHTS["tools"] * PRIORITY_WEIGHTS["preferred"])
        # Skills 0.30 at 100%, tools 0.05 at 0%: 0.30 / 0.35.
        assert report.score == 86

    def test_a_years_demand_can_be_preferred(self) -> None:
        report = build_report("3+ years of experience preferred.", corpus("python", months=12))
        assert _find(report, "3 years experience").priority == "preferred"

    def test_a_posting_with_no_cues_is_all_required(self) -> None:
        sentences = split_by_priority("We need Python and SQL.\nAlso Airflow.")
        assert {sentence.priority for sentence in sentences} == {"required"}

    def test_priority_weights_are_published(self) -> None:
        report = build_report("We need Python.", corpus("python"))
        assert report.priority_weights == PRIORITY_WEIGHTS


def _gate(report: AtsReport, name: str) -> Gate:
    return next(gate for gate in report.gates if gate.name == name)


class TestGates:
    """Degree, years and location as pass/fail filters.

    A failed gate caps the score, because a screen that filters on it rejects
    the application before it reads a single skill. A fact the record does not
    hold is ``unverified`` and never caps: missing data is not a shortfall.
    """

    def test_a_lower_degree_fails_and_caps_the_score(self) -> None:
        report = build_report(
            "Required: Python, SQL. A Master's degree is required.",
            corpus("python sql", education="bsc mathematics"),
        )
        gate = _gate(report, "degree")
        assert (gate.status, gate.required, gate.found) == (
            "fail",
            "master's degree",
            "bachelor's degree",
        )
        assert report.uncapped_score > GATE_CAP
        assert report.score == GATE_CAP
        assert report.capped is True
        assert report.band == "Weak match"

    def test_a_higher_degree_satisfies_a_lower_demand(self) -> None:
        report = build_report(
            "A bachelor's degree is required.", corpus("x", education="m.sc statistics")
        )
        assert _gate(report, "degree").status == "pass"

    def test_either_level_reads_as_the_lower(self) -> None:
        report = build_report(
            "Bachelor's or Master's degree required.", corpus("x", education="bsc")
        )
        gate = _gate(report, "degree")
        assert (gate.required, gate.status) == ("bachelor's degree", "pass")

    @pytest.mark.parametrize(
        "jd",
        [
            "A Master's degree or equivalent experience is required.",
            "A Master's degree is preferred.",
            "Scrum Master certification required.",
            "You will need a high degree of ownership.",
        ],
    )
    def test_no_degree_gate_without_a_hard_demand(self, jd: str) -> None:
        report = build_report(jd, corpus("x", education="bsc"))
        assert not [gate for gate in report.gates if gate.name == "degree"]

    def test_no_education_on_record_is_unverified_not_failed(self) -> None:
        report = build_report("Required: Python. A Master's degree is required.", corpus("python"))
        assert _gate(report, "degree").status == "unverified"
        assert report.capped is False

    def test_years_well_short_fail_and_cap(self) -> None:
        report = build_report(
            "Required: Python. 5+ years of experience required.", corpus("python", months=12)
        )
        assert _gate(report, "experience").status == "fail"
        assert report.score == GATE_CAP

    def test_years_within_a_year_pass_with_a_note(self) -> None:
        report = build_report("5+ years of experience required.", corpus("python", months=50))
        gate = _gate(report, "experience")
        assert gate.status == "pass"
        assert "within a year" in gate.detail

    def test_no_dated_experience_is_unverified(self) -> None:
        report = build_report(
            "Required: Python. 5+ years of experience required.", corpus("python")
        )
        assert _gate(report, "experience").status == "unverified"
        assert report.capped is False

    def test_preferred_years_do_not_gate(self) -> None:
        report = build_report("3+ years of experience preferred.", corpus("python", months=12))
        assert not [gate for gate in report.gates if gate.name == "experience"]

    def test_the_largest_required_figure_binds(self) -> None:
        report = build_report(
            "2 years of SQL and 5 years of Python required.", corpus("python sql", months=36)
        )
        gate = _gate(report, "experience")
        assert (gate.required, gate.status) == ("5+ years of experience", "fail")

    def test_a_renamed_city_matches(self) -> None:
        report = build_report(
            "Location: Bangalore, India\nWe need Python.",
            corpus("python", location="Bengaluru, India"),
        )
        assert _gate(report, "location").status == "pass"

    def test_another_city_is_flagged_not_failed(self) -> None:
        """Only the candidate knows whether they would relocate, so a
        different city never caps the score."""
        report = build_report(
            "Location: Mumbai\nRequired: Python.", corpus("python", location="Bengaluru, India")
        )
        gate = _gate(report, "location")
        assert gate.status == "unverified"
        assert "relocate" in gate.detail
        assert report.capped is False

    def test_the_country_alone_is_not_a_match(self) -> None:
        report = build_report(
            "Location: Mumbai, India", corpus("python", location="Bengaluru, India")
        )
        assert _gate(report, "location").status == "unverified"

    def test_a_remote_role_passes(self) -> None:
        report = build_report("This is a fully remote role.", corpus("python", location="Kochi"))
        assert _gate(report, "location").status == "pass"

    def test_a_location_in_a_sentence_is_read(self) -> None:
        report = build_report(
            "The role is based in Pune. You will use Python.",
            corpus("python", location="Pune, India"),
        )
        gate = _gate(report, "location")
        assert (gate.required, gate.status) == ("Pune", "pass")

    def test_no_stated_requirement_means_no_gate(self) -> None:
        assert build_report("We need Python.", corpus("python", location="Pune")).gates == []


class TestBands:
    @pytest.mark.parametrize(
        ("score", "band"),
        [
            (100, "Strong match"),
            (80, "Strong match"),
            (79, "Moderate match"),
            (60, "Moderate match"),
            (59, "Partial match"),
            (40, "Partial match"),
            (39, "Weak match"),
            (0, "Weak match"),
        ],
    )
    def test_boundaries(self, score: int, band: str) -> None:
        assert band_for(score) == band


class TestIndependenceFromGeneration:
    def test_the_module_cannot_reach_the_renderer(self) -> None:
        """Feature 2 must not be able to generate a resume.

        Asserted against the module source rather than left to a comment: an
        import added here in six months is exactly how "standalone" quietly
        stops being true.
        """
        from pathlib import Path

        from resume_tailor.domain import ats as module

        text = Path(module.__file__ or "").read_text(encoding="utf-8")
        for forbidden in ("resume_tailor.render", "ResumeSpec", "pagefit", "PdfEngine"):
            assert forbidden not in text


def _find(report: AtsReport, term: str) -> Requirement:
    for category in report.breakdown:
        for requirement in category.requirements:
            if requirement.term == term:
                return requirement
    raise AssertionError(f"no requirement {term!r} in report")


class TestVocabularyPartition:
    def test_the_vocabularies_are_pairwise_disjoint(self) -> None:
        """A term in two vocabularies becomes two requirements in two weighted
        categories, which double-counts one thing the posting asked for once --
        directly against the "credited once" property this module promises."""
        from itertools import combinations

        from resume_tailor.domain.vocabulary import (
            CERTIFICATION_TERMS,
            DOMAINS,
            EDUCATION_TERMS,
            RESPONSIBILITIES,
            SKILLS,
            TOOLS,
        )

        named = {
            "SKILLS": SKILLS,
            "TOOLS": TOOLS,
            "DOMAINS": DOMAINS,
            "RESPONSIBILITIES": RESPONSIBILITIES,
            "EDUCATION_TERMS": EDUCATION_TERMS,
            "CERTIFICATION_TERMS": CERTIFICATION_TERMS,
        }
        for (left, left_terms), (right, right_terms) in combinations(named.items(), 2):
            assert not (left_terms & right_terms), f"{left} and {right} share terms"

    def test_a_term_is_scored_in_exactly_one_category(self) -> None:
        report = build_report(
            "We need Python, transformers, statistics and code review.", corpus("python")
        )
        terms = [
            requirement.term
            for category in report.breakdown
            for requirement in category.requirements
        ]
        assert len(terms) == len(set(terms))


class TestKeywordCap:
    def test_the_cap_keeps_the_terms_the_posting_mentions_first(self) -> None:
        """Alphabetical truncation used to drop Snowflake and Terraform while
        keeping Aardvark, purely on spelling."""
        early = "Zephyrbase"
        filler = " ".join(f"Widget{index}" for index in range(40))
        report = build_report(f"You will use {early}. Also: {filler}.", corpus("cobol"))
        keywords = next(c for c in report.breakdown if c.name == "keywords")
        assert len(keywords.requirements) == MAX_KEYWORD_REQUIREMENTS
        assert keywords.requirements[0].term == early.lower()

    def test_keyword_order_is_document_order_not_alphabetical(self) -> None:
        report = build_report("First Zebra, then Aardvark.", corpus("cobol"))
        keywords = next(c for c in report.breakdown if c.name == "keywords")
        assert [r.term for r in keywords.requirements] == ["zebra", "aardvark"]


class TestExperienceProvenance:
    def test_a_years_match_names_the_sources_that_dated_it(self) -> None:
        both = CandidateCorpus(
            sources=(NamedCorpus(name="profile", text=normalize("python")),),
            experience_months=72,
            experience_sources=("knowledge", "profile"),
        )
        requirement = _find(build_report("5+ years required.", both), "5 years experience")
        assert requirement.matched_sources == ["knowledge", "profile"]
        assert "knowledge, profile" in requirement.detail


class TestEducationConsistency:
    def test_degree_and_bachelor_agree_about_the_same_candidate(self) -> None:
        """Relatedness is one hop, not transitive. Without the abbreviations
        wired to "degree" as well, one qualification produced two contradictory
        statuses in one category."""
        report = build_report(
            "A bachelor degree is required.", corpus("b.tech in computer science")
        )
        statuses = {r.term: r.status for c in report.breakdown for r in c.requirements}
        assert statuses["bachelor"] == statuses["degree"] == "related"
