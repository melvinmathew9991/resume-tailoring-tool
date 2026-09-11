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


def _parse_panel_script(payload: dict[str, object]) -> None:
    """Run as its own Streamlit script by ``AppTest.from_function``, which
    re-executes the source in a fresh module -- so every name it uses has to be
    imported inside it."""
    from resume_tailor.api.schemas import ParseCheckOut
    from ui import components

    components.render_parse_check(ParseCheckOut.model_validate(payload), engine="fake")


def parse_panel(status: str, **overrides: object) -> AppTest:
    """Render just the parse-check panel, for the states a real compile on a
    developer machine cannot produce on demand."""
    findings = {
        "pass": [],
        "warn": [{"code": "ligatures", "severity": "warn", "detail": "two words use ligatures"}],
        "fail": [{"code": "no_text", "severity": "fail", "detail": "nothing could be extracted"}],
    }[status]
    payload: dict[str, object] = {
        "status": status,
        "parses": status != "fail",
        "characters": 4000,
        "words": 700,
        "term_coverage": 0.94,
        "expected_terms": 500,
        "missing_terms": ["classification", "mlflow"],
        "links": ["mailto:test@example.com"],
        "findings": findings,
        "note": "how this was measured",
        **overrides,
    }

    instance = AppTest.from_function(
        _parse_panel_script, default_timeout=60, kwargs={"payload": payload}
    )
    instance.run()
    return instance


class TestParseCheckPanel:
    def test_a_failure_is_an_error_not_a_caption(self) -> None:
        """An unreadable PDF is the one outcome that must stop someone sending
        the document, so it cannot be a line of grey text."""
        app = parse_panel("fail")
        assert not app.exception
        assert any("ATS may not read" in item.value for item in app.error)

    def test_a_warning_names_the_cause(self) -> None:
        app = parse_panel("warn")
        assert not app.exception
        assert any("ligatures" in item.value for item in app.warning)

    def test_the_detail_lists_the_words_that_were_lost(self) -> None:
        """The actionable part: which keywords an ATS will not see."""
        app = parse_panel("warn")
        body = " ".join(item.value for item in app.markdown)
        assert "classification" in body and "mlflow" in body

    def test_further_findings_are_not_dropped(self) -> None:
        app = parse_panel(
            "warn",
            findings=[
                {"code": "ligatures", "severity": "warn", "detail": "first cause"},
                {"code": "split_words", "severity": "warn", "detail": "second cause"},
            ],
        )
        captions = " ".join(item.value for item in app.caption)
        assert "second cause" in captions


class TestInitialRender:
    def test_page_loads_without_error(self, app: AppTest) -> None:
        assert not app.exception
        assert app.title[0].value == "Resume Tailoring Tool"

    def test_backend_status_is_shown(self, app: AppTest) -> None:
        assert any("Backend ready" in item.value for item in app.sidebar.success)

    def test_placeholder_engine_is_loudly_flagged(self, app: AppTest) -> None:
        """Someone must never be able to mistake a placeholder PDF for a real
        resume, so the notice is permanent rather than a one-time toast.

        It says *not typeset* rather than *blank*: the placeholder engine emits
        the document's real words, so that the text-extraction check has
        something to read. Calling the output blank would now be wrong, and a
        notice that is wrong about one thing gets ignored about the rest.
        """
        warnings = " ".join(item.value for item in app.sidebar.warning)
        assert "placeholder" in warnings.lower()
        assert "not typeset" in warnings.lower()
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

    def test_a_clean_parse_check_stays_quiet(self, app: AppTest) -> None:
        """It reports, it does not congratulate. A green banner on every single
        generation is one the reader learns to scroll past -- including on the
        run where it finally has something to say."""
        matched(app)
        next(b for b in app.button if b.label == "Generate PDF").click().run()
        captions = " ".join(item.value for item in app.caption)
        assert "read back from the PDF" in captions
        assert not any("ATS may not read" in item.value for item in app.error)

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


