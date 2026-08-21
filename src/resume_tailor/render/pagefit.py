"""The page-fit guarantee.

The core safety property of this whole tool, stated precisely:

    For every generated resume, either ``page_count <= max_pages``, or
    ``warning`` is non-empty. Never neither.

That invariant exists because of a real failure in this project's history: a
resume that silently compiled to three pages and shipped before a human noticed
visually. Automating "try progressively smaller, verify the real page count,
stop when it fits, and shout if it never does" was the fix, and it is preserved
here exactly.

What changed from the original implementation
---------------------------------------------
* Page counting uses ``pypdf`` in-process instead of shelling out to
  ``pdfinfo``. That removes a second external binary, and with it the branch
  where a failed ``pdfinfo`` fell through to a confusing ``RuntimeError``
  (defect B9).
* An empty font ladder used to leave ``last_result`` as ``None`` and then
  crash on ``None.warning``. The ladder is now validated up front.
* A compile failure on the first rung aborts immediately rather than retrying
  four more times: a LaTeX syntax error is not a function of font size, so
  retrying only multiplies the wait before the same error.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from resume_tailor.core.errors import InvalidInputError, PageCountError
from resume_tailor.core.logging import get_logger
from resume_tailor.render.engines.base import PdfEngine

logger = get_logger(__name__)

#: ``render_fn(font_size, line_spacing) -> tex_source``. Font size is itself a
#: template variable, so each rung of the ladder requires a fresh render.
RenderFn = Callable[[float, float], str]


@dataclass(frozen=True)
class FitResult:
    pdf_bytes: bytes
    page_count: int
    font_size: float
    line_spacing: float
    attempts: int
    engine: str
    warning: str = ""
    source_warnings: tuple[str, ...] = ()

    @property
    def fits(self) -> bool:
        return not self.warning


def count_pages(pdf_bytes: bytes) -> int:
    """Page count, read in-process. No external binary, no subprocess."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        count = len(reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        raise PageCountError(f"could not read the generated PDF: {exc}") from exc
    if count < 1:
        raise PageCountError("the generated PDF reports zero pages")
    return count


def compile_with_page_fit(
    render_fn: RenderFn,
    engine: PdfEngine,
    *,
    max_pages: int,
    font_ladder: Sequence[tuple[float, float]],
    timeout_s: float,
) -> FitResult:
    """Compile at successively smaller font sizes until the page limit is met.

    Returns the first result that fits. If the ladder is exhausted, returns the
    smallest-font attempt with ``warning`` set -- the caller still gets a PDF to
    look at while deciding what to trim, but is never handed an over-length
    resume without being told.
    """
    if not font_ladder:
        raise InvalidInputError("font ladder is empty; there is nothing to try")
    if max_pages < 1:
        raise InvalidInputError(f"max_pages must be at least 1, got {max_pages}")

    last: FitResult | None = None

    for attempt, (font_size, line_spacing) in enumerate(font_ladder, start=1):
        tex_source = render_fn(font_size, line_spacing)
        compiled = engine.compile(tex_source, timeout_s=timeout_s)
        page_count = count_pages(compiled.pdf_bytes)

        last = FitResult(
            pdf_bytes=compiled.pdf_bytes,
            page_count=page_count,
            font_size=font_size,
            line_spacing=line_spacing,
            attempts=attempt,
            engine=compiled.engine,
        )

        logger.debug(
            "pagefit.attempt",
            attempt=attempt,
            font_size=font_size,
            pages=page_count,
            max_pages=max_pages,
        )

        if page_count <= max_pages:
            return last

    assert last is not None  # guaranteed: the ladder was checked non-empty above
    return FitResult(
        pdf_bytes=last.pdf_bytes,
        page_count=last.page_count,
        font_size=last.font_size,
        line_spacing=last.line_spacing,
        attempts=last.attempts,
        engine=last.engine,
        warning=_overflow_warning(max_pages, last, shrank=len(font_ladder) > 1),
    )


def _overflow_warning(max_pages: int, last: FitResult, *, shrank: bool) -> str:
    """Say what was actually tried.

    With a multi-rung ladder the engine really did shrink the type and still
    failed, so "even at the smallest font size" is the honest summary. With a
    single fixed rung the format is pinned by design and nothing was shrunk;
    claiming otherwise sends the reader looking for a font setting to loosen
    instead of at the content, which is the only thing that can change.
    """
    tried = (
        f"even at the smallest font size ({last.font_size:g}pt)"
        if shrank
        else f"at the fixed {last.font_size:g}pt format"
    )
    return (
        f"Could not fit within {max_pages} page(s) {tried} -- the PDF below is "
        f"{last.page_count} page(s). Content needs to be trimmed (remove a project "
        "or shorten bullets), not just shrunk further."
    )
