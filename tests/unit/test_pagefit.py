"""The page-fit guarantee and the engines that feed it.

The invariant under test, stated once:

    page_count <= max_pages  OR  warning != ""    -- never neither.

Everything else in this file exists to attack that statement from a different
angle.
"""

from __future__ import annotations

import pytest

from resume_tailor.core.config import Settings
from resume_tailor.core.errors import (
    CompilationError,
    CompileTimeoutError,
    EngineUnavailableError,
    InvalidInputError,
    PageCountError,
)
from resume_tailor.render.engines.fake import FakeEngine, estimate_pages, make_pdf
from resume_tailor.render.engines.pdflatex import PdflatexEngine
from resume_tailor.render.engines.registry import available_engines, select_engine
from resume_tailor.render.engines.tectonic import TectonicEngine
from resume_tailor.render.pagefit import compile_with_page_fit, count_pages

pytestmark = pytest.mark.unit

LADDER = [(9.6, 11.5), (9.4, 11.3), (9.2, 11.0), (9.0, 10.8), (8.8, 10.6)]


def source_of(length: int, font_size: float) -> str:
    return f"\\fontsize{{{font_size:g}}}{{11.5}}" + "x" * length


def renderer(length: int):
    """A render function whose output length is fixed, so page count depends
    only on the font size the ladder is currently trying."""

    def render(font_size: float, line_spacing: float) -> str:
        del line_spacing
        return source_of(length, font_size)

    return render


class TestCountPages:
    @pytest.mark.parametrize("pages", [1, 2, 5, 12])
    def test_counts_real_pdf_pages(self, pages: int) -> None:
        assert count_pages(make_pdf(pages)) == pages

    def test_non_pdf_bytes_raise_a_typed_error(self) -> None:
        with pytest.raises(PageCountError):
            count_pages(b"this is not a pdf")

    def test_truncated_pdf_raises_a_typed_error(self) -> None:
        with pytest.raises(PageCountError):
            count_pages(make_pdf(2)[:80])

    def test_empty_bytes_raise(self) -> None:
        with pytest.raises(PageCountError):
            count_pages(b"")


class TestFakeEngine:
    def test_produces_a_real_parseable_pdf(self) -> None:
        compiled = FakeEngine().compile(source_of(100, 9.6), timeout_s=5)
        assert compiled.pdf_bytes.startswith(b"%PDF")
        assert count_pages(compiled.pdf_bytes) >= 1

    def test_is_deterministic(self) -> None:
        engine = FakeEngine()
        first = engine.compile(source_of(5000, 9.6), timeout_s=5).pdf_bytes
        second = engine.compile(source_of(5000, 9.6), timeout_s=5).pdf_bytes
        assert first == second

    def test_smaller_font_fits_more_content(self) -> None:
        """Without this the ladder would be untestable: every rung would give
        the same page count and the loop would never be exercised."""
        # 11,000 characters straddles a page boundary: three pages at 9.6pt,
        # two at 8.8pt. Picking a length inside a single bucket would make this
        # assertion pass or fail for reasons unrelated to the font ladder.
        assert estimate_pages(source_of(11_000, 8.8)) < estimate_pages(source_of(11_000, 9.6))

    def test_defaults_to_ten_point_when_no_font_declared(self) -> None:
        assert estimate_pages("x" * 100) == 1

    def test_records_every_call(self) -> None:
        engine = FakeEngine()
        engine.compile("a", timeout_s=1)
        engine.compile("b", timeout_s=1)
        assert engine.compile_calls == ["a", "b"]

    def test_failure_injection(self) -> None:
        engine = FakeEngine(fail_if_source_contains="BOOM")
        with pytest.raises(CompilationError):
            engine.compile("x BOOM x", timeout_s=5)

    def test_timeout_injection(self) -> None:
        engine = FakeEngine(timeout_if_source_contains="HANG")
        with pytest.raises(CompileTimeoutError):
            engine.compile("x HANG x", timeout_s=5)

    def test_status_is_always_available_but_flagged_as_fake(self) -> None:
        status = FakeEngine().status()
        assert status.available is True
        assert status.name == "fake"


