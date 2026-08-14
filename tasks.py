#!/usr/bin/env python
"""Cross-platform task runner.

Replaces the old ``run.sh``, which was bash-only and therefore unusable on the
machine this project is actually developed on (defect S5). Pure stdlib, so it
works before anything is installed.

    python tasks.py setup      # install the package + dev extras (editable)
    python tasks.py test       # fast suite -- no LaTeX toolchain required
    python tasks.py test-all   # everything, including real-compile tests
    python tasks.py cov        # fast suite with coverage gate
    python tasks.py lint       # ruff
    python tasks.py fmt        # ruff --fix + format
    python tasks.py types      # mypy
    python tasks.py check      # lint + types + cov  (what CI runs)
    python tasks.py api        # run the FastAPI backend
    python tasks.py ui         # run the Streamlit frontend
    python tasks.py dev        # run both, together
    python tasks.py doctor     # report what is installed and what is missing
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def run(*args: str, check: bool = True, **kwargs: object) -> int:
    print(f"\n$ {' '.join(args)}", flush=True)
    proc = subprocess.run(args, cwd=ROOT, **kwargs)  # type: ignore[call-overload]
    if check and proc.returncode != 0:
        sys.exit(proc.returncode)
    return int(proc.returncode)


def task_setup() -> None:
    run(PY, "-m", "pip", "install", "--upgrade", "pip")
    run(PY, "-m", "pip", "install", "-e", ".[dev,ui]")
    print("\nInstalled. Next: `python tasks.py doctor`, then `python tasks.py test`.")


def task_test() -> None:
    run(PY, "-m", "pytest", "-m", "not integration and not latex and not slow", "-q")


def task_test_all() -> None:
    run(PY, "-m", "pytest", "-q")


def task_cov() -> None:
    run(
        PY,
        "-m",
        "pytest",
        "-m",
        "not integration and not latex and not slow",
        "--cov=resume_tailor",
        "--cov-report=term-missing",
        "--cov-report=xml",
    )


def task_lint() -> None:
    run(PY, "-m", "ruff", "check", ".")
    run(PY, "-m", "ruff", "format", "--check", ".")


def task_fmt() -> None:
    run(PY, "-m", "ruff", "check", "--fix", ".")
    run(PY, "-m", "ruff", "format", ".")


def task_types() -> None:
    run(PY, "-m", "mypy")


def task_check() -> None:
    task_lint()
    task_types()
    task_cov()


def task_api() -> None:
    run(PY, "-m", "uvicorn", "resume_tailor.api.main:app", "--reload", "--port", "8000")


def task_ui() -> None:
    env = dict(os.environ)
    env.setdefault("RT_UI_MODE", "http")
    run(PY, "-m", "streamlit", "run", str(ROOT / "ui" / "app.py"), env=env)


def task_dev() -> None:
    """Backend and frontend together; Ctrl+C stops both."""
    api = subprocess.Popen(
        [PY, "-m", "uvicorn", "resume_tailor.api.main:app", "--port", "8000"], cwd=ROOT
    )
    time.sleep(2.0)
    env = dict(os.environ, RT_UI_MODE="http")
    ui = subprocess.Popen(
        [PY, "-m", "streamlit", "run", str(ROOT / "ui" / "app.py")], cwd=ROOT, env=env
    )
    print("\nAPI  -> http://127.0.0.1:8000/docs\nUI   -> http://localhost:8501\nCtrl+C to stop.")
    try:
        ui.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (ui, api):
            proc.terminate()


def task_doctor() -> None:
    print(f"python           : {sys.version.split()[0]}  ({PY})")
    for module in (
        "fastapi",
        "pydantic",
        "streamlit",
        "pypdf",
        "structlog",
        "pytest",
        "hypothesis",
    ):
        try:
            __import__(module)
            print(f"{module:17}: installed")
        except ImportError:
            print(f"{module:17}: MISSING  -> run `python tasks.py setup`")

    print("\nPDF engines:")
    found = False
    for binary in ("tectonic", "pdflatex", "xelatex"):
        path = shutil.which(binary)
        print(f"  {binary:13}: {path or 'not on PATH'}")
        found = found or bool(path)
    if not found:
        print(
            "\n  No real engine found. The fake engine still lets the full test\n"
            "  suite run. To produce actual PDFs, install Tectonic (a single\n"
            "  self-contained binary -- no TeX distribution needed):\n"
            "    winget install TectonicProject.Tectonic\n"
            "    brew install tectonic\n"
            "    cargo install tectonic\n"
            "  ...or run `docker compose -f docker/docker-compose.yml up`."
        )


TASKS = {
    "setup": task_setup,
    "test": task_test,
    "test-all": task_test_all,
    "cov": task_cov,
    "lint": task_lint,
    "fmt": task_fmt,
    "types": task_types,
    "check": task_check,
    "api": task_api,
    "ui": task_ui,
    "dev": task_dev,
    "doctor": task_doctor,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("task", choices=sorted(TASKS), help="task to run")
    args = parser.parse_args()
    TASKS[args.task]()


if __name__ == "__main__":
    main()
