"""Streamlit UI tests.

Driven through ``streamlit.testing.v1.AppTest``, which executes the real page
script. These are the tests the old JavaScript frontend could not have: it had
no way to assert that a backend failure produced an actionable message rather
than the string "Something went wrong".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from resume_tailor.core.config import reset_settings

pytestmark = pytest.mark.ui

APP_PATH = str(Path(__file__).resolve().parents[2] / "ui" / "app.py")
JD = "We need a Data Scientist with Python and SQL for a fintech team."


@pytest.fixture
def ui_env(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[None]:
    """Point the app at an isolated data directory and the fake engine.

    ``cache_resource`` is cleared because the client is cached per process, and
    a client built for a previous test's settings would silently be reused.
    """
    monkeypatch.setenv("RT_UI_MODE", "embedded")
    monkeypatch.setenv("RT_PDF_ENGINE", "fake")
    monkeypatch.setenv("RT_ENVIRONMENT", "test")
    monkeypatch.setenv("RT_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("RT_DATA_DIR", str(data_dir))
    reset_settings()
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()
    reset_settings()


@pytest.fixture
def app(ui_env: None) -> AppTest:
    instance = AppTest.from_file(APP_PATH, default_timeout=120)
    instance.run()
    return instance


def matched(app: AppTest) -> AppTest:
    """Run the app through step 1 so the selection UI exists."""
    app.text_area[0].set_value(JD).run()
    app.button[0].click().run()
    return app


class TestInitialRender:
    def test_page_loads_without_error(self, app: AppTest) -> None:
        assert not app.exception
        assert app.title[0].value == "Resume Tailoring Tool"

    def test_backend_status_is_shown(self, app: AppTest) -> None:
        assert any("Backend ready" in item.value for item in app.sidebar.success)

    def test_placeholder_engine_is_loudly_flagged(self, app: AppTest) -> None:
        """Someone must never be able to mistake a blank placeholder PDF for a
        real resume, so the notice is permanent rather than a one-time toast."""
        warnings = " ".join(item.value for item in app.sidebar.warning)
        assert "blank" in warnings.lower()
        assert "tectonic" in warnings.lower()

    def test_analyse_button_is_disabled_without_a_jd(self, app: AppTest) -> None:
        assert app.button[0].disabled is True

    def test_selection_ui_is_hidden_until_a_match_runs(self, app: AppTest) -> None:
        assert app.checkbox.len == 0


class TestMatchFlow:
    def test_match_populates_the_project_list(self, app: AppTest) -> None:
        matched(app)
        assert not app.exception
        assert app.checkbox.len > 0

    def test_hidden_projects_are_not_offered(self, app: AppTest) -> None:
        matched(app)
        labels = " ".join(box.label for box in app.checkbox)
        assert "Unverified Work In Progress" not in labels

    def test_top_matches_are_preselected(self, app: AppTest) -> None:
        matched(app)
        assert any(box.value for box in app.checkbox)

    def test_zero_score_projects_are_still_listed(self, app: AppTest) -> None:
        """A zero-score project may be the strongest one; the UI deprioritises
        it rather than hiding it."""
        app.text_area[0].set_value("A job about underwater basket weaving.").run()
        app.button[0].click().run()
        assert app.checkbox.len > 0
        assert not any(box.value for box in app.checkbox)


class TestGeneration:
    def test_generate_produces_a_download(self, app: AppTest) -> None:
        matched(app)
        generate = next(button for button in app.button if button.label == "Generate PDF")
        generate.click().run()

        assert not app.exception
        assert len(app.get("download_button")) == 1
        assert any("Generated" in item.value for item in app.success)

    def test_result_metrics_are_shown(self, app: AppTest) -> None:
        matched(app)
        next(b for b in app.button if b.label == "Generate PDF").click().run()
        labels = {metric.label for metric in app.metric}
        assert {"Pages", "Font size", "Attempts", "Engine"} <= labels

    def test_preview_renders_latex_without_compiling(self, app: AppTest) -> None:
        matched(app)
        next(b for b in app.button if b.label == "Preview LaTeX").click().run()
        assert not app.exception
        assert app.code.len == 1
        assert app.code[0].value.startswith("\\documentclass")

    def test_dangerous_summary_shows_actionable_guidance(self, app: AppTest) -> None:
        matched(app)
        app.text_area[1].set_value(r"\input{/etc/passwd}").run()
        next(b for b in app.button if b.label == "Generate PDF").click().run()

        assert not app.exception, "a rejected summary must not crash the page"
        errors = " ".join(item.value for item in app.error)
        info = " ".join(item.value for item in app.info)
        assert "not permitted" in errors
        assert "**bold**" in info

    def test_generate_is_disabled_with_no_selection(self, app: AppTest) -> None:
        matched(app)
        for box in app.checkbox:
            box.uncheck()
        app.run()
        generate = next(b for b in app.button if b.label == "Generate PDF")
        assert generate.disabled is True


class TestBackendFailure:
    def test_unreachable_backend_explains_how_to_start_it(
        self, monkeypatch: pytest.MonkeyPatch, data_dir: Path
    ) -> None:
        """The old frontend could only say "Could not reach the backend"; this
        distinguishes the failure and tells the user the command to run."""
        monkeypatch.setenv("RT_UI_MODE", "http")
        monkeypatch.setenv("RT_API_BASE_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("RT_DATA_DIR", str(data_dir))
        reset_settings()
        st.cache_resource.clear()

        app = AppTest.from_file(APP_PATH, default_timeout=60)
        app.run()

        try:
            assert not app.exception
            messages = " ".join(item.value for item in app.error)
            assert "could not reach" in messages.lower()
            assert "tasks.py api" in " ".join(item.value for item in app.info)
        finally:
            st.cache_resource.clear()
            reset_settings()


class TestStateHandling:
    def test_rerunning_a_match_resets_stale_results(self, app: AppTest) -> None:
        matched(app)
        next(b for b in app.button if b.label == "Generate PDF").click().run()
        assert len(app.get("download_button")) == 1

        app.text_area[0].set_value("A completely different job description.").run()
        app.button[0].click().run()
        assert len(app.get("download_button")) == 0, (
            "a PDF from the previous selection must not remain on screen"
        )

    def test_state_survives_a_rerun(self, app: AppTest) -> None:
        matched(app)
        app.run()
        assert app.checkbox.len > 0, "session state must survive a rerun"
