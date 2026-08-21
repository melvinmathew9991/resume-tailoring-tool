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

    def test_analyse_is_clickable_before_the_text_area_commits(self, app: AppTest) -> None:
        """The button must NOT be disabled on an empty `jd_text`.

        `st.text_area` commits on blur, not on keystroke, so a freshly pasted
        job description has not reached the server yet. Gating the button on
        `jd_text` left it greyed out after a paste until the user clicked
        elsewhere, which reads as the app lagging.
        """
        assert app.button[0].disabled is False

    def test_analyse_without_a_jd_explains_itself(self, app: AppTest) -> None:
        app.button[0].click().run()
        assert not app.exception
        warnings = " ".join(item.value for item in app.warning)
        assert "job description" in warnings.lower()
        assert app.checkbox.len == 0, "an empty submission must not run a match"

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


class TestCloudEntrypoint:
    """The Community Cloud entrypoint, not `ui/app.py`.

    Every test above drives `ui/app.py` directly. That is the wrong file on
    Community Cloud, which runs `streamlit_app.py`, and the gap let a total
    failure ship green: the wrapper used to invoke the app with `import
    ui.app`, and an import only executes a module once. Streamlit re-executes
    its entrypoint on every interaction, so from the second successful import
    onwards `main()` never ran and the page rendered nothing at all.

    What made it survive review is the ordering. `main()` calls `st.stop()`
    until a match exists, and that exception propagates out of the import,
    discarding the half-initialised module so the next run re-imports it. The
    app therefore works for exactly as long as it keeps stopping early. The
    first run that completes normally -- a successful match -- is the one that
    poisons it, and the *next* interaction goes blank and stays blank.

    So the reproduction has to be at least three runs long, and the third has
    to follow a run that did not stop. Anything shorter passes against the bug.
    """

    @pytest.fixture
    def cloud_app(self, ui_env: None) -> AppTest:
        entrypoint = str(Path(__file__).resolve().parents[2] / "streamlit_app.py")
        return AppTest.from_file(entrypoint, default_timeout=120)

    def test_the_entrypoint_renders(self, cloud_app: AppTest) -> None:
        cloud_app.run()
        assert not cloud_app.exception
        assert cloud_app.title.len == 1

    def test_it_survives_an_interaction_after_a_successful_match(self, cloud_app: AppTest) -> None:
        cloud_app.run()
        cloud_app.text_area[0].set_value(JD)
        cloud_app.button[0].click().run()
        assert cloud_app.header.len >= 2, "the match itself must render"

        # The step that used to blank the page: reselecting a project.
        cloud_app.checkbox[0].uncheck().run()

        assert not cloud_app.exception
        assert cloud_app.header.len >= 2, (
            "the page went blank after a project reselection -- the entrypoint "
            "is executing the app once instead of on every rerun"
        )
        assert cloud_app.checkbox.len > 0

    def test_it_keeps_rendering_across_many_reruns(self, cloud_app: AppTest) -> None:
        cloud_app.run()
        cloud_app.text_area[0].set_value(JD)
        cloud_app.button[0].click().run()

        for index in range(5):
            cloud_app.run()
            assert cloud_app.header.len >= 2, f"blank on rerun {index + 1}"

    def test_the_engine_download_is_skipped_for_a_pinned_engine(
        self, cloud_app: AppTest, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`RT_PDF_ENGINE=fake` (set by `ui_env`) must not fetch an engine.

        Without this guard the UI suite would download 10 MB from GitHub on any
        machine without tectonic on PATH -- including CI.
        """

        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("the entrypoint must not download an engine here")

        monkeypatch.setattr("urllib.request.urlopen", explode)
        cloud_app.run()
        assert not cloud_app.exception