class TestFeatureNavigation:
    """The sidebar switch between the three top-level features.

    The tailoring flow is the default, so every existing test above continues
    to exercise it without knowing the switch exists -- which is the property
    worth protecting here.
    """

    def test_the_default_view_is_the_tailoring_flow(self, app: AppTest) -> None:
        assert app.sidebar.radio[0].value == "Tailor resume"
        assert "Resume Tailoring Tool" in app.title[0].value

    def test_the_three_features_are_offered(self, app: AppTest) -> None:
        assert app.sidebar.radio[0].options == [
            "Tailor resume",
            "Profile knowledge",
            "ATS match check",
        ]

    def test_the_knowledge_view_renders_its_empty_state(self, app: AppTest) -> None:
        app.sidebar.radio[0].set_value("Profile knowledge").run()
        assert not app.exception
        assert "Profile knowledge" in app.title[0].value
        assert any("Nothing yet" in info.value for info in app.info)

    def test_pasted_text_is_stored_and_the_result_is_reported(self, app: AppTest) -> None:
        app.sidebar.radio[0].set_value("Profile knowledge").run()
        app.radio(key="knowledge_method").set_value("Paste text").run()
        app.text_area(key="knowledge_paste_box").set_value(
            "Technical Skills\nPython, SQL, Airflow\n"
        ).run()
        app.button[0].click().run()

        assert not app.exception
        assert any("Added" in success.value for success in app.success)

    def test_saving_nothing_asks_for_input_rather_than_failing(self, app: AppTest) -> None:
        app.sidebar.radio[0].set_value("Profile knowledge").run()
        app.radio(key="knowledge_method").set_value("Paste text").run()
        app.button[0].click().run()
        assert not app.exception
        assert any("Paste some text" in warning.value for warning in app.warning)

    def test_the_ats_view_renders_and_scores(self, app: AppTest) -> None:
        app.sidebar.radio[0].set_value("ATS match check").run()
        assert not app.exception
        assert "ATS match check" in app.title[0].value

        app.text_area(key="ats_jd_box").set_value(
            "We need Python, SQL and Snowflake for a fintech team."
        ).run()
        app.button(key="ats_check_button").click().run()

        assert not app.exception
        assert app.metric.len >= 3, "score, requirement count and band"
        assert any("Gaps" in header.value for header in app.header)

    def test_the_ats_view_asks_for_a_job_description_first(self, app: AppTest) -> None:
        app.sidebar.radio[0].set_value("ATS match check").run()
        app.button(key="ats_check_button").click().run()
        assert not app.exception
        assert any("Paste a job description" in warning.value for warning in app.warning)

    def test_the_ats_view_offers_no_way_to_generate_a_resume(self, app: AppTest) -> None:
        """Feature 2 is standalone: there is no project selection and no
        generate button anywhere on the page."""
        app.sidebar.radio[0].set_value("ATS match check").run()
        app.text_area(key="ats_jd_box").set_value("We need Python.").run()
        app.button(key="ats_check_button").click().run()

        labels = {button.label for button in app.button}
        assert "Generate PDF" not in labels
        assert "Preview LaTeX" not in labels
        assert app.checkbox.len == 0

    def test_the_save_confirmation_is_shown_once_and_not_re_asserted(self, app: AppTest) -> None:
        """Session state outlives the rerun that set it, so a message left in
        place would re-announce a save on every later interaction."""
        app.sidebar.radio[0].set_value("Profile knowledge").run()
        app.radio(key="knowledge_method").set_value("Paste text").run()
        app.text_area(key="knowledge_paste_box").set_value("Skills\nPython\n").run()
        app.button[0].click().run()
        assert any("Added" in success.value for success in app.success)

        app.run()
        assert not any("Added" in success.value for success in app.success)

    def test_the_tailoring_view_scores_the_resume_without_compiling(self, app: AppTest) -> None:
        """The score is feedback on the project selection, so it must appear
        before -- and without -- a compile."""
        app.text_area[0].set_value("We need Python, SQL and FastAPI.").run()
        app.button[0].click().run()
        assert not app.exception
        assert any("ATS match" in header.value for header in app.header)
        assert any(metric.label.startswith("ATS match") for metric in app.metric)

    def test_the_two_scores_are_labelled_as_different_questions(self, app: AppTest) -> None:
        """One scores the document, the other scores the candidate. Two
        similar-looking numbers meaning different things is the failure worth
        preventing outright."""
        app.text_area[0].set_value(JD).run()
        app.button[0].click().run()
        assert any("this resume" in caption.value for caption in app.caption)


