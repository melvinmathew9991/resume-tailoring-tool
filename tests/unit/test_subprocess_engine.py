"""Subprocess engine error mapping, tested without a LaTeX toolchain.

``SubprocessEngine`` is where "the compiler misbehaved" becomes a typed error,
and it is exactly where the original leaked raw exceptions: an uncaught
``TimeoutExpired`` surfaced as a 500 (defect B10), and a failed page-count
subprocess fell through to a confusing ``RuntimeError`` (defect B9).

Those paths are exercised here by pointing the engine at ``python`` with a
one-line script instead of at ``pdflatex`` -- the interesting behaviour is the
error handling, not the typesetting.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from resume_tailor.core.errors import CompilationError, CompileTimeoutError
from resume_tailor.render.engines.base import MAX_PDF_BYTES, SubprocessEngine

pytestmark = pytest.mark.unit


class ScriptedEngine(SubprocessEngine):
    """Runs a Python snippet in place of a TeX binary."""

    name = "scripted"
    binary = sys.executable

    def __init__(self, script: str) -> None:
        self._script = script

    def build_command(self, tex_path: Path, workdir: Path) -> list[str]:
        del tex_path, workdir
        return [sys.executable, "-c", self._script]

    def missing_binary_hint(self) -> str:  # pragma: no cover - never missing
        return "python is always present"

    def resolve_binary(self) -> str | None:
        return sys.executable


WRITE_PDF = "open('resume.pdf','wb').write(b'%PDF-1.4 fake body')"


class TestSuccessPath:
    def test_returns_the_produced_pdf(self) -> None:
        compiled = ScriptedEngine(WRITE_PDF).compile("source", timeout_s=30)
        assert compiled.pdf_bytes.startswith(b"%PDF")
        assert compiled.engine == "scripted"

    def test_source_is_written_where_the_command_runs(self) -> None:
        script = (
            "import pathlib;"
            "assert pathlib.Path('resume.tex').read_text(encoding='utf-8') == 'MARKER';" + WRITE_PDF
        )
        assert ScriptedEngine(script).compile("MARKER", timeout_s=30).pdf_bytes


class TestFailurePaths:
    def test_no_pdf_produced_is_a_compilation_error(self) -> None:
        with pytest.raises(CompilationError, match="produced no PDF") as info:
            ScriptedEngine("print('I did nothing')").compile("source", timeout_s=30)
        assert info.value.status_code == 422

    def test_exit_code_is_reported(self) -> None:
        with pytest.raises(CompilationError) as info:
            ScriptedEngine("import sys; sys.exit(3)").compile("source", timeout_s=30)
        assert info.value.context["exit_code"] == 3

    def test_log_tail_is_captured(self) -> None:
        script = "print('! Undefined control sequence.')"
        with pytest.raises(CompilationError) as info:
            ScriptedEngine(script).compile("source", timeout_s=30)
        assert "Undefined control sequence" in info.value.log_tail

    def test_timeout_maps_to_its_own_error_type(self) -> None:
        """A hang means pathological input, not a bad template, so it gets its
        own code and a 504 rather than being lumped in with syntax errors."""
        with pytest.raises(CompileTimeoutError) as info:
            ScriptedEngine("import time; time.sleep(30)").compile("source", timeout_s=0.5)
        assert info.value.status_code == 504

    def test_non_pdf_output_is_rejected(self) -> None:
        script = "open('resume.pdf','wb').write(b'this is not a pdf')"
        with pytest.raises(CompilationError, match="not a PDF"):
            ScriptedEngine(script).compile("source", timeout_s=30)

    def test_oversized_output_is_rejected(self) -> None:
        script = f"open('resume.pdf','wb').write(b'%PDF' + b'x' * {MAX_PDF_BYTES + 1})"
        with pytest.raises(CompilationError, match="over the"):
            ScriptedEngine(script).compile("source", timeout_s=60)

    def test_unrunnable_binary_is_a_compilation_error(self) -> None:
        class MissingEngine(ScriptedEngine):
            def build_command(self, tex_path: Path, workdir: Path) -> list[str]:
                del tex_path, workdir
                return ["definitely-not-a-real-binary-xyz"]

        with pytest.raises(CompilationError, match="could not run"):
            MissingEngine("").compile("source", timeout_s=5)


class TestSandboxing:
    def test_environment_is_redirected_into_the_scratch_directory(self, tmp_path: Path) -> None:
        """A compile must not be able to read or write the user's TeX config."""
        environment = ScriptedEngine("")._subprocess_env(tmp_path)
        assert environment["HOME"] == str(tmp_path)
        assert environment["TEXMFHOME"].startswith(str(tmp_path))
        assert environment["openout_any"] == "p"

    def test_source_date_epoch_is_pinned_for_reproducibility(self, tmp_path: Path) -> None:
        assert ScriptedEngine("")._subprocess_env(tmp_path)["SOURCE_DATE_EPOCH"] == "0"

    def test_containment_survives_opting_out_of_the_home_redirect(self, tmp_path: Path) -> None:
        """``sandbox_home = False`` must give up the home redirect and nothing else.

        The redirect is defence in depth on top of the TEXMF redirect and the
        kpathsea ``*_any`` limits. An engine that declines it still must not be
        able to reach the user's TeX tree.
        """

        class OpenHomeEngine(ScriptedEngine):
            sandbox_home = False

        environment = OpenHomeEngine("")._subprocess_env(tmp_path)
        assert "HOME" not in environment or environment["HOME"] != str(tmp_path)
        assert environment["TEXMFHOME"].startswith(str(tmp_path))
        assert environment["TEXMFVAR"].startswith(str(tmp_path))
        assert environment["TEXMFCONFIG"].startswith(str(tmp_path))
        assert environment["openin_any"] == "p"
        assert environment["openout_any"] == "p"

    def test_scratch_directory_is_cleaned_up(self) -> None:
        script = "import os, pathlib; " + WRITE_PDF + "; print(os.getcwd())"
        compiled = ScriptedEngine(script).compile("source", timeout_s=30)
        workdir = Path(compiled.log.strip().splitlines()[-1])
        assert not workdir.exists(), "the temporary compile directory must not survive"


