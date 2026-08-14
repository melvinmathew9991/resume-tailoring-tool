"""PDF engines.

The abstraction exists for one concrete reason: the machine this project is
developed on has no LaTeX toolchain, and a test suite that cannot run is a test
suite that does not protect anything. :class:`~.fake.FakeEngine` makes the
entire page-fit ladder, the warning path and every API contract testable with
no external binary at all, while :class:`~.tectonic.TectonicEngine` and
:class:`~.pdflatex.PdflatexEngine` produce real documents.
"""

from resume_tailor.render.engines.base import (
    CompiledPdf,
    EngineStatus,
    PdfEngine,
)
from resume_tailor.render.engines.fake import FakeEngine
from resume_tailor.render.engines.pdflatex import PdflatexEngine
from resume_tailor.render.engines.registry import available_engines, select_engine
from resume_tailor.render.engines.tectonic import TectonicEngine

__all__ = [
    "CompiledPdf",
    "EngineStatus",
    "FakeEngine",
    "PdfEngine",
    "PdflatexEngine",
    "TectonicEngine",
    "available_engines",
    "select_engine",
]
