"""Tectonic engine -- the default.

Tectonic is a single self-contained binary that fetches only the TeX packages a
document actually needs and caches them. That matters here for a specific
reason: it is installable on the development machine in seconds, where a full
TeX distribution is a multi-gigabyte install that this project's author does
not currently have. Being able to produce a real PDF locally is the difference
between a tool and a demo.

``--untrusted`` is passed unconditionally. It disables shell escape and the
other features that make compiling an arbitrary document risky. It is also why
this engine can safely decline the home-directory redirect: see
``sandbox_home`` below.
"""

from __future__ import annotations

from pathlib import Path

from resume_tailor.render.engines.base import SubprocessEngine


class TectonicEngine(SubprocessEngine):
    name = "tectonic"
    binary = "tectonic"

    #: Tectonic resolves its downloaded-package cache through the platform's
    #: standard directories -- ``USERPROFILE`` on Windows, ``HOME`` elsewhere.
    #: Pointing those at a per-compile scratch directory does not contain it
    #: (its sandboxing is ``--untrusted``, and it does not read TeX user
    #: configuration at all); it makes it exit 1 with "Unable to find standard
    #: directories for platform" before typesetting anything, and on the
    #: platforms where it would survive, it would re-download the whole
    #: package bundle on every single compile.
    sandbox_home = False

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
            "tectonic is not on PATH. Install it with `brew install tectonic` "
            "(macOS), `cargo install tectonic` (anywhere with Rust), or on "
            "Windows download the release binary from "
            "https://github.com/tectonic-typesetting/tectonic/releases and put "
            "it on PATH -- it is not published to winget. "
            "It is one binary and needs no TeX distribution."
        )
