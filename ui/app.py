"""Resume Tailoring Tool -- Streamlit frontend.

Replaces the hand-rolled HTML/CSS/JavaScript frontend. Same four steps, but in
Python, sharing the API's response models, and with the failure paths the old
one could not express: a distinguishable "backend unreachable" versus "backend
said no", a visible PDF-engine status, and a guard against double-submitting a
multi-second compile.

Run it with ``python tasks.py ui`` (talks to the API over HTTP) or
``RT_UI_MODE=embedded streamlit run ui/app.py`` (single process).
"""

from __future__ import annotations

import streamlit as st

from resume_tailor.api.schemas import ResumeRequest
from ui import components
from ui.client import BackendClient, BackendError, build_client
from ui.state import get_state, reset_results, sync_bank_version

st.set_page_config(
    page_title="Resume Tailoring Tool",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

AUTO_SELECT_TOP_N = 5


@st.cache_resource
def get_client() -> BackendClient:
    """One client per session process.

    ``cache_resource`` rather than ``cache_data``: this holds a live HTTP
    connection pool, which must not be copied per rerun.
    """
    return build_client()


def clear_project_checkboxes() -> None:
    """Drop the checkbox widget state before repopulating the list.

    Streamlit keeps widget values keyed by widget key across reruns, so a
    checkbox that existed in the previous match result keeps its old ticked
    state and quietly overrides the new auto-selection. Clearing the keys is
    the documented way to force the widgets to take their new defaults.
    """
    for key in [k for k in st.session_state if str(k).startswith("project_")]:
        del st.session_state[key]


def main() -> None:
    state = get_state()
    client = get_client()

    st.title("Resume Tailoring Tool")
    st.caption(
        "Paste a job description, review the keyword match, choose your projects, "
        "and generate a page-limit-verified PDF. Every bullet is pre-written and "
        "fact-checked -- this tool selects and formats, it never writes new claims."
    )

    # -- sidebar ------------------------------------------------------------
    try:
        readiness = client.readiness()
        components.render_backend_status(readiness, client.mode)
    except BackendError as exc:
        st.sidebar.error(f"Backend unavailable ({client.mode} mode)")
        components.render_error(exc)
        st.stop()

    if state.meta is None:
        try:
            state.meta = client.meta()
        except BackendError as exc:
            components.render_error(exc)
            st.stop()
    meta = state.meta

    if sync_bank_version(state, meta.bank_version):
        st.info("The project bank changed on disk. Re-run the analysis to refresh scores.")
        state.match = None
        reset_results(state)

    with st.sidebar.expander("Limits"):
        st.write(
            {
                "max job description characters": meta.max_jd_chars,
                "max summary characters": meta.max_summary_chars,
                "max projects": meta.max_selected_projects,
                "max pages": meta.max_pages_limit,
                "font ladder": [f"{size:g}pt" for size, _ in meta.font_ladder],
            }
        )

    # -- step 1: job description --------------------------------------------
    st.header("1 · Paste the job description")
    state.jd_text = st.text_area(
        "Job description",
        value=state.jd_text,
        height=220,
        placeholder="Paste the full job description here...",
        label_visibility="collapsed",
    )

    left, right = st.columns([1, 4])
    if left.button("Analyse & match", type="primary", disabled=not state.jd_text.strip()):
        try:
            state.match = client.match(state.jd_text)
            clear_project_checkboxes()
            state.selected_keys = [
                result.key
                for result in state.match.ranked_projects[:AUTO_SELECT_TOP_N]
                if result.score > 0
            ]
            reset_results(state)
        except BackendError as exc:
            state.match = None
            components.render_error(exc)
    right.caption(f"{len(state.jd_text):,} / {meta.max_jd_chars:,} characters")

    if state.match is None:
        st.stop()

    # -- step 2: selection ---------------------------------------------------
    st.header("2 · Choose projects")
    st.caption(components.MATCH_CAVEAT)

    with st.expander("Terms not covered by your project bank", expanded=False):
        components.render_gap_terms(state.match)

    selected: list[str] = []
    for result in state.match.ranked_projects:
        checked = st.checkbox(
            components.project_label(result),
            value=result.key in state.selected_keys,
            key=f"project_{result.key}",
        )
        if checked:
            selected.append(result.key)

    if selected != state.selected_keys:
        state.selected_keys = selected
        reset_results(state)

    st.caption(
        f"{len(state.selected_keys)} selected. Order on the resume follows the ranking above."
    )

    # -- step 3: options -----------------------------------------------------
    st.header("3 · Options")
    option_left, option_right = st.columns(2)
    state.max_pages = option_left.number_input(
        "Maximum pages",
        min_value=1,
        max_value=meta.max_pages_limit,
        value=state.max_pages,
        help="The tool tries progressively smaller fonts until it fits, and warns "
        "loudly if it never does.",
    )
    state.summary_override = option_right.text_area(
        "Summary override (optional)",
        value=state.summary_override,
        height=140,
        placeholder="Leave blank to use the default summary. **bold** and *italic* work.",
        help="Plain text. LaTeX commands are rejected; use **bold** / *italic*.",
    )

    request = ResumeRequest(
        selected_project_keys=state.selected_keys,
        max_pages=int(state.max_pages),
        summary=state.summary_override.strip() or None,
    )

    action_left, action_right, _ = st.columns([1, 1, 3])
    preview_clicked = action_left.button(
        "Preview LaTeX", disabled=not state.selected_keys, help="Renders without compiling."
    )
    generate_clicked = action_right.button(
        "Generate PDF",
        type="primary",
        disabled=not state.selected_keys or state.generating,
    )

    if preview_clicked:
        try:
            preview = client.preview(request)
            st.caption(f"{preview.character_count:,} characters of LaTeX")
            for warning in preview.warnings:
                st.warning(warning)
            st.code(preview.tex, language="latex")
        except BackendError as exc:
            components.render_error(exc)

    if generate_clicked:
        state.generating = True
        try:
            with st.spinner("Compiling — trying font sizes until it fits…"):
                state.result = client.generate(request)
                state.pdf_bytes = client.download(state.result.document_id).content
        except BackendError as exc:
            reset_results(state)
            components.render_error(exc)
        finally:
            state.generating = False

    # -- step 4: result ------------------------------------------------------
    if state.result is not None:
        st.header("4 · Result")
        components.render_result(state.result, state.pdf_bytes)


main()
