"""In-memory engine that emits a real, valid, deterministic PDF.

This is what makes the project developable and CI-testable on a machine with no
TeX toolchain. It is not a mock: it returns genuine PDF bytes that ``pypdf``
parses, so the page-fit ladder, the page-count path, the warning guarantee, the
download endpoint and the UI all exercise their real code.

The page count is a deterministic function of source length *and font size*, so
shrinking the font really does fit more content -- meaning the ladder logic is
exercised end to end rather than stubbed out.

The pages also carry the document's actual words, as selectable text in a
base-14 font, plus a link annotation for every ``\\href`` in the source. That is
not decoration. :mod:`resume_tailor.render.parsecheck` answers "would an ATS
read this?" by extracting text and links back out of the compiled PDF, and an
engine that emitted blank pages would make that check fail identically on a
perfect document and a broken one -- which is to say, make it untestable
anywhere without a TeX toolchain. What this engine does *not* model is
typesetting: no line breaking, no hyphenation, no kerning. So a clean result
here proves the check's plumbing, and only a real engine proves the layout.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from resume_tailor.core.errors import CompilationError, CompileTimeoutError
from resume_tailor.domain.latex import latex_to_display_text
from resume_tailor.render.engines.base import CompiledPdf, EngineStatus

_FONTSIZE_RE = re.compile(r"\\fontsize\{([0-9.]+)\}")

#: Characters that fit on one page at 10pt. Calibrated against the real
#: project bank and template so the fake engine behaves like the real thing:
#: a handful of projects fits inside two pages, a mid-sized selection needs
#: the ladder to step down a rung or two, and selecting everything overflows
#: and trips the warning path. Without that calibration the fake engine would
#: still "work" but would never exercise the branches that matter.
BASE_CHARS_PER_PAGE = 4800.0

# -- turning LaTeX source back into the words on the page --------------------

_BODY_RE = re.compile(r"\\begin\{document\}(.*)\\end\{document\}", re.DOTALL)
#: ``\href{target}{label}``. The label may hold one level of nesting, which is
#: as deep as ``renderer._href`` ever builds.
_HREF_RE = re.compile(r"\\href\{([^{}]*)\}\{((?:[^{}]|\{[^{}]*\})*)\}")
_SECTION_RE = re.compile(r"\\section\*?\{([^{}]*)\}")
_LINEBREAK_RE = re.compile(r"\\\\(?:\[[^\]]*\])?")
_ENVIRONMENT_RE = re.compile(r"\\(?:begin|end)\{[^{}]*\}")
_COMMAND_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?")
_BRACE_RE = re.compile(r"[{}]")
_SPACE_RE = re.compile(r"[ \t]+")


def document_links(tex_source: str) -> list[str]:
    r"""Every ``\href`` target in the source, in order, de-duplicated."""
    return list(dict.fromkeys(match.group(1) for match in _HREF_RE.finditer(tex_source)))


def document_lines(tex_source: str) -> list[str]:
    r"""The source reduced to the lines of text a reader would see.

    Deliberately crude -- it drops markup rather than interpreting it, and a
    stray ``\fontsize`` argument survives as a number on the page. That is
    acceptable because the parse check only ever asks whether expected text is
    *missing*; text this leaves behind costs nothing, whereas text it wrongly
    dropped would show up as a phantom extraction failure.
    """
    match = _BODY_RE.search(tex_source)
    body = match.group(1) if match else tex_source

    body = _HREF_RE.sub(lambda m: m.group(2), body)
    body = _SECTION_RE.sub(lambda m: m.group(1), body)
    body = _LINEBREAK_RE.sub("\n", body)
    body = _ENVIRONMENT_RE.sub(" ", body)
    body = latex_to_display_text(body)
    body = _COMMAND_RE.sub(" ", body)
    body = _BRACE_RE.sub(" ", body)

    lines = (_SPACE_RE.sub(" ", line).strip() for line in body.splitlines())
    return [line for line in lines if line]


# -- PDF assembly ------------------------------------------------------------

_PAGE_WIDTH = 595
_PAGE_HEIGHT = 842
_MARGIN = 40
_FONT_SIZE = 9.0
_MAX_LEADING = 11.0
_MIN_LEADING = 1.0


def _escape_pdf_string(line: str) -> bytes:
    """Encode one line as a PDF literal string.

    ``latin-1`` with replacement, to match the ``/WinAnsiEncoding`` declared on
    the font: a character the encoding cannot express must become a visible
    ``?`` rather than a byte the extractor would decode as something else.
    """
    escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("latin-1", errors="replace")


def _content_stream(lines: list[str]) -> bytes:
    if not lines:
        return b""
    usable = _PAGE_HEIGHT - 2 * _MARGIN
    leading = max(_MIN_LEADING, min(_MAX_LEADING, usable / len(lines)))
    top = _PAGE_HEIGHT - _MARGIN
    parts = [
        f"BT /F1 {_FONT_SIZE:g} Tf {leading:.2f} TL 1 0 0 1 {_MARGIN} {top:.2f} Tm\n".encode(
            "ascii"
        )
    ]
    for line in lines:
        parts.append(b"(" + _escape_pdf_string(line) + b") Tj T*\n")
    parts.append(b"ET\n")
    return b"".join(parts)


def _split_evenly(lines: list[str], buckets: int) -> list[list[str]]:
    """Spread lines across pages so no page is empty while text remains."""
    if not lines:
        return [[] for _ in range(buckets)]
    per_page = math.ceil(len(lines) / buckets)
    return [lines[index * per_page : (index + 1) * per_page] for index in range(buckets)]


def make_pdf(
    page_count: int,
    title: str = "resume-tailor fake engine",
    *,
    lines: list[str] | None = None,
    links: list[str] | None = None,
) -> bytes:
    """Build a minimal but structurally valid multi-page PDF.

    ``lines`` are laid out as extractable text, spread over the pages; ``links``
    become URI link annotations on the first page. Both default to empty, so an
    existing caller that only wants N blank pages still gets exactly that.
    """
    page_count = max(1, page_count)
    chunks = _split_evenly(list(lines or []), page_count)
    link_targets = list(links or [])

    # Object numbers are assigned before any object is serialised, because the
    # page tree has to name its children and each page has to name its content
    # stream. 1 catalog, 2 page tree, 3 font, then the pages.
    next_number = 4
    layout: list[tuple[int, int, list[int]]] = []
    for index in range(page_count):
        page_number = next_number
        content_number = next_number + 1
        next_number += 2
        annotation_numbers: list[int] = []
        if index == 0:
            annotation_numbers = list(range(next_number, next_number + len(link_targets)))
            next_number += len(link_targets)
        layout.append((page_number, content_number, annotation_numbers))

    bodies: dict[int, bytes] = {}
    kids = " ".join(f"{page_number} 0 R" for page_number, _, _ in layout)
    bodies[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    bodies[2] = f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("ascii")
    bodies[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"

    for (page_number, content_number, annotation_numbers), chunk in zip(
        layout, chunks, strict=True
    ):
        annots = (
            f" /Annots [{' '.join(f'{number} 0 R' for number in annotation_numbers)}]"
            if annotation_numbers
            else ""
        )
        bodies[page_number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_number} 0 R{annots} >>"
        ).encode("ascii")

        stream = _content_stream(chunk)
        bodies[content_number] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream"
        )

        for offset, number in enumerate(annotation_numbers):
            bottom = _PAGE_HEIGHT - _MARGIN - (offset + 1) * 12
            bodies[number] = (
                f"<< /Type /Annot /Subtype /Link /Border [0 0 0] "
                f"/Rect [{_MARGIN} {bottom} {_PAGE_WIDTH - _MARGIN} {bottom + 10}] "
                f"/A << /Type /Action /S /URI /URI (".encode("ascii")
                + _escape_pdf_string(link_targets[offset])
                + b") >> >>"
            )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number in range(1, len(bodies) + 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + bodies[number] + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R "
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
    emit_blank_pages: bool = False
    """Emit pages with no text at all, as a scanned or glyph-less PDF would.
    The failure branch of the parse check has to come from somewhere, and a
    real engine cannot be asked to produce a broken document on demand."""
    compile_calls: list[str] = field(default_factory=list)

    def status(self) -> EngineStatus:
        return EngineStatus(
            name=self.name,
            available=True,
            detail="in-process engine; emits the document's text, but does not typeset it",
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
        pdf_bytes = make_pdf(
            pages,
            lines=None if self.emit_blank_pages else document_lines(tex_source),
            links=None if self.emit_blank_pages else document_links(tex_source),
        )
        return CompiledPdf(pdf_bytes=pdf_bytes, log="fake engine ok", engine=self.name)
