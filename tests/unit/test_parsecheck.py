"""The PDF text-extraction check (spec section 2, criterion 4).

The property under test throughout: *what the spec puts on the page is what a
text extractor reads back*. Everything else in this file exists to prove that
each way of breaking it is actually detected, because a check that cannot fail
is indistinguishable from no check at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from resume_tailor.domain.matching import normalize
from resume_tailor.domain.models import ResumeSpec
from resume_tailor.render.engines.fake import FakeEngine, document_lines, document_links, make_pdf
from resume_tailor.render.parsecheck import (
    FAIL_COVERAGE,
    MAX_WORD_CHARS,
    MIN_CHARACTERS,
    SECTION_HEADINGS,
    Finding,
    ParseCheck,
    _appears_split,
    _expand_ligatures,
    _missing_term_findings,
    check_parse,
    expected_links,
    expected_sections,
    expected_text,
    extract_pdf_links,
    extract_pdf_text,
    tokenize,
)
from resume_tailor.services.resume_service import ResumeService

TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "resume_tailor"
    / "render"
    / "templates"
    / "resume.tex.j2"
)


def diagnose(text: str, missing: list[str]) -> dict[str, Finding]:
    """Classify missing terms against extracted text, keyed by cause.

    Coverage is passed low enough to stay below the reporting threshold, so
    these tests are about *which* cause is named rather than about when the
    check decides a loss is large enough to mention.
    """
    findings = _missing_term_findings(0.5, missing, text, normalize(text))
    return {finding.code: finding for finding in findings}


@pytest.fixture
def spec(service: ResumeService) -> ResumeSpec:
    return service.build_spec(["proj_a", "proj_b"], max_pages=2)


@pytest.fixture
def compiled(service: ResumeService, spec: ResumeSpec) -> bytes:
    tex, _ = service.render_preview(spec)
    return FakeEngine().compile(tex, timeout_s=5).pdf_bytes


class TestTokenize:
    def test_normalises_case(self) -> None:
        """The pattern only starts a token at a lowercase letter, so raw text
        would yield ``ython`` -- a phantom miss on one side and a phantom term
        on the other."""
        assert "python" in tokenize("Python and PYTHON")
        assert "ython" not in tokenize("Python")

    @pytest.mark.parametrize("term", ["scikit-learn", "node.js", "test@example.com", "ci/cd"])
    def test_internal_punctuation_survives(self, term: str) -> None:
        """A parser that splits these is doing the thing the check exists to
        catch; a tokenizer that split them first would hide it."""
        assert term in tokenize(f"we use {term} daily")

    def test_edge_punctuation_is_trimmed(self) -> None:
        assert "python" in tokenize("python, sql.")

    def test_short_tokens_are_dropped(self) -> None:
        assert tokenize("a ml is of") == set()

    def test_empty_text_yields_nothing(self) -> None:
        assert tokenize("") == set()


class TestExpectedText:
    def test_header_fields_are_expected(self, spec: ResumeSpec) -> None:
        text = expected_text(spec)
        assert "test person" in text
        assert "test@example.com" in text
        assert "testville" in text

    def test_link_display_text_is_expected_not_the_url(self, spec: ResumeSpec) -> None:
        """The template prints ``LinkedIn: <display>``; the URL itself is the
        annotation target and never appears as words on the page."""
        text = expected_text(spec)
        assert "linkedin.com/in/test" in text
        assert "https://www.linkedin.com/in/test/" not in text

    def test_latex_markup_is_reduced_to_what_is_printed(self, spec: ResumeSpec) -> None:
        text = expected_text(spec)
        assert "markup" in text
        assert "textbf" not in text
        assert "50%" in text

    def test_project_bullets_are_expected(self, spec: ResumeSpec) -> None:
        assert "fastapi" in expected_text(spec)

    def test_every_template_heading_is_declared(self) -> None:
        """A heading added to the template and forgotten in ``SECTION_HEADINGS``
        would quietly stop being checked, which is the failure mode this whole
        module exists to prevent."""
        source = TEMPLATE.read_text(encoding="utf-8")
        in_template = set(re.findall(r"\\section\*?\{([^{}]+)\}", source))
        assert in_template == {heading for heading, _ in SECTION_HEADINGS}

    def test_a_section_with_no_content_is_not_expected(self, service: ResumeService) -> None:
        spec = service.build_spec([], max_pages=2)
        headings = {heading for heading, _ in expected_sections(spec)}
        assert "Projects" not in headings
        assert "Summary" in headings


class TestExpectedLinks:
    def test_every_href_target_is_listed(self, spec: ResumeSpec) -> None:
        links = expected_links(spec)
        assert "mailto:test@example.com" in links
        assert "https://github.com/testuser/project-a" in links

    def test_targets_are_deduplicated(self, spec: ResumeSpec) -> None:
        assert len(expected_links(spec)) == len(set(expected_links(spec)))

    def test_it_agrees_with_what_the_renderer_emits(
        self, service: ResumeService, spec: ResumeSpec
    ) -> None:
        """Two independent derivations of the same set. If they ever disagree,
        one of them is describing a resume that is not being produced."""
        tex, _ = service.render_preview(spec)
        assert sorted(document_links(tex)) == sorted(expected_links(spec))


class TestExtraction:
    def test_text_round_trips_through_a_compiled_pdf(self, compiled: bytes) -> None:
        text = extract_pdf_text(compiled).lower()
        assert "test person" in text
        assert "fastapi" in text

    def test_links_round_trip_as_annotations(self, compiled: bytes) -> None:
        assert "mailto:test@example.com" in extract_pdf_links(compiled)

    def test_a_pdf_with_no_annotations_yields_no_links(self) -> None:
        assert extract_pdf_links(make_pdf(1)) == []

    def test_unreadable_bytes_are_reported_not_raised(self) -> None:
        """Generation has already succeeded by here. Refusing to hand over a
        compiled resume because the check on it blew up would be worse than
        the problem it is reporting."""
        assert extract_pdf_text(b"not a pdf at all") == ""
        assert extract_pdf_links(b"not a pdf at all") == []

    def test_a_truncated_pdf_is_reported_not_raised(self) -> None:
        assert extract_pdf_text(make_pdf(2)[:80]) == ""


class TestCheckParse:
    def test_a_clean_document_passes_completely(self, compiled: bytes, spec: ResumeSpec) -> None:
        check = check_parse(compiled, spec)
        assert check.status == "pass"
        assert check.parses
        assert check.findings == ()
        assert check.term_coverage == 1.0
        assert check.missing_terms == ()

    def test_it_reports_what_it_measured(self, compiled: bytes, spec: ResumeSpec) -> None:
        check = check_parse(compiled, spec)
        assert check.expected_terms > 0
        assert check.characters > MIN_CHARACTERS
        assert check.words > 0
        assert check.note

    def test_every_expected_link_is_clickable(self, compiled: bytes, spec: ResumeSpec) -> None:
        check = check_parse(compiled, spec)
        assert sorted(check.links) == sorted(expected_links(spec))

    def test_a_blank_pdf_fails(self, service: ResumeService, spec: ResumeSpec) -> None:
        """The whole point of the check: a document that looks like a PDF, has
        the right page count, and says nothing to a parser."""
        tex, _ = service.render_preview(spec)
        blank = FakeEngine(emit_blank_pages=True).compile(tex, timeout_s=5).pdf_bytes
        check = check_parse(blank, spec)
        assert check.status == "fail"
        assert not check.parses
        assert next(finding.code for finding in check.findings) == "no_text"

    def test_an_unreadable_pdf_fails(self, spec: ResumeSpec) -> None:
        check = check_parse(b"%PDF-1.4 truncated", spec)
        assert check.status == "fail"
        assert check.findings[0].code == "no_text"

    def test_a_nearly_empty_pdf_fails(self, spec: ResumeSpec) -> None:
        check = check_parse(make_pdf(1, lines=["Test Person"]), spec)
        assert check.status == "fail"
        assert check.findings[0].code == "little_text"
        assert str(MIN_CHARACTERS) in check.findings[0].detail

    def test_losing_most_of_the_page_fails(self, spec: ResumeSpec) -> None:
        """Half a resume extracting is not a warning. The score was computed
        from the whole document and no longer describes what an ATS reads."""
        kept = expected_text(spec).split()
        half = " ".join(kept[: len(kept) // 2])
        check = check_parse(make_pdf(1, lines=[half]), spec)
        assert check.status == "fail"
        assert check.findings[0].code == "text_loss"
        assert check.term_coverage < FAIL_COVERAGE

    def test_losing_a_few_words_only_warns(self, spec: ResumeSpec) -> None:
        words = expected_text(spec).split()
        check = check_parse(make_pdf(1, lines=[" ".join(words[:-3]), "x" * 5]), spec)
        assert check.status == "warn"
        assert check.parses, "a mostly-readable resume is still worth sending"
        assert check.findings[0].code == "text_loss"

    def test_missing_words_are_named(self, spec: ResumeSpec) -> None:
        text = expected_text(spec).replace("fastapi", "")
        check = check_parse(make_pdf(1, lines=[text]), spec)
        assert "fastapi" in check.missing_terms

    def test_missing_words_are_capped(self, spec: ResumeSpec) -> None:
        check = check_parse(make_pdf(1, lines=["Test Person " * 40]), spec)
        assert 0 < len(check.missing_terms) <= 25

    def test_a_printed_but_unclickable_link_warns(self, spec: ResumeSpec) -> None:
        """``hyperref`` not producing annotations is invisible on screen and
        total to anything that follows links."""
        check = check_parse(make_pdf(1, lines=[expected_text(spec)]), spec)
        codes = [finding.code for finding in check.findings]
        assert "links_not_clickable" in codes
        assert check.parses, "an unclickable link never makes a resume unreadable"

    def test_a_hostile_link_scheme_warns(self, spec: ResumeSpec) -> None:
        pdf = make_pdf(
            1,
            lines=[expected_text(spec)],
            links=[*expected_links(spec), "javascript:alert(1)"],
        )
        codes = [finding.code for finding in check_parse(pdf, spec).findings]
        assert "link_scheme" in codes

    def test_words_running_together_warn(self, spec: ResumeSpec) -> None:
        glued = "a" * (MAX_WORD_CHARS + 5)
        pdf = make_pdf(1, lines=[expected_text(spec), glued], links=expected_links(spec))
        codes = [finding.code for finding in check_parse(pdf, spec).findings]
        assert "missing_spaces" in codes

    def test_it_is_one_directional(self, spec: ResumeSpec) -> None:
        """Extra text in the PDF is not a defect. The template prints labels no
        model holds, and flagging those would make the check noise."""
        pdf = make_pdf(
            1,
            lines=[expected_text(spec), "an entirely unexpected sentence about otters"],
            links=expected_links(spec),
        )
        assert check_parse(pdf, spec).status == "pass"

    def test_it_is_deterministic(self, compiled: bytes, spec: ResumeSpec) -> None:
        assert check_parse(compiled, spec) == check_parse(compiled, spec)


class TestDiagnosis:
    """Naming the cause is the point.

    "3.4% of words are missing" is a number; "these words are set with
    ligatures and these others are split by kerning" is a diagnosis, and the
    two have different remedies. Each cause is proven separately here.
    """

    def test_a_ligature_word_is_reported_as_a_ligature(self) -> None:
        """Verified against a real Tectonic compile, which sets ``fi`` as one
        glyph: the word is on the page and unreadable to a keyword scan.

        Classified directly rather than through a generated PDF, because the
        placeholder engine writes WinAnsi bytes and cannot express a ligature
        codepoint at all -- only a real typesetter produces this.
        """
        findings = diagnose("we built a classiﬁcation model", ["classification"])
        assert "ligatures" in findings
        assert "text_loss" not in findings, "a ligature is a known cause, not a mystery"
        assert "classification" in findings["ligatures"].detail

    def test_a_ligature_is_not_mistaken_for_a_split(self) -> None:
        findings = diagnose("ﬁve ﬁxed workﬂows", ["five", "fixed", "workflows"])
        assert set(findings) == {"ligatures"}

    def test_a_kerned_word_is_reported_as_a_split(self, spec: ResumeSpec) -> None:
        """What Tectonic does to ``Frameworks``: the extractor reads the wide
        ``F r`` kern as a word boundary."""
        page = expected_text(spec).replace("fastapi", "f astapi")
        findings = {
            finding.code: finding
            for finding in check_parse(make_pdf(1, lines=[page]), spec).findings
        }
        assert "split_words" in findings
        assert "fastapi" in findings["split_words"].detail

    def test_a_genuinely_absent_word_is_reported_as_lost(self, spec: ResumeSpec) -> None:
        page = expected_text(spec).replace("fastapi", "")
        findings = {
            finding.code: finding
            for finding in check_parse(make_pdf(1, lines=[page]), spec).findings
        }
        assert "text_loss" in findings
        assert "fastapi" in findings["text_loss"].detail

    def test_causes_are_reported_separately(self) -> None:
        """The shape of the real Tectonic result: some words lost to ligatures,
        some to kerning, and each named with its own remedy."""
        findings = diagnose(
            "a classiﬁcation model, F rameworks, and nothing about otters",
            ["classification", "frameworks", "kubernetes"],
        )
        assert set(findings) == {"ligatures", "split_words", "text_loss"}
        assert "classification" in findings["ligatures"].detail
        assert "frameworks" in findings["split_words"].detail
        assert "kubernetes" in findings["text_loss"].detail

    @pytest.mark.parametrize(
        ("ligature", "letters"),
        [("ﬀ", "ff"), ("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬃ", "ffi")],
    )
    def test_every_mapped_ligature_expands(self, ligature: str, letters: str) -> None:
        assert _expand_ligatures(f"a{ligature}b") == f"a{letters}b"

    @pytest.mark.parametrize("split", ["f astapi", "fast api", "fastap i"])
    def test_a_space_anywhere_inside_counts_as_split(self, split: str) -> None:
        assert _appears_split("fastapi", f"we use {split} daily")

    def test_two_genuinely_separate_words_are_not_a_split(self) -> None:
        assert not _appears_split("fastapi", "we use flask and django")

    def test_a_dash_range_is_not_a_missing_word(self, service: ResumeService) -> None:
        """LaTeX turns ``--`` into an en dash, so the source's ``8--13`` and the
        page's ``8-13`` are the same authored text written two ways. Reporting
        that as lost content was a false alarm found on the first real compile.
        """
        spec = service.build_spec(["proj_a"], summary="Cut variance inflation to 8--13 percent.")
        page = expected_text(spec).replace("8--13", "8\u201313")
        check = check_parse(make_pdf(1, lines=[page], links=expected_links(spec)), spec)
        assert check.status == "pass"

    def test_a_hyphenated_term_is_still_one_word(self) -> None:
        """The other half of the dash rule: splitting ``scikit-learn`` would
        turn one real keyword match into two that no posting asks for."""
        assert "scikit-learn" in tokenize("we use scikit-learn")


class TestStatus:
    def test_a_failure_outranks_a_warning(self) -> None:
        check = ParseCheck(
            characters=10,
            words=2,
            term_coverage=0.5,
            expected_terms=4,
            missing_terms=(),
            links=(),
            findings=(Finding("w", "warn", "x"), Finding("f", "fail", "y")),
        )
        assert check.status == "fail"
        assert not check.parses

    def test_no_findings_is_a_pass(self) -> None:
        check = ParseCheck(
            characters=10,
            words=2,
            term_coverage=1.0,
            expected_terms=4,
            missing_terms=(),
            links=(),
            findings=(),
        )
        assert check.status == "pass"
        assert check.parses


class TestFakeEngineFidelity:
    """The fake engine has to emit real text, or the check above proves nothing.

    These tests pin the properties the parse check depends on -- not the
    typesetting, which the fake engine explicitly does not model.
    """

    def test_the_documents_words_reach_the_page(self, service: ResumeService) -> None:
        spec = service.build_spec(["proj_a"], max_pages=2)
        tex, _ = service.render_preview(spec)
        text = extract_pdf_text(FakeEngine().compile(tex, timeout_s=5).pdf_bytes).lower()
        assert "test person" in text
        assert "project a" in text

    def test_markup_does_not_reach_the_page(self, service: ResumeService) -> None:
        tex, _ = service.render_preview(service.build_spec(["proj_a"], max_pages=2))
        joined = " ".join(document_lines(tex))
        assert "textbf" not in joined
        assert "begin" not in joined
        assert "section*" not in joined

    def test_text_is_spread_across_every_page(self) -> None:
        pdf = make_pdf(3, lines=[f"line number {index}" for index in range(30)])
        assert "line number 29" in extract_pdf_text(pdf)

    def test_a_caller_that_wants_blank_pages_still_gets_them(self) -> None:
        """The original signature, unchanged: existing page-count tests must
        not have to care that text became available."""
        assert extract_pdf_text(make_pdf(2)).strip() == ""

    def test_parentheses_do_not_break_the_content_stream(self) -> None:
        """An unescaped ``)`` ends a PDF string early and corrupts everything
        after it -- and resume bullets are full of parentheses."""
        pdf = make_pdf(1, lines=[r"Built (in Python) a \ thing (fast)"])
        assert "Built (in Python) a" in extract_pdf_text(pdf)

    def test_blank_page_mode_emits_no_text(self) -> None:
        engine = FakeEngine(emit_blank_pages=True)
        pdf = engine.compile("hello world", timeout_s=5).pdf_bytes
        assert extract_pdf_text(pdf).strip() == ""
