"""pdflatex engine -- the classic path, for machines with a TeX distribution."""

from __future__ import annotations

from pathlib import Path

from resume_tailor.render.engines.base import SubprocessEngine


class PdflatexEngine(SubprocessEngine):
    name = "pdflatex"
    binary = "pdflatex"

    def build_command(self, tex_path: Path, workdir: Path) -> list[str]:
        del workdir
        return [
            self.binary,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-no-shell-escape",  # never negotiable: \write18 stays off
            "-file-line-error",
            tex_path.name,
        ]

    def missing_binary_hint(self) -> str:
        return (
            "pdflatex is not on PATH. Install a TeX distribution "
            "(Debian/Ubuntu: `apt install texlive-latex-base texlive-latex-extra`; "
            "macOS: `brew install --cask mactex-no-gui`; Windows: MiKTeX), or use "
            "the tectonic engine, which is a single self-contained binary."
        )