class TestClientCacheInvalidation:
    """`get_client` is `st.cache_resource`, which outlives a hot reload.

    Streamlit only invalidates that cache when the decorated function's own
    source changes, so editing `ui/client.py` used to leave a live session
    holding an instance of the previous class -- failing with `AttributeError`
    on any newly added method while the source on disk plainly had it.
    """

    def test_the_fingerprint_is_stable_while_the_file_is(self) -> None:
        from ui.client import source_fingerprint

        assert source_fingerprint() == source_fingerprint()
        assert source_fingerprint() != "unknown"

    def test_the_fingerprint_changes_when_the_module_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ui import client as client_module

        stand_in = tmp_path / "client.py"
        stand_in.write_text("# v1", encoding="utf-8")
        monkeypatch.setattr(client_module, "__file__", str(stand_in))
        before = client_module.source_fingerprint()

        stand_in.write_text("# v2 -- a method was added", encoding="utf-8")
        assert client_module.source_fingerprint() != before

    def test_a_missing_file_degrades_rather_than_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ui import client as client_module

        monkeypatch.setattr(client_module, "__file__", str(tmp_path / "gone.py"))
        assert client_module.source_fingerprint() == "unknown"

    def test_every_client_method_exists_in_both_modes(self) -> None:
        """The AttributeError that started this: the two implementations must
        cover the whole protocol, or one mode fails on a call the other
        serves."""
        from ui.client import BackendClient, EmbeddedBackendClient, HttpBackendClient

        required = {
            name
            for name in BackendClient.__annotations__ | vars(BackendClient).keys()
            if not name.startswith("_") and name != "mode"
        }
        for implementation in (HttpBackendClient, EmbeddedBackendClient):
            missing = [name for name in required if not hasattr(implementation, name)]
            assert not missing, f"{implementation.__name__} is missing {missing}"

    def test_the_knowledge_view_re_reads_the_store_every_render(
        self, app: AppTest, data_dir: Path
    ) -> None:
        """A cached copy is how "the app did not pick up my upload" happens.

        The store is written by this app, by `tasks.py`, and potentially by
        another session, so the view must not render a snapshot.
        """
        app.sidebar.radio[0].set_value("Profile knowledge").run()
        app.radio(key="knowledge_method").set_value("Paste text").run()
        app.text_area(key="knowledge_paste_box").set_value("Skills\nPython\n").run()
        app.button[0].click().run()

        # Change the store behind the app's back, then rerun without interacting.
        store = data_dir / "knowledge.json"
        assert store.exists()
        store.write_text('{"entries": [], "experience": [], "sources": []}', encoding="utf-8")
        app.run()

        assert not app.exception
        assert any("Nothing yet" in info.value for info in app.info)

    def test_an_upload_invalidates_a_previous_ats_report(self, app: AppTest) -> None:
        """Otherwise a fresh knowledge base is compared against a score that
        predates it, which reads as the two features disagreeing."""
        app.sidebar.radio[0].set_value("ATS match check").run()
        app.text_area(key="ats_jd_box").set_value("We need Python and Terraform.").run()
        app.button(key="ats_check_button").click().run()
        assert app.metric.len > 0, "a score was produced"

        app.sidebar.radio[0].set_value("Profile knowledge").run()
        app.radio(key="knowledge_method").set_value("Paste text").run()
        app.text_area(key="knowledge_paste_box").set_value("Skills\nTerraform\n").run()
        app.button[0].click().run()

        app.sidebar.radio[0].set_value("ATS match check").run()
        assert not app.exception
        # The stale report is gone; the view stops before rendering a score.
        assert not any(m.label.startswith("ATS match") for m in app.metric)

    def test_merge_on_an_unhealthy_store_points_at_replace(self, app: AppTest) -> None:
        """Merge cannot clean a polluted store. Saying so where the choice is
        made is the difference between a dead end and a next step."""
        app.sidebar.radio[0].set_value("Profile knowledge").run()
        app.radio(key="knowledge_method").set_value("Paste text").run()
        app.text_area(key="knowledge_paste_box").set_value("Skills\nPython\n").run()
        app.button[0].click().run()

        # The seeded store warns (no dated roles), so the merge hint must show.
        assert any("Merge cannot fix them" in info.value for info in app.info)
