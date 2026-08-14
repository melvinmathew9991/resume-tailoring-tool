"""Hypothesis property tests.

Example-based tests prove the cases someone thought of. These state the
properties that must hold for *every* input, which is the only honest way to
make a claim like "escaping is safe" about a function that will be fed
arbitrary pasted text.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, assume, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from resume_tailor.domain.latex import (
    count_unbalanced_braces,
    escape_latex,
    escape_user_text,
    find_unescaped_specials,
    find_unknown_commands,
    strip_control_chars,
    unescape_latex,
)
from resume_tailor.domain.matching import count_matches, normalize, score_project
from resume_tailor.domain.models import Project
from resume_tailor.render.engines.fake import FakeEngine
from resume_tailor.render.pagefit import compile_with_page_fit, count_pages
from resume_tailor.services.resume_service import ResumeService

pytestmark = [pytest.mark.property, pytest.mark.unit]

#: Printable text: no control characters, no surrogates. Escaping deliberately
#: strips those, so they belong in their own test rather than muddying the
#: exact round-trip property.
PRINTABLE = st.text(st.characters(exclude_categories=("Cs", "Cc")), max_size=400)

ANY_TEXT = st.text(max_size=400)

LADDER = [(9.6, 11.5), (9.4, 11.3), (9.2, 11.0), (9.0, 10.8), (8.8, 10.6)]


class TestEscapingProperties:
    @given(PRINTABLE)
    def test_round_trip_is_exact(self, text: str) -> None:
        assert unescape_latex(escape_latex(text)) == text

    @given(ANY_TEXT)
    def test_round_trip_after_sanitising_is_exact(self, text: str) -> None:
        assert unescape_latex(escape_latex(text)) == strip_control_chars(text)

    @given(ANY_TEXT)
    def test_escaped_output_never_contains_an_unescaped_special(self, text: str) -> None:
        assert find_unescaped_specials(escape_latex(text)) == []

    @given(ANY_TEXT)
    def test_escaped_output_emits_only_allowlisted_commands(self, text: str) -> None:
        """No input can make escaping produce a control sequence the audit
        would reject -- if it could, arbitrary text could break generation."""
        assert find_unknown_commands(escape_latex(text)) == []

    @given(ANY_TEXT)
    def test_escaped_output_has_balanced_braces(self, text: str) -> None:
        assert count_unbalanced_braces(escape_latex(text)) == 0

    @given(ANY_TEXT)
    def test_escaped_output_has_no_control_characters(self, text: str) -> None:
        assert not any(ord(char) < 32 and char not in "\n\t" for char in escape_latex(text))

    @given(ANY_TEXT)
    def test_user_text_helper_is_also_always_safe(self, text: str) -> None:
        rendered = escape_user_text(text)
        assert find_unescaped_specials(rendered) == []
        assert find_unknown_commands(rendered) == []
        assert count_unbalanced_braces(rendered) == 0

    @given(PRINTABLE)
    def test_escaping_is_deterministic(self, text: str) -> None:
        assert escape_latex(text) == escape_latex(text)

    @given(PRINTABLE, PRINTABLE)
    def test_escaping_distributes_over_concatenation(self, left: str, right: str) -> None:
        """A character-wise transform must not depend on its neighbours; if it
        did, splitting a bullet across two template variables would change it."""
        assert escape_latex(left + right) == escape_latex(left) + escape_latex(right)


class TestMatchingProperties:
    KEYWORD = st.text(st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=12)

    @given(KEYWORD, PRINTABLE)
    def test_score_is_never_negative(self, keyword: str, jd: str) -> None:
        project = Project.model_validate(
            {
                "title": "T",
                "github": "o/r",
                "keywords": [keyword],
                "domain": [],
                "bullets": ["b"],
            }
        )
        result = score_project(jd, "k", project)
        assert result.score >= 0
        assert 0.0 <= result.coverage <= 1.0

    @given(KEYWORD)
    def test_a_standalone_keyword_token_always_matches(self, keyword: str) -> None:
        assert count_matches(normalize(f"skills: {keyword}, and more"), keyword) >= 1

    @given(KEYWORD)
    def test_a_keyword_embedded_in_a_longer_word_never_matches(self, keyword: str) -> None:
        assert count_matches(normalize(f"zz{keyword}zz"), keyword) == 0

    @given(KEYWORD, PRINTABLE)
    def test_matched_keywords_are_always_a_subset_of_the_projects_own(
        self, keyword: str, jd: str
    ) -> None:
        project = Project.model_validate(
            {
                "title": "T",
                "github": "o/r",
                "keywords": [keyword],
                "domain": [],
                "bullets": ["b"],
            }
        )
        result = score_project(jd, "k", project)
        assert set(result.matched_keywords) <= set(project.keywords)


class TestPageFitProperties:
    @given(
        length=st.integers(min_value=1, max_value=120_000),
        max_pages=st.integers(min_value=1, max_value=4),
    )
    @hyp_settings(max_examples=40, deadline=None)
    def test_fits_or_warns_but_never_neither(self, length: int, max_pages: int) -> None:
        """The core safety guarantee, over the whole input space."""

        def render(font_size: float, line_spacing: float) -> str:
            del line_spacing
            return f"\\fontsize{{{font_size:g}}}{{11.5}}" + "x" * length

        result = compile_with_page_fit(
            render, FakeEngine(), max_pages=max_pages, font_ladder=LADDER, timeout_s=5
        )
        assert result.page_count <= max_pages or result.warning
        assert result.fits == (not result.warning)
        assert count_pages(result.pdf_bytes) == result.page_count

    @given(st.integers(min_value=1, max_value=60_000))
    @hyp_settings(max_examples=25, deadline=None)
    def test_attempts_never_exceed_the_ladder(self, length: int) -> None:
        def render(font_size: float, line_spacing: float) -> str:
            del line_spacing
            return f"\\fontsize{{{font_size:g}}}{{11.5}}" + "x" * length

        result = compile_with_page_fit(
            render, FakeEngine(), max_pages=2, font_ladder=LADDER, timeout_s=5
        )
        assert 1 <= result.attempts <= len(LADDER)


class TestSelectionProperties:
    @given(st.lists(st.sampled_from(["proj_a", "proj_b"]), max_size=12))
    @hyp_settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
    def test_selection_is_deduplicated_and_order_preserving(
        self, service: ResumeService, keys: list[str]
    ) -> None:
        resolved, projects = service.resolve_selection(keys)
        assert len(resolved) == len(set(resolved))
        assert len(resolved) == len(projects)
        assert resolved == list(dict.fromkeys(keys))
        assert set(resolved) <= set(keys)

    @given(st.lists(st.sampled_from(["proj_a", "proj_b"]), min_size=1, max_size=6))
    @hyp_settings(
        max_examples=15,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_any_valid_selection_produces_a_pdf(
        self, service: ResumeService, keys: list[str]
    ) -> None:
        assume(keys)
        result = service.generate_sync(service.build_spec(keys))
        assert result.fit.pdf_bytes.startswith(b"%PDF")
        assert result.fit.page_count >= 1
