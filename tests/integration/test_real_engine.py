"""Tests that compile actual LaTeX.

Skipped automatically when no engine is installed, and carry the ``integration``
and ``latex`` markers so ``pytest -m "not latex"`` stays green on a bare
machine. CI runs them with Tectonic, which installs in seconds.

These exist because two of the original project's worst bugs -- the raw
underscore in a GitHub repo name, and a template variable colliding with
``dict.items`` -- were *only* ever caught by a real compile. A mocked compiler
would have reported success for both.
"""

from __future__ import annotations

import pytest

from resume_tailor.core.config import Settings
from resume_tailor.core.errors import CompilationError
from resume_tailor.data.bank_repo import BankRepository
from resume_tailor.data.profile_repo import ProfileRepository
from resume_tailor.render.engines.registry import available_engines
from resume_tailor.render.pagefit import count_pages
from resume_tailor.services.resume_service import ResumeService

pytestmark = [pytest.mark.integration, pytest.mark.latex, pytest.mark.slow]


def _installed_engine() -> str | None:
    for status in available_engines():
        if status.available:
            return status.name
    return None


ENGINE_NAME = _installed_engine()

requires_engine = pytest.mark.skipif(
    ENGINE_NAME is None,
    reason="no LaTeX engine installed; install tectonic to run these",
)


@pytest.fixture
def real_engine_service(settings: Settings) -> ResumeService:
    assert ENGINE_NAME is not None
    from resume_tailor.render.engines.registry import select_engine

    # The shared `settings` fixture allows 10s, which is generous for the fake
    # engine and not enough for a real one on a cold cache: Tectonic downloads
    # its package bundle on first use, and a full TeX Live pdflatex run is not
    # instant either. CI warms the cache first, but a developer's first local
    # run does not -- and "your first ever real compile times out" is a bad
    # introduction to a tool that works fine.
    live = settings.model_copy(update={"pdf_engine": ENGINE_NAME, "compile_timeout_s": 180.0})
    return ResumeService(
        settings=live,
        bank_repo=BankRepository(live.bank_path),
        profile_repo=ProfileRepository(live.profile_path),
        engine=select_engine(live),
    )


@requires_engine
class TestRealCompilation:
    def test_compiles_a_real_pdf(self, real_engine_service: ResumeService) -> None:
        result = real_engine_service.generate_sync(real_engine_service.build_spec(["proj_a"]))
        assert result.fit.pdf_bytes.startswith(b"%PDF")
        assert len(result.fit.pdf_bytes) > 1000, "suspiciously small for a real document"
        assert count_pages(result.fit.pdf_bytes) == result.fit.page_count

    def test_underscore_in_github_name_does_not_break_compilation(
        self, real_engine_service: ResumeService
    ) -> None:
        """LaTeX reads a bare underscore as a subscript operator outside maths
        mode; this was a fatal, real bug."""
        result = real_engine_service.generate_sync(real_engine_service.build_spec(["proj_b"]))
        assert result.fit.page_count >= 1

    def test_special_characters_in_content_compile(
        self, real_engine_service: ResumeService
    ) -> None:
        spec = real_engine_service.build_spec(
            ["proj_a"],
            summary="R&D at 50% capacity, $100k budget, #1 ranked, a_b, {braces}, ~ and ^.",
            personal_info={"name": "A & B_C"},
        )
        assert real_engine_service.generate_sync(spec).fit.page_count >= 1

    def test_empty_selection_compiles(self, real_engine_service: ResumeService) -> None:
        assert (
            real_engine_service.generate_sync(real_engine_service.build_spec([])).fit.page_count
            >= 1
        )

    def test_page_fit_guarantee_holds_with_a_real_compiler(
        self, real_engine_service: ResumeService
    ) -> None:
        keys = list(real_engine_service.bank().visible())
        result = real_engine_service.generate_sync(
            real_engine_service.build_spec(keys, max_pages=1)
        )
        assert result.fit.page_count <= 1 or result.fit.warning

    def test_broken_source_raises_a_typed_error(self, real_engine_service: ResumeService) -> None:
        from resume_tailor.render.engines.registry import select_engine

        engine = select_engine(
            Settings(environment="test", pdf_engine=ENGINE_NAME)  # type: ignore[arg-type]
        )
        with pytest.raises(CompilationError):
            engine.compile(r"\documentclass{article}\begin{document}\undefinedmacro", timeout_s=60)

    def test_compilation_is_reproducible(self, real_engine_service: ResumeService) -> None:
        """SOURCE_DATE_EPOCH is pinned in the engine environment, so the same
        input produces identical bytes -- which is what makes golden-file
        comparison on generated PDFs possible at all."""
        spec = real_engine_service.build_spec(["proj_a"])
        first = real_engine_service.generate_sync(spec).fit.pdf_bytes
        second = real_engine_service.generate_sync(spec).fit.pdf_bytes
        assert first == second


@requires_engine
class TestRealProductionContent:
    def test_the_actual_resume_compiles(self, real_settings: Settings) -> None:
        """The content that will really be sent to employers must compile."""
        from resume_tailor.render.engines.registry import select_engine

        live = real_settings.model_copy(
            update={"pdf_engine": ENGINE_NAME, "compile_timeout_s": 180.0}
        )
        service = ResumeService(
            settings=live,
            bank_repo=BankRepository(live.bank_path),
            profile_repo=ProfileRepository(live.profile_path),
            engine=select_engine(live),
        )
        keys = list(service.bank().visible())[:4]
        result = service.generate_sync(service.build_spec(keys, max_pages=2))
        assert result.fit.pdf_bytes.startswith(b"%PDF")
        assert result.fit.page_count <= 2 or result.fit.warning


def test_engine_availability_is_reported_honestly() -> None:
    """Runs everywhere: the point is that a missing engine is *reported*, not
    that one is installed."""
    statuses = available_engines()
    assert statuses
    for status in statuses:
        assert status.available or status.detail, "an unavailable engine must explain itself"
