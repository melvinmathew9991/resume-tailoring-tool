"""Job-description completeness (spec section 1, step 1).

The asymmetry that makes this worth checking at all: a truncated posting scores
*higher* than the real one, because the half that was never pasted is all the
requirements nobody was measured against. A silent failure that looks like good
news is the hardest kind to notice, so the tests here are as much about
precision -- not crying wolf on a complete posting -- as about detection.
"""

from __future__ import annotations

import pytest

from resume_tailor.domain.posting import (
    MIN_POSTING_CHARS,
    check_posting,
)

pytestmark = pytest.mark.unit


FULL = """Senior Data Scientist

About the role
You will build and validate predictive models for our lending business, working
with engineers and product managers to ship them.

Responsibilities
- Build classification and regression models for credit decisions
- Partner with engineering to take models to production
- Present findings to non-technical stakeholders

Requirements
- 3+ years of experience in Python and SQL
- Strong background in statistics and machine learning
- Experience with scikit-learn, XGBoost or similar frameworks
- Bachelor's degree in a quantitative field

Nice to have
- Experience with MLflow or Airflow
"""


def codes(text: str) -> set[str]:
    return {signal.code for signal in check_posting(text).signals}


class TestCompletePostings:
    def test_a_full_posting_raises_nothing(self) -> None:
        check = check_posting(FULL)
        assert check.complete
        assert check.signals == ()
        assert check.has_requirements_section

    def test_it_reports_what_it_measured(self) -> None:
        check = check_posting(FULL)
        assert check.characters > MIN_POSTING_CHARS
        assert check.words > 0
        assert check.note

    def test_a_bullet_ending_is_not_truncation(self) -> None:
        """The common shape: a posting whose last line is a bullet with no full
        stop. Flagging that would fire on most real postings and teach the
        reader to ignore the warning."""
        assert check_posting(FULL.rstrip() + "\n- Exposure to Kubernetes").complete

    @pytest.mark.parametrize("ending", [".", "!", "?", ":", ")", '"', "%"])
    def test_ordinary_terminal_punctuation_is_fine(self, ending: str) -> None:
        assert "ends_mid_sentence" not in codes(FULL.rstrip() + f" Apply today{ending}")

    def test_a_heading_synonym_counts_as_requirements(self) -> None:
        """Postings say this a dozen ways; the check must not insist on the
        word "Requirements"."""
        for heading in ("What you'll need", "Who you are", "The ideal candidate"):
            text = FULL.replace("Requirements", heading)
            assert check_posting(text).has_requirements_section, heading


class TestTruncationMarkers:
    @pytest.mark.parametrize(
        "tail", ["...", "…", "[…]", "Show more", "Read more", "see more", "Continue reading"]
    )
    def test_a_marker_at_the_end_is_caught(self, tail: str) -> None:
        """Nobody ends a job posting with "Show more" -- it is the control the
        selection stopped at."""
        assert "truncation_marker" in codes(f"{FULL.rstrip()}\n{tail}")

    def test_a_marker_mid_text_is_not_a_signal(self) -> None:
        """An ellipsis inside a sentence is punctuation, not a fold."""
        text = FULL.replace("You will build", "You will ... build")
        assert "truncation_marker" not in codes(text)

    def test_the_marker_outranks_the_sentence_check(self) -> None:
        """One cause, one signal. "Show more" already explains the ending, and
        reporting an unfinished sentence as well would double-count it."""
        found = codes(f"{FULL.rstrip()}\nrequirements and ...")
        assert "truncation_marker" in found
        assert "ends_mid_sentence" not in found


class TestUnfinishedEndings:
    @pytest.mark.parametrize("word", ["and", "or", "with", "including", "such", "to"])
    def test_ending_on_a_continuation_word_is_caught(self, word: str) -> None:
        assert "ends_mid_sentence" in codes(f"{FULL.rstrip()} experience {word}")

    @pytest.mark.parametrize("mark", [",", ";", "-", "(", "/", "&"])
    def test_ending_on_dangling_punctuation_is_caught(self, mark: str) -> None:
        assert "ends_mid_sentence" in codes(f"{FULL.rstrip()} experience{mark}")

    def test_an_ordinary_noun_ending_is_not_caught(self) -> None:
        assert "ends_mid_sentence" not in codes(f"{FULL.rstrip()} Strong SQL skills")

    def test_trailing_whitespace_does_not_hide_the_ending(self) -> None:
        assert "ends_mid_sentence" in codes(f"{FULL.rstrip()} experience with   \n\n  ")

    def test_a_quoted_continuation_word_still_counts(self) -> None:
        assert "ends_mid_sentence" in codes(f'{FULL.rstrip()} experience "with')


class TestMissingRequirements:
    def test_a_posting_with_no_requirements_section_is_flagged(self) -> None:
        """The exact case the spec names: requirements behind a collapsed
        panel, so the text reads complete and the scored half is absent."""
        blurb = "About the company\n" + ("We build lending software for banks. " * 20)
        assert "no_requirements_section" in codes(blurb)

    def test_a_posting_with_one_is_not(self) -> None:
        assert "no_requirements_section" not in codes(FULL)


class TestLength:
    def test_a_fragment_is_flagged(self) -> None:
        assert "too_short" in codes("We need a Data Scientist with Python and SQL.")

    def test_a_full_posting_is_not(self) -> None:
        assert "too_short" not in codes(FULL)

    def test_the_threshold_is_measured_on_normalised_text(self) -> None:
        """Whitespace is not content: padding a fragment must not pass it."""
        assert "too_short" in codes("Short posting. " + "\n" * 5_000)


class TestSignalsCombine:
    def test_several_problems_are_reported_together(self) -> None:
        found = codes("Data Scientist needed with")
        assert {"ends_mid_sentence", "too_short", "no_requirements_section"} == found

    def test_complete_is_false_when_anything_fired(self) -> None:
        assert not check_posting("Data Scientist needed with").complete

    def test_it_is_deterministic(self) -> None:
        assert check_posting(FULL) == check_posting(FULL)

    def test_empty_text_does_not_crash(self) -> None:
        """The service rejects empty input before this runs, but a pure
        function that cannot be called safely is a trap for the next caller."""
        check = check_posting("")
        assert not check.complete
        assert "ends_mid_sentence" not in {signal.code for signal in check.signals}
