"""JD-to-project matching.

The regression that drove the rewrite of this module has its own class below:
substring containment meant the one-character keyword ``r`` matched nearly any
job description, and the real bank contains that keyword.
"""

from __future__ import annotations

import pytest

from resume_tailor.domain.matching import (
    build_bank_haystack,
    count_matches,
    extract_meaningful_terms,
    find_gap_terms,
    match_bank,
    normalize,
    score_project,
)
from resume_tailor.domain.models import ProjectBank

pytestmark = pytest.mark.unit


class TestNormalize:
    def test_lowercases_and_collapses_whitespace(self) -> None:
        assert normalize("  PYTHON   and\n SQL ") == "python and sql"

    @pytest.mark.parametrize("token", ["c++", "node.js", ".net", "ci/cd", "c#"])
    def test_preserves_intra_token_punctuation(self, token: str) -> None:
        """The original stripped every non-alphanumeric character, destroying
        these tokens before matching ever ran."""
        assert token in normalize(f"experience with {token} required")


class TestBoundaryMatching:
    def test_single_letter_keyword_does_not_match_r_and_d(self) -> None:
        # The exact defect: "R&D" scored a hit on the R-language keyword.
        assert count_matches(normalize("We are an R&D team"), "r") == 0

    def test_single_letter_keyword_matches_a_standalone_token(self) -> None:
        assert count_matches(normalize("Skills: Python, R, SQL"), "r") == 1

    def test_keyword_does_not_match_inside_a_longer_word(self) -> None:
        assert count_matches(normalize("we use nosql stores"), "sql") == 0

    def test_keyword_matches_at_a_boundary(self) -> None:
        assert count_matches(normalize("strong SQL skills"), "sql") == 1

    def test_multiword_keyword_matches_hyphenated_form(self) -> None:
        assert count_matches(normalize("machine-learning engineer"), "machine learning") == 1

    def test_multiword_keyword_matches_spaced_form(self) -> None:
        assert count_matches(normalize("machine learning engineer"), "machine learning") == 1

    def test_plural_matches_for_long_keywords(self) -> None:
        assert count_matches(normalize("build models daily"), "model") == 1

    def test_no_plural_widening_for_short_keywords(self) -> None:
        """Allowing "r" to match "rs" would re-open a narrower version of the
        very bug this module was rewritten to fix."""
        assert count_matches(normalize("the rs value"), "r") == 0

    def test_alias_is_credited_to_the_canonical_keyword(self) -> None:
        assert count_matches(normalize("deep ML experience"), "machine learning") == 1

    def test_case_insensitive(self) -> None:
        assert count_matches(normalize("PYTHON AND SQL"), "python") == 1

    def test_counts_repeat_occurrences(self) -> None:
        assert count_matches(normalize("python, python, python"), "python") == 3

    def test_empty_keyword_scores_nothing(self) -> None:
        assert count_matches("anything", "") == 0


class TestScoreProject:
    def test_keyword_hits_are_reported(self, sample_bank: ProjectBank) -> None:
        result = score_project("we need Python and SQL", "proj_a", sample_bank.projects["proj_a"])
        assert set(result.matched_keywords) == {"python", "sql"}
        assert result.score >= 2

    def test_domain_hit_adds_bonus(self, sample_bank: ProjectBank) -> None:
        result = score_project("a fintech role", "proj_a", sample_bank.projects["proj_a"])
        assert result.domain_match is True
        assert result.matched_domains == ["fintech"]
        assert result.score == 2

    def test_no_overlap_scores_zero(self, sample_bank: ProjectBank) -> None:
        result = score_project("graphic designer wanted", "proj_a", sample_bank.projects["proj_a"])
        assert result.score == 0
        assert result.matched_keywords == []

    def test_title_is_display_text_not_raw_latex(self, sample_bank: ProjectBank) -> None:
        result = score_project("python", "proj_a", sample_bank.projects["proj_a"])
        assert "\\textbf" not in result.title

    def test_coverage_is_a_fraction_of_the_projects_own_keywords(
        self, sample_bank: ProjectBank
    ) -> None:
        result = score_project("python", "proj_a", sample_bank.projects["proj_a"])
        assert result.coverage == pytest.approx(1 / 4)

    def test_raw_jd_text_is_normalised_internally(self, sample_bank: ProjectBank) -> None:
        """Callers must not have to pre-normalise; forgetting used to produce a
        valid-looking all-zero result with no error at all."""
        assert score_project("PYTHON!", "proj_a", sample_bank.projects["proj_a"]).score == 1


