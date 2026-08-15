"""Reusable render helpers.

Kept out of ``app.py`` so each can be exercised on its own, and so the page
script stays a readable sequence of steps rather than a wall of layout code.
"""

from __future__ import annotations

import streamlit as st

from resume_tailor.api.schemas import (
    GenerateResponse,
    MatchResponse,
    MatchResultOut,
    ReadinessResponse,
)
from ui.client import BackendError

MATCH_CAVEAT = (
    "Scores are literal keyword overlap, not a judgement of relevance. "
    "A project scoring zero may still be your strongest one -- it may simply "
    "use different words than this job description."
)


def render_backend_status(readiness: ReadinessResponse, mode: str) -> None:
    """Sidebar health panel.

    The engine name is shown unconditionally and on purpose: when no LaTeX
    toolchain is installed the app still works end to end, but the PDF is a
    blank placeholder. Anything less than a loud, permanent notice risks
    someone sending a blank page to an employer.
    """
    engine = readiness.checks.get("pdf_engine", {})
    bank = readiness.checks.get("project_bank", {})

    if readiness.ready:
        st.sidebar.success(f"Backend ready ({mode} mode)")
    else:
        st.sidebar.error(f"Backend not ready ({mode} mode)")

    st.sidebar.caption(f"PDF engine: **{engine.get('name', 'unknown')}**")
    if engine.get("name") == "fake":
        st.sidebar.warning(
            "No LaTeX engine is installed, so generated PDFs are **blank "
            "placeholders with the right page count** -- useful for checking "
            "length, not for sending anywhere.\n\n"
            "Install Tectonic (one self-contained binary) to get real output: "
            "`brew install tectonic`, `cargo install tectonic`, or on Windows "
            "the release binary from "
            "https://github.com/tectonic-typesetting/tectonic/releases",
            icon="⚠️",
        )
    elif not engine.get("ok", False):
        st.sidebar.error(engine.get("detail", "PDF engine unavailable"))

    if bank.get("ok"):
        st.sidebar.caption(
            f"Bank: {bank.get('selectable', 0)} selectable of {bank.get('projects', 0)} "
            f"(version `{bank.get('version', '?')}`)"
        )
        for warning in bank.get("warnings", []):
            st.sidebar.caption(f"• {warning}")
    else:
        st.sidebar.error(bank.get("error", "project bank could not be loaded"))


def render_error(error: BackendError) -> None:
    """One place that turns a backend failure into something actionable."""
    guidance = {
        "unreachable": "Start the API with `python tasks.py api`, or switch to "
        "embedded mode with `RT_UI_MODE=embedded`.",
        "unsafe_content": "Remove the LaTeX command from your text. Use `**bold**` "
        "and `*italic*` for formatting instead.",
        "unknown_project": "The project bank changed. Re-run the analysis to refresh the list.",
        "hidden_project": "That project is marked hidden in the bank and cannot go on a resume.",
        "rate_limited": "Too many requests in the last minute. Wait a moment and try again.",
        "payload_too_large": "The text you pasted is too large. Trim it and try again.",
        "compile_timeout": "Compilation hung and was stopped. Try selecting fewer projects.",
        "engine_unavailable": "No PDF engine is installed. Install Tectonic, or use "
        "Preview to inspect the LaTeX source without compiling.",
    }.get(error.code)

    st.error(f"**{error.title}** — {error.detail}")
    if guidance:
        st.info(guidance)


def render_gap_terms(match: MatchResponse) -> None:
    if not match.gap_terms:
        st.caption("No unmatched terms found in this job description.")
        return
    st.caption(
        "Terms in the job description with no counterpart anywhere in your "
        "project bank. Review them by hand -- this is the real gap list:"
    )
    st.markdown(" ".join(f"`{term}`" for term in match.gap_terms))


def project_label(result: MatchResultOut) -> str:
    if result.matched_keywords:
        matched = ", ".join(result.matched_keywords[:6])
        suffix = "…" if len(result.matched_keywords) > 6 else ""
        detail = f"matched: {matched}{suffix}"
    else:
        detail = "no keyword overlap — review before excluding"
    domain = " · domain hit" if result.domain_match else ""
    return f"**{result.title}**  \n`score {result.score}`{domain} — {detail}"


def render_result(result: GenerateResponse, pdf_bytes: bytes | None) -> None:
    """The result panel, with the page-fit guarantee front and centre."""
    if result.fits:
        st.success(
            f"Generated {result.page_count} page(s) at {result.font_size_used:g}pt "
            f"(attempt {result.compile_attempts} of the size ladder)."
        )
    else:
        st.warning(f"**Does not fit.** {result.warning}", icon="⚠️")

    columns = st.columns(4)
    columns[0].metric("Pages", f"{result.page_count} / {result.max_pages}")
    columns[1].metric("Font size", f"{result.font_size_used:g}pt")
    columns[2].metric("Attempts", result.compile_attempts)
    columns[3].metric("Engine", result.engine)

    for warning in result.source_warnings:
        st.caption(f"Source audit: {warning}")

    if pdf_bytes:
        st.download_button(
            "Download PDF",
            data=pdf_bytes,
            file_name=result.filename,
            mime="application/pdf",
            type="primary",
        )
        if result.engine == "fake":
            st.caption(
                "Reminder: this PDF came from the placeholder engine. The page "
                "count is meaningful; the content is blank."
            )
