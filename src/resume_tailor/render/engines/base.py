"""The PDF engine contract, and the shared subprocess machinery.

Every real engine is "write a .tex file into a scratch directory, run a binary,
pick up the .pdf". The differences are the command line and the failure
messages, so that is all a subclass provides.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from resume_tailor.core.errors import CompilationError, CompileTimeoutError
from resume_tailor.core.logging import get_logger

logger = get_logger(__name__)

#: Refuse to hand back anything implausibly large; a runaway document should
#: fail rather than exhaust memory on the way to the client.
MAX_PDF_BYTES = 32 * 1024 * 1024

LOG_TAIL_LINES = 40


@functools.lru_cache(maxsize=8)
def _probe_binary_version(path: str) -> str:
    """``<binary> --version``, cached on the resolved path.

    ``status()`` is not a rare call: ``/health/ready`` reaches it on every
    request, and both container images run a ``HEALTHCHECK`` against that
    endpoint every 30 seconds. Uncached, each one spawned a subprocess --
    roughly 50 ms of process creation, forever, to re-learn a string that only
    changes when the toolchain is reinstalled.

    Cached on the path rather than on the engine instance, so that a caller
    which builds a fresh engine (``registry.available_engines`` does) still
    hits, while a binary installed at a different location is still probed.
    """
    try:
        proc = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment specific
        return ""
    return (
        (proc.stdout or proc.stderr).strip().splitlines()[0] if proc.stdout or proc.stderr else ""
    )


@dataclass(frozen=True)
class EngineStatus:
    """Whether an engine can actually run right now. Drives ``/health/ready``."""

    name: str
    available: bool
    detail: str = ""
    version: str = ""


@dataclass(frozen=True)
class CompiledPdf:
    pdf_bytes: bytes
    log: str
    engine: str


@runtime_checkable
class PdfEngine(Protocol):
    """Anything that can turn LaTeX source into PDF bytes."""

    name: str

    def status(self) -> EngineStatus: ...

    def compile(self, tex_source: str, *, timeout_s: float) -> CompiledPdf: ...


class SubprocessEngine(ABC):
    """Base for engines that shell out to a binary."""

    name: str = "subprocess"
    binary: str = ""

    #: Whether to point ``HOME``/``USERPROFILE`` at the scratch directory.
    #:
    #: True for TeX distributions, which read user configuration out of the
    #: home directory and must not be allowed to. False for engines that
    #: resolve their own package cache through the platform's standard
    #: directories -- redirecting the home directory does not sandbox those,
    #: it just breaks them (see ``TectonicEngine``).
    sandbox_home: bool = True

    @abstractmethod
    def build_command(self, tex_path: Path, workdir: Path) -> list[str]:
        """Argv for compiling ``tex_path`` inside ``workdir``."""

    @abstractmethod
    def missing_binary_hint(self) -> str:
        """Installation guidance shown when the binary is not on PATH."""

    def resolve_binary(self) -> str | None:
        return shutil.which(self.binary)

    def status(self) -> EngineStatus:
        path = self.resolve_binary()
        if path is None:
            return EngineStatus(self.name, False, detail=self.missing_binary_hint())
        return EngineStatus(self.name, True, detail=path, version=self._probe_version(path))

    def _probe_version(self, path: str) -> str:
        return _probe_binary_version(path)

    def _subprocess_env(self, workdir: Path) -> dict[str, str]:
        """A deliberately boring environment.

        ``TEXMF*`` are redirected into the scratch directory so a compile
        cannot read or write the user's TeX configuration, ``openin_any``/
        ``openout_any`` stop the document itself from touching anything
        outside the working directory, and ``SOURCE_DATE_EPOCH`` is pinned so
        the same source produces byte-identical output -- which is what makes
        golden-file tests on generated PDFs possible at all.

        ``HOME``/``USERPROFILE`` are redirected only when ``sandbox_home`` is
        set. They are defence in depth on top of the ``TEXMF*`` redirect, not
        the primary control, so an engine that needs a real home directory can
        decline them without giving up the containment above.
        """
        env = dict(os.environ)
        env.update(
            {
                "TEXMFHOME": str(workdir / "texmf"),
                "TEXMFVAR": str(workdir / "texmf-var"),
                "TEXMFCONFIG": str(workdir / "texmf-config"),
                "SOURCE_DATE_EPOCH": "0",
                "FORCE_SOURCE_DATE": "1",
                "openout_any": "p",  # paranoid: no writing outside the cwd
                "openin_any": "p",
            }
        )
        if self.sandbox_home:
            env["HOME"] = str(workdir)
            env["USERPROFILE"] = str(workdir)
        return env

    def compile(self, tex_source: str, *, timeout_s: float) -> CompiledPdf:
        with tempfile.TemporaryDirectory(prefix="resume-tailor-") as tmp:
            workdir = Path(tmp)
            tex_path = workdir / "resume.tex"
            tex_path.write_text(tex_source, encoding="utf-8")

            command = self.build_command(tex_path, workdir)
            try:
                proc = subprocess.run(
                    command,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout_s,
                    env=self._subprocess_env(workdir),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                # The original let this escape as an unhandled 500 (defect B10).
                # A hang is a *different* failure from a syntax error: it means
                # pathological or hostile input, and it deserves its own code.
                raise CompileTimeoutError(
                    f"{self.name} did not finish within {timeout_s:g}s and was terminated. "
                    "This usually means the input contains a pathological LaTeX construct.",
                    log_tail=_tail(str(exc.output or "")),
                ) from exc
            except OSError as exc:
                raise CompilationError(f"could not run {self.name}: {exc}", log_tail="") from exc

            log_text = (proc.stdout or "") + (proc.stderr or "")
            pdf_path = workdir / "resume.pdf"

            if not pdf_path.exists():
                raise CompilationError(
                    f"{self.name} produced no PDF (exit code {proc.returncode}). This is "
                    "almost always a LaTeX syntax error in the rendered source -- check "
                    "the log tail for the first line starting with '!'.",
                    log_tail=_tail(log_text),
                    exit_code=proc.returncode,
                )

            # Size checked from the directory entry, before the read. Checking
            # after `read_bytes()` -- which is what this did -- means a runaway
            # document is already resident in memory by the time it is refused,
            # so the limit protected nothing it was written to protect.
            size = pdf_path.stat().st_size
            if size > MAX_PDF_BYTES:
                raise CompilationError(
                    f"generated PDF is {size / 1e6:.1f} MB, over the "
                    f"{MAX_PDF_BYTES / 1e6:.0f} MB limit",
                    log_tail=_tail(log_text),
                )

            pdf_bytes = pdf_path.read_bytes()

        if not pdf_bytes.startswith(b"%PDF"):
            raise CompilationError(
                f"{self.name} produced a file that is not a PDF", log_tail=_tail(log_text)
            )
        logger.debug("engine.compiled", engine=self.name, bytes=len(pdf_bytes))
        return CompiledPdf(pdf_bytes=pdf_bytes, log=log_text, engine=self.name)


def _tail(text: str, lines: int = LOG_TAIL_LINES) -> str:
    return "\n".join(text.splitlines()[-lines:])
