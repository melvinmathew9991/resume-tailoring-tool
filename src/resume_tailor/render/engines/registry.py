"""Engine selection.

``auto`` probes real engines in preference order and, if none is installed,
falls back to the fake engine *only outside production*. That fallback is
deliberately loud: it logs a warning, and ``/health/ready`` reports the engine
by name so nobody can mistake a blank placeholder PDF for a real resume.
"""

from __future__ import annotations

from resume_tailor.core.config import EngineName, Settings
from resume_tailor.core.errors import EngineUnavailableError
from resume_tailor.core.logging import get_logger
from resume_tailor.render.engines.base import EngineStatus, PdfEngine
from resume_tailor.render.engines.fake import FakeEngine
from resume_tailor.render.engines.pdflatex import PdflatexEngine
from resume_tailor.render.engines.tectonic import TectonicEngine

logger = get_logger(__name__)

#: Preference order for ``auto``. Tectonic first because it is self-contained.
_REAL_ENGINES: tuple[type[TectonicEngine] | type[PdflatexEngine], ...] = (
    TectonicEngine,
    PdflatexEngine,
)


def available_engines() -> list[EngineStatus]:
    """Status of every real engine. Feeds diagnostics and ``/health/ready``."""
    return [engine_class().status() for engine_class in _REAL_ENGINES]


def select_engine(settings: Settings) -> PdfEngine:
    """Return the engine named by configuration, or probe for one."""
    choice: EngineName = settings.pdf_engine

    if choice == "fake":
        return FakeEngine()
    if choice == "tectonic":
        return _require(TectonicEngine())
    if choice == "pdflatex":
        return _require(PdflatexEngine())

    for engine_class in _REAL_ENGINES:
        engine = engine_class()
        if engine.status().available:
            logger.info("engine.selected", engine=engine.name, mode="auto")
            return engine

    hints = [status.detail for status in available_engines()]
    if settings.environment == "prod":
        raise EngineUnavailableError(
            "no real PDF engine is installed and the fake engine is not permitted "
            "in production",
            hints=hints,
        )

    logger.warning(
        "engine.fallback_to_fake",
        detail="no real PDF engine found; generated PDFs will be blank placeholders",
        hints=hints,
    )
    return FakeEngine()


def _require(engine: PdfEngine) -> PdfEngine:
    status = engine.status()
    if not status.available:
        raise EngineUnavailableError(
            f"PDF engine '{engine.name}' was requested but is not usable: {status.detail}"
        )
    return engine
