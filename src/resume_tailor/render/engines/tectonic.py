"""Tectonic engine -- the default.

Tectonic is a single self-contained binary that fetches only the TeX packages a
document actually needs and caches them. That matters here for a specific
reason: it is installable on the development machine in seconds, where a full
TeX distribution is a multi-gigabyte install that this project's author does
not currently have. Being able to produce a real PDF locally is the difference
between a tool and a demo.

``--untrusted`` is passed unconditionally. It disables shell escape and the
other features that make compiling an arbitrary document risky.
"""

from __future__ import annotations

from pathlib import Path

from resume_tailor.render.engines.base import SubprocessEngine


class TectonicEngine(SubprocessEngine):
    name = "tectonic"
    binary = "tectonic"

    def build_command(self, tex_path: Path, workdir: Path) -> list[str]:
        return [
            self.binary,
            "--untrusted",
            "--chatter",
            "minimal",
            "--outdir",
            str(workdir),
            tex_path.name,
        ]

    def missing_binary_hint(self) -> str:
        return (
            "tectonic is not on PATH. Install it with "
            "`winget install TectonicProject.Tectonic` (Windows), "
            "`brew install tectonic` (macOS), or `cargo install tectonic`. "
            "It is one binary and needs no TeX distribution."
        )
