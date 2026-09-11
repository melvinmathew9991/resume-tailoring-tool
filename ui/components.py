"""Reusable render helpers.

Kept out of ``app.py`` so each can be exercised on its own, and so the page
script stays a readable sequence of steps rather than a wall of layout code.
"""

from __future__ import annotations

import streamlit as st

from resume_tailor.api.schemas import (
    GateOut,
    GenerateResponse,
    MatchResponse,
    MatchResultOut,
    ReadinessResponse,
    ResumeAtsResponse,
)
from ui.client import BackendError

MATCH_CAVEAT = (
    "Scores are literal keyword overlap, not a judgement of relevance. "
    "A project scoring zero may still be your strongest one -- it may simply "
    "use different words than this job description."
)


GATE_ICON = {"pass": "✅", "fail": "❌", "unverified": "❔"}


def render_gates(gates: list[GateOut], *, capped: bool, uncapped_score: int, gate_cap: int) -> None:
    """Hard filters, shown apart from the percentage because they are not one.

    A cap is stated before anything else about the gates: a screen that filters
    on a failed one never reaches the keywords, so a strong-looking breakdown
    beside it would be the most misleading thing on the page.
    """
    if not gates:
        return
    if capped:
        st.error(
            f"**Score capped at {gate_cap}%** — it would be {uncapped_score}% otherwise. "
            "The posting has a hard requirement your record falls short of, and a screen "
            "that filters on it rejects the application whatever else matches."
        )
    for gate in gates:
        line = f"{GATE_ICON.get(gate.status, '•')} **{gate.label}** — requires {gate.required}"
        if gate.found:
            line += f"; on record: {gate.found}"
        st.markdown(line)
        if gate.detail:
            st.caption(gate.detail)


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


RESUME_ATS_CAVEAT = (
    "Scored against **this resume** -- your profile plus the bullets that "
    "actually fit on the page -- not against everything you know. Project "
    "keywords in the bank are excluded, because an ATS only ever sees what is "
    "printed. Each requirement counts once, however often the posting repeats it."
)


def render_resume_ats(report: ResumeAtsResponse) -> None:
    """The resume-scoped ATS panel.

    Deliberately reports a different number from the ATS match check view, and
    says so: that view scores the candidate, this one scores the document. Two
    scores that look alike and mean different things is the one outcome worth
    spending a caption to prevent.
    """
    if report.requirement_count == 0:
        st.caption(
            "No recognisable requirements were found in this job description, "
            "so there is nothing to score against."
        )
        return

    columns = st.columns([1, 1, 2])
    columns[0].metric("ATS match · this resume", f"{report.score}%")
    columns[1].metric("Requirements", report.requirement_count)
    columns[2].metric("Band", report.band)
    st.progress(report.score / 100)
    st.caption(
        f"This resume covers {report.score}% of the requirements in the job description. "
        + RESUME_ATS_CAVEAT
    )
    # Said outright, because two similar-looking percentages in one app read as
    # a contradiction rather than as two measurements. Naming the other number
    # and what separates them is cheaper than letting the user discover it.
    st.caption(
        "**This is not the same number as the ATS match check view.** That one scores "
        "everything you know about yourself and answers *should I apply?*; this one scores "
        "the document you are about to send and answers *will this page pass a screen?* "
        "The document number is normally lower, and the gap is the list below."
    )

    render_gates(
        report.gates,
        capped=report.capped,
        uncapped_score=report.uncapped_score,
        gate_cap=report.gate_cap,
    )

    # The recoverable gaps come first. They are the only ones the user can
    # close from this screen, by choosing a different project.
    if report.covered_elsewhere:
        st.warning(
            "**On your record but not on this resume ("
            f"{len(report.covered_elsewhere)}).** A different project selection would "
            "cover these:\n\n" + " ".join(f"`{term}`" for term in report.covered_elsewhere),
            icon="💡",
        )

    unrecoverable = [
        term for term in report.missing_requirements if term not in set(report.covered_elsewhere)
    ]
    if unrecoverable:
        st.error(
            f"**Missing everywhere ({len(unrecoverable)}).** Nothing in your project bank, "
            "profile or knowledge covers these:\n\n"
            + " ".join(f"`{term}`" for term in unrecoverable)
        )
    if report.weak_requirements:
        st.caption(
            "Matched only by a related term: "
            + " ".join(f"`{term}`" for term in report.weak_requirements)
        )
    if not report.missing_requirements and not report.weak_requirements:
        st.success("Every requirement this check found is covered by this resume.")

    with st.expander("Breakdown by factor", expanded=False):
        for category in report.breakdown:
            st.markdown(
                f"**{category.label}** — {category.score * 100:.0f}% "
                f"(weight {category.weight:g}) · {category.exact_count} exact / "
                f"{category.related_count} related / {category.missing_count} missing"
            )