class TestMatchBank:
    def test_ranks_by_score_descending(self, sample_bank: ProjectBank) -> None:
        results = match_bank("Python, SQL and machine learning for fintech", sample_bank)
        assert results[0].key == "proj_a"
        assert results[0].score >= results[1].score

    def test_hidden_projects_are_excluded_by_default(self, sample_bank: ProjectBank) -> None:
        keys = [result.key for result in match_bank("experimental", sample_bank)]
        assert "proj_hidden" not in keys

    def test_hidden_projects_can_be_included_explicitly(self, sample_bank: ProjectBank) -> None:
        keys = [r.key for r in match_bank("experimental", sample_bank, include_hidden=True)]
        assert "proj_hidden" in keys

    def test_empty_jd_scores_everything_zero(self, sample_bank: ProjectBank) -> None:
        assert all(result.score == 0 for result in match_bank("", sample_bank))

    def test_ordering_is_deterministic_for_ties(self, sample_bank: ProjectBank) -> None:
        """Ties used to fall back on dict insertion order, so results depended
        on how the JSON file happened to be written."""
        first = [r.key for r in match_bank("nothing matches here", sample_bank)]
        second = [r.key for r in match_bank("nothing matches here", sample_bank)]
        assert first == second == sorted(first)

    def test_real_bank_ranks_a_relevant_jd_sensibly(self, real_bank: ProjectBank) -> None:
        jd = "Data Scientist: credit risk, fraud detection, XGBoost, SHAP, Python, SQL."
        top = [result.key for result in match_bank(jd, real_bank)[:3]]
        assert "credit_default" in top or "aml_fraud" in top

    def test_real_bank_is_not_fooled_by_r_and_d(self, real_bank: ProjectBank) -> None:
        # Reproduced against the real bank during the audit: this JD used to
        # rank the ARIMA project first purely on the letter R.
        jd = "We are hiring an R&D engineer. Rust, Ruby and AI-adjacent chains."
        assert all(result.score == 0 for result in match_bank(jd, real_bank))


class TestExtractTerms:
    def test_extracts_acronyms(self) -> None:
        terms = extract_meaningful_terms("Experience with AWS and GCP required.")
        assert {"AWS", "GCP"} <= set(terms)

    def test_extracts_terms_containing_digits(self) -> None:
        assert "Python3" in extract_meaningful_terms("Familiarity with Python3 helps.")

    def test_ignores_sentence_initial_capitals(self) -> None:
        terms = extract_meaningful_terms("We need someone great. Excellent communication.")
        assert "We" not in terms
        assert "Excellent" not in terms

    def test_extracts_midsentence_proper_nouns(self) -> None:
        terms = extract_meaningful_terms("Experience with Docker and Kubernetes is required.")
        assert {"Docker", "Kubernetes"} <= set(terms)

    def test_does_not_split_dotted_technology_names(self) -> None:
        """Splitting sentences on a bare "." tore Node.js and .NET in half."""
        assert "Node.js" in extract_meaningful_terms("Some Node.js experience helps.")

    def test_strips_surrounding_punctuation(self) -> None:
        assert "Kubernetes" in extract_meaningful_terms("Tools: (Kubernetes), Docker.")

    def test_empty_input(self) -> None:
        assert extract_meaningful_terms("") == []

    def test_stopwords_excluded(self) -> None:
        assert extract_meaningful_terms("we need Experience and Skills") == []


class TestGapTerms:
    def test_reports_terms_absent_from_the_bank(self, sample_bank: ProjectBank) -> None:
        gaps = find_gap_terms("We require Kubernetes and Terraform experience.", sample_bank)
        assert {"Kubernetes", "Terraform"} <= set(gaps)

    def test_does_not_report_terms_covered_by_keywords(self, sample_bank: ProjectBank) -> None:
        assert "Python" not in find_gap_terms("Strong Python skills needed.", sample_bank)

    def test_does_not_report_terms_that_appear_only_in_bullets(
        self, sample_bank: ProjectBank
    ) -> None:
        """Matching gap terms against the keyword list alone reported false
        gaps for tools named in a bullet but not duplicated as a keyword."""
        assert "FastAPI" not in find_gap_terms("We use FastAPI in production.", sample_bank)

    def test_limit_is_respected(self, sample_bank: ProjectBank) -> None:
        jd = " ".join(f"Tool{index} and" for index in range(50))
        assert len(find_gap_terms(jd, sample_bank, limit=5)) == 5

    def test_haystack_is_cached_per_bank_version(self, sample_bank: ProjectBank) -> None:
        assert build_bank_haystack(sample_bank) is build_bank_haystack(sample_bank)
