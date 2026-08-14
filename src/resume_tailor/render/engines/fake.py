"""In-memory engine that emits a real, valid, deterministic PDF.

This is what makes the project developable and CI-testable on a machine with no
TeX toolchain. It is not a mock: it returns genuine PDF bytes that ``pypdf``
parses, so the page-fit ladder, the page-count path, the warning guarantee, the
download endpoint and the UI all exercise their real code.

The page count is a deterministic function of source length *and font size*, so
shrinking the font really does fit more content -- meaning the ladder logic is
exercised end to end rather than stubbed out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from resume_tailor.core.errors import CompilationError, CompileTimeoutError
from resume_tailor.render.engines.base import CompiledPdf, EngineStatus

_FONTSIZE_RE = re.compile(r"\\fontsize\{([0-9.]+)\}")

#: Characters that fit on one page at 10pt. Calibrated against the real
#: project bank and template so the fake engine behaves like the real thing:
#: a handful of projects fits inside two pages, a mid-sized selection needs
#: the ladder to step down a rung or two, and selecting everything overflows
#: and trips the warning path. Without that calibration the fake engine would
#: still "work" but would never exercise the branches that matter.
BASE_CHARS_PER_PAGE = 4800.0


def make_pdf(page_count: int, title: str = "resume-tailor fake engine") -> bytes:
    """Build a minimal but structurally valid multi-page PDF."""
    page_count = max(1, page_count)
    objects: list[bytes] = []

    kids = " ".join(f"{index + 3} 0 R" for index in range(page_count))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("ascii"))
    for _ in range(page_count):
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << >> >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R "
        f"/Info << /Title ({title}) >> >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def estimate_pages(tex_source: str, base_chars_per_page: float = BASE_CHARS_PER_PAGE) -> int:
    """Pages a document of this length would take at the font size it declares."""
    match = _FONTSIZE_RE.search(tex_source)
    font_size = float(match.group(1)) if match else 10.0
    if font_size <= 0:  # pragma: no cover - config validation forbids this
        font_size = 10.0
    capacity = base_chars_per_page * (10.0 / font_size) ** 2
    return max(1, math.ceil(len(tex_source) / capacity))


@dataclass
class FakeEngine:
    """Deterministic engine with opt-in failure injection for edge-case tests."""

    name: str = "fake"
    base_chars_per_page: float = BASE_CHARS_PER_PAGE
    fail_if_source_contains: str | None = None
    """Raise :class:`CompilationError` when the source contains this substring.
    Used to test the "LaTeX error, not a page-count problem" branch."""
    timeout_if_source_contains: str | None = None
    fixed_page_count: int | None = None
    emit_invalid_pdf: bool = False
    compile_calls: list[str] = field(default_factory=list)

    def status(self) -> EngineStatus:
        return EngineStatus(
            name=self.name,
            available=True,
            detail="in-process engine; produces structurally valid but blank PDFs",
            version="fake-1",
        )

    def compile(self, tex_source: str, *, timeout_s: float) -> CompiledPdf:
        del timeout_s
        self.compile_calls.append(tex_source)

        if self.timeout_if_source_contains and self.timeout_if_source_contains in tex_source:
            raise CompileTimeoutError("fake engine: simulated timeout", log_tail="simulated")
        if self.fail_if_source_contains and self.fail_if_source_contains in tex_source:
            raise CompilationError(
                "fake engine: simulated LaTeX error",
                log_tail="! Undefined control sequence.",
            )
        if self.emit_invalid_pdf:
            raise CompilationError("fake engine: produced a file that is not a PDF")

        pages = self.fixed_page_count or estimate_pages(tex_source, self.base_chars_per_page)
        return CompiledPdf(pdf_bytes=make_pdf(pages), log="fake engine ok", engine=self.name)
