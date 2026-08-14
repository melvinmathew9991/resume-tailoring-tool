"""
pdf_compiler.py -- compiles LaTeX source to PDF via pdflatex, and
guarantees a page-count constraint by iterating font size downward
until it fits (or fails loudly and explicitly, rather than silently
shipping a 3-page resume).

This directly addresses the recurring real bug in this project's
history: several hand-built resumes silently compiled to 3 pages
before a human caught it visually. This module makes that check
mandatory and automatic, not something that depends on remembering
to look.
"""
import subprocess
import shutil
import tempfile
from pathlib import Path
from dataclasses import dataclass


class LatexNotFoundError(RuntimeError):
    """Raised when pdflatex is not installed on the host system."""
    pass


class CompilationError(RuntimeError):
    """Raised when pdflatex fails to compile the given source at all
    (a real LaTeX syntax error, not a page-count problem)."""
    def __init__(self, message: str, log_tail: str = ""):
        super().__init__(message)
        self.log_tail = log_tail


@dataclass
class CompileResult:
    pdf_bytes: bytes
    page_count: int
    font_size_used: float
    line_spacing_used: float
    attempts: int
    warning: str = ""


# Font size / line-spacing pairs to try, largest (most readable) first.
# Matches the sizing choices already validated across this project's
# resume history (9.6/11.5 down to 8.8/10.6 as a hard floor -- below
# that, readability suffers more than a slightly longer resume would).
_FONT_LADDER = [
    (9.6, 11.5),
    (9.4, 11.3),
    (9.2, 11.0),
    (9.0, 10.8),
    (8.8, 10.6),
]


def _require_pdflatex():
    if shutil.which("pdflatex") is None:
        raise LatexNotFoundError(
            "pdflatex not found on PATH. Install a LaTeX distribution "
            "(e.g. `sudo apt install texlive-latex-base texlive-latex-extra`, "
            "or MacTeX / MiKTeX on Mac/Windows) before using this tool."
        )
    if shutil.which("pdfinfo") is None:
        raise LatexNotFoundError(
            "pdfinfo not found on PATH (usually part of poppler-utils). "
            "Install it, e.g. `sudo apt install poppler-utils`."
        )


def _compile_once(tex_source: str, workdir: Path) -> tuple:
    """Runs pdflatex once. Returns (pdf_path, log_text). Raises
    CompilationError if pdflatex reports a fatal error or produces no PDF."""
    tex_path = workdir / "resume.tex"
    tex_path.write_text(tex_source, encoding="utf-8")

    proc = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "resume.tex"],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=60,
    )

    log_text = proc.stdout + proc.stderr
    pdf_path = workdir / "resume.pdf"

    if not pdf_path.exists():
        log_tail = "\n".join(log_text.splitlines()[-40:])
        raise CompilationError(
            "pdflatex failed to produce a PDF -- likely a LaTeX syntax error "
            "in the rendered source (check for unescaped special characters).",
            log_tail=log_tail,
        )

    return pdf_path, log_text


def _get_page_count(pdf_path: Path) -> int:
    proc = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    raise RuntimeError(f"Could not determine page count from pdfinfo output: {proc.stdout}")


def compile_with_page_fit(
    render_fn,
    max_pages: int = 2,
    font_ladder: list = None,
) -> CompileResult:
    """Compiles a resume, trying successively smaller fonts from
    `font_ladder` until the result fits within `max_pages`.

    `render_fn(font_size, line_spacing) -> str` must return the fully
    rendered LaTeX source for those parameters (i.e. the caller re-renders
    the Jinja template at each font size, since font size is itself a
    template variable).

    Raises CompilationError if pdflatex fails outright at every attempted
    size. Returns a CompileResult with `warning` set (non-empty) if the
    ladder was exhausted and the SMALLEST font still doesn't fit --
    the caller decides whether to accept that or trim content, but is
    never silently handed a result that violates max_pages without
    being told.
    """
    _require_pdflatex()
    ladder = font_ladder or _FONT_LADDER

    last_result = None
    for attempt, (font_size, line_spacing) in enumerate(ladder, start=1):
        tex_source = render_fn(font_size, line_spacing)

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            pdf_path, log_text = _compile_once(tex_source, workdir)
            page_count = _get_page_count(pdf_path)
            pdf_bytes = pdf_path.read_bytes()

        result = CompileResult(
            pdf_bytes=pdf_bytes,
            page_count=page_count,
            font_size_used=font_size,
            line_spacing_used=line_spacing,
            attempts=attempt,
        )

        if page_count <= max_pages:
            return result

        last_result = result  # keep the smallest-font attempt in case we exhaust the ladder

    # Exhausted the ladder without fitting -- return the smallest-font
    # attempt but flag it loudly rather than pretending it's fine.
    last_result.warning = (
        f"Could not fit within {max_pages} page(s) even at the smallest "
        f"font size ({last_result.font_size_used}pt) -- resulting PDF is "
        f"{last_result.page_count} page(s). Content needs to be trimmed "
        f"(remove a project or shorten bullets), not just shrunk further."
    )
    return last_result