class TestStatus:
    def test_available_when_the_binary_resolves(self) -> None:
        status = ScriptedEngine("").status()
        assert status.available is True
        assert status.name == "scripted"

    def test_unavailable_when_the_binary_is_missing(self) -> None:
        class MissingEngine(ScriptedEngine):
            def resolve_binary(self) -> str | None:
                return None

            def missing_binary_hint(self) -> str:
                return "install the thing"

        status = MissingEngine("").status()
        assert status.available is False
        assert status.detail == "install the thing"


class TestOversizedOutputIsNotReadIntoMemory:
    def test_the_size_check_happens_before_the_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The limit has to be applied to the directory entry, not to the bytes.

        Checking ``len(pdf_bytes) > MAX_PDF_BYTES`` after ``read_bytes()`` -- what
        this used to do -- means the runaway document is already resident by the
        time it is refused, so the ceiling protected nothing it was written to
        protect. Reading is sabotaged here: if the engine still reaches it, the
        test fails with the sabotage rather than the size error.
        """

        def explode(self: Path) -> bytes:
            raise AssertionError("read_bytes() was called on an over-limit PDF")

        monkeypatch.setattr(Path, "read_bytes", explode)
        script = f"open('resume.pdf','wb').write(b'%PDF' + b'x' * {MAX_PDF_BYTES + 1})"
        with pytest.raises(CompilationError, match="over the"):
            ScriptedEngine(script).compile("source", timeout_s=60)


class TestVersionProbeIsCached:
    def test_repeated_status_calls_spawn_one_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``/health/ready`` reaches ``status()`` on every request, and both
        container images poll it every 30 seconds. Uncached, each one spawned a
        ``--version`` subprocess forever."""
        from resume_tailor.render.engines import base

        base._probe_binary_version.cache_clear()
        calls = 0
        real = base.subprocess.run

        def counting_run(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(base.subprocess, "run", counting_run)
        engine = ScriptedEngine("")
        for _ in range(5):
            engine.status()
        assert calls == 1

    def test_a_different_path_is_probed_separately(self) -> None:
        """Cached on the path, so a binary installed elsewhere is still probed."""
        from resume_tailor.render.engines import base

        base._probe_binary_version.cache_clear()
        base._probe_binary_version(sys.executable)
        info = base._probe_binary_version.cache_info()
        assert info.misses == 1
        base._probe_binary_version(sys.executable)
        assert base._probe_binary_version.cache_info().hits == 1
