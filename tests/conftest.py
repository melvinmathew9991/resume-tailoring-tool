"""Shared fixtures.

Design rule: the default fixtures use the **fake PDF engine** and therefore
need no LaTeX toolchain. Tests that want a real compile ask for it explicitly
and carry the ``integration``/``latex`` markers, so ``pytest -m "not latex"``
is fully green on a bare machine. That is not a convenience -- the project's
primary development machine has no TeX installed, and a suite that cannot run
there protects nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from resume_tailor.core.config import Settings
from resume_tailor.data.bank_repo import BankRepository
from resume_tailor.data.profile_repo import ProfileRepository
from resume_tailor.domain.models import ProjectBank
from resume_tailor.render.engines.fake import FakeEngine
from resume_tailor.services.resume_service import ResumeService

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DATA_DIR = REPO_ROOT / "data"


# --- raw content ------------------------------------------------------------


@pytest.fixture
def sample_bank_raw() -> dict[str, Any]:
    """A small hand-built bank.

    Kept separate from the real one so unit tests do not break every time a
    bullet is reworded, and so hostile shapes can be introduced deliberately.
    """
    return {
        "proj_a": {
            "title": r"Project A -- \textbf{Test} Fixture",
            "github": "testuser/project-a",
            "domain": ["fintech"],
            "keywords": ["python", "sql", "machine learning", "r"],
            "bullets": [
                r"Built a test system using Python and SQL.",
                r"Achieved a 95\% accuracy score on the held-out set.",
                r"Deployed via FastAPI with automated testing.",
            ],
        },
        "proj_b": {
            "title": r"Project B -- Underscore\_Test \& Special Chars",
            "github": "testuser/project_b_with_underscores",
            "domain": ["healthcare"],
            "keywords": ["nlp", "healthcare"],
            "bullets": [
                r"Processed data with a 50\% reduction in errors.",
                r"Used R\&D methodology for the analysis.",
            ],
        },
        "proj_hidden": {
            "title": "Unverified Work In Progress",
            "github": "testuser/wip",
            "domain": ["misc"],
            "keywords": ["experimental"],
            "bullets": ["This project has not been fact-checked yet."],
            "hidden": True,
        },
    }


@pytest.fixture
def sample_bank(sample_bank_raw: dict[str, Any]) -> ProjectBank:
    return ProjectBank.from_raw(sample_bank_raw)


@pytest.fixture
def real_bank() -> ProjectBank:
    """The actual production bank, for tests that must validate real content."""
    return BankRepository(REAL_DATA_DIR / "project_bank.json").load()


@pytest.fixture
def sample_profile_yaml() -> str:
    return """
personal:
  name: Test Person
  title: Data Scientist
  phone: "+91 0000000000"
  location: Testville
  email: test@example.com
  linkedin_url: https://www.linkedin.com/in/test/
  linkedin_display: linkedin.com/in/test
  github_url: https://github.com/testuser
  github_display: github.com/testuser
summary: A test summary with \\textbf{markup} and a 50\\% figure.
experience:
  - title: Data Scientist
    company: Test Corp
    dates: 2024 -- 2025
    subtitle: ""
    bullets:
      - Did a measurable thing with Python.
skills:
  - name: Languages
    content: Python, SQL
education:
  - degree: BSc Mathematics
    institution: Test University
    dates: 2017 -- 2020
"""


# --- filesystem -------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path, sample_bank_raw: dict[str, Any], sample_profile_yaml: str) -> Path:
    """An isolated data directory, so tests can mutate content freely."""
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "project_bank.json").write_text(
        json.dumps(sample_bank_raw, indent=2), encoding="utf-8"
    )
    (directory / "profile.yaml").write_text(sample_profile_yaml, encoding="utf-8")
    return directory


# --- configuration ----------------------------------------------------------


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    """Test settings against the isolated data directory.

    Constructed explicitly rather than through ``get_settings()`` so no
    developer's ``.env`` or exported ``RT_*`` variable can change what the
    suite asserts.
    """
    return Settings(
        environment="test",
        pdf_engine="fake",
        log_level="WARNING",
        data_dir=data_dir,
        max_concurrent_compiles=2,
        compile_timeout_s=10.0,
    )


@pytest.fixture
def real_settings() -> Settings:
    """Test settings pointed at the production data directory."""
    return Settings(
        environment="test", pdf_engine="fake", log_level="WARNING", data_dir=REAL_DATA_DIR
    )


# --- engine and service -----------------------------------------------------


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def service(settings: Settings, engine: FakeEngine) -> ResumeService:
    return ResumeService(
        settings=settings,
        bank_repo=BankRepository(settings.bank_path),
        profile_repo=ProfileRepository(settings.profile_path),
        engine=engine,
    )


@pytest.fixture
def real_service(real_settings: Settings, engine: FakeEngine) -> ResumeService:
    return ResumeService(
        settings=real_settings,
        bank_repo=BankRepository(real_settings.bank_path),
        profile_repo=ProfileRepository(real_settings.profile_path),
        engine=engine,
    )


# --- http client ------------------------------------------------------------


@pytest.fixture
def client(settings: Settings, engine: FakeEngine) -> Iterator[TestClient]:
    from resume_tailor.api.main import create_app

    app = create_app(settings, engine=engine)
    # raise_server_exceptions=False so the registered 500 handler runs and the
    # test sees the response a real client would get, not a re-raised traceback.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def real_client(real_settings: Settings, engine: FakeEngine) -> Iterator[TestClient]:
    from resume_tailor.api.main import create_app

    app = create_app(real_settings, engine=engine)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