class TestPageFitLadder:
    def test_returns_immediately_when_it_fits(self) -> None:
        engine = FakeEngine()
        result = compile_with_page_fit(
            renderer(1000), engine, max_pages=2, font_ladder=LADDER, timeout_s=5
        )
        assert result.attempts == 1
        assert result.font_size == 9.6
        assert result.fits

    def test_steps_down_the_ladder_until_it_fits(self) -> None:
        engine = FakeEngine()
        result = compile_with_page_fit(
            renderer(11_000), engine, max_pages=2, font_ladder=LADDER, timeout_s=5
        )
        assert result.attempts > 1
        assert result.page_count <= 2
        assert result.fits
        assert len(engine.compile_calls) == result.attempts

    def test_exhausted_ladder_warns_and_still_returns_a_pdf(self) -> None:
        result = compile_with_page_fit(
            renderer(60_000), FakeEngine(), max_pages=2, font_ladder=LADDER, timeout_s=5
        )
        assert result.page_count > 2
        assert result.warning
        assert result.fits is False
        assert result.pdf_bytes.startswith(b"%PDF")
        assert result.font_size == 8.8, "the returned PDF is the smallest-font attempt"

    def test_warning_explains_that_trimming_is_needed(self) -> None:
        result = compile_with_page_fit(
            renderer(60_000), FakeEngine(), max_pages=1, font_ladder=LADDER, timeout_s=5
        )
        assert "trimmed" in result.warning

    @pytest.mark.parametrize("length", [10, 3000, 9000, 20_000, 60_000, 200_000])
    @pytest.mark.parametrize("max_pages", [1, 2, 3])
    def test_invariant_holds_across_sizes(self, length: int, max_pages: int) -> None:
        result = compile_with_page_fit(
            renderer(length), FakeEngine(), max_pages=max_pages, font_ladder=LADDER, timeout_s=5
        )
        assert result.page_count <= max_pages or result.warning, (
            "an over-length resume was returned with no warning -- exactly the "
            "silent-overflow failure this tool exists to prevent"
        )

    def test_empty_ladder_is_rejected(self) -> None:
        """The original left `last_result` as None here and crashed on
        `None.warning` after the loop."""
        with pytest.raises(InvalidInputError, match="ladder is empty"):
            compile_with_page_fit(
                renderer(100), FakeEngine(), max_pages=2, font_ladder=[], timeout_s=5
            )

    def test_zero_max_pages_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError, match="at least 1"):
            compile_with_page_fit(
                renderer(100), FakeEngine(), max_pages=0, font_ladder=LADDER, timeout_s=5
            )

    def test_single_rung_ladder_works(self) -> None:
        result = compile_with_page_fit(
            renderer(100), FakeEngine(), max_pages=2, font_ladder=[(9.6, 11.5)], timeout_s=5
        )
        assert result.attempts == 1

    def test_compilation_error_aborts_rather_than_retrying(self) -> None:
        """A LaTeX syntax error is not a function of font size, so retrying
        four more times only multiplies the wait before the same failure."""
        engine = FakeEngine(fail_if_source_contains="fontsize")
        with pytest.raises(CompilationError):
            compile_with_page_fit(
                renderer(100), engine, max_pages=2, font_ladder=LADDER, timeout_s=5
            )
        assert len(engine.compile_calls) == 1

    def test_timeout_propagates_as_its_own_error_type(self) -> None:
        engine = FakeEngine(timeout_if_source_contains="fontsize")
        with pytest.raises(CompileTimeoutError):
            compile_with_page_fit(
                renderer(100), engine, max_pages=2, font_ladder=LADDER, timeout_s=5
            )


class TestEngineRegistry:
    def test_explicit_fake_selection(self) -> None:
        settings = Settings(environment="test", pdf_engine="fake")
        assert select_engine(settings).name == "fake"

    def test_auto_falls_back_to_fake_outside_production(self) -> None:
        settings = Settings(environment="test", pdf_engine="auto")
        assert select_engine(settings).name in {"fake", "tectonic", "pdflatex"}

    def test_auto_refuses_to_fall_back_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A blank placeholder PDF must never be mistaken for a real resume in
        a production deployment."""
        monkeypatch.setattr("resume_tailor.render.engines.base.shutil.which", lambda _name: None)
        settings = Settings(
            environment="prod", pdf_engine="auto", debug=False, cors_origins=["https://x"]
        )
        with pytest.raises(EngineUnavailableError):
            select_engine(settings)

    def test_requesting_a_missing_engine_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("resume_tailor.render.engines.base.shutil.which", lambda _name: None)
        settings = Settings(environment="test", pdf_engine="tectonic")
        with pytest.raises(EngineUnavailableError, match="tectonic"):
            select_engine(settings)

    def test_available_engines_reports_every_real_engine(self) -> None:
        names = {status.name for status in available_engines()}
        assert names == {"tectonic", "pdflatex"}

    @pytest.mark.parametrize("engine_class", [TectonicEngine, PdflatexEngine])
    def test_missing_binary_hint_is_actionable(self, engine_class: type) -> None:
        hint = engine_class().missing_binary_hint()
        assert "install" in hint.lower()

    def test_pdflatex_command_never_enables_shell_escape(self, tmp_path) -> None:
        command = PdflatexEngine().build_command(tmp_path / "resume.tex", tmp_path)
        assert "-no-shell-escape" in command
        assert not any("--shell-escape" in part for part in command)

    def test_tectonic_command_is_untrusted(self, tmp_path) -> None:
        command = TectonicEngine().build_command(tmp_path / "resume.tex", tmp_path)
        assert "--untrusted" in command
