"""ATS match check view (feature 2).

Paste a job description, get a score and a gap list. Nothing on this page can
generate, preview or download a resume -- the view has no access to a project
selection and the client method it calls returns no document.
"""

from __future__ import annotations

import streamlit as st

from resume_tailor.api.schemas import AtsResponse, CategoryBreakdownOut
from ui import components
from ui.client import BackendClient, BackendError
from ui.state import AppState

INTRO = (
    "How well does a job description line up with what the system knows about "
    "you? This does not write or change a resume. It reads the requirements out "
    "of the posting, compares them with your **Profile knowledge**, and shows "
    "the score, the breakdown and the gaps."
)

STATUS_ICON = {"exact": "✅", "related": "🟡", "missing": "❌"}


def render(client: BackendClient, state: AppState) -> None:
    st.title("ATS match check")
    st.caption(INTRO)

    st.header("1 · Paste the job description")
    state.ats_jd_text = st.text_area(
        "Job description",
        value=state.ats_jd_text,
        height=240,
        placeholder="Paste the full job description here...",
        label_visibility="collapsed",
        key="ats_jd_box",
    )

    left, right = st.columns([1, 4])
    if left.button("Check match", type="primary", key="ats_check_button"):
        if not state.ats_jd_text.strip():
            st.warning("Paste a job description first.")
        else:
            try:
                state.ats = client.ats_check(state.ats_jd_text)
            except BackendError as exc:
                state.ats = None
                components.render_error(exc)
    right.caption(f"{len(state.ats_jd_text):,} characters")

    if state.ats is None:
        st.stop()

    _render_score(state.ats)
    components.render_gates(
        state.ats.gates,
        capped=state.ats.capped,
        uncapped_score=state.ats.uncapped_score,
        gate_cap=state.ats.gate_cap,
    )
    _render_gaps(state.ats)
    _render_breakdown(state.ats)
    st.caption(state.ats.note)
    st.caption(
        "**Not the same number as the one in Tailor resume.** This scores everything you "
        "know; that scores the bullets that fit on one page. Both are correct, and the "
        "difference is what a resume cannot carry."
    )


def _render_score(report: AtsResponse) -> None:
    st.header("2 · Score")
    columns = st.columns([1, 1, 2])
    columns[0].metric("ATS match · your profile", f"{report.score}%")
    columns[1].metric("Requirements found", report.requirement_count)
    columns[2].metric("Band", report.band)
    st.progress(report.score / 100)

    exact = len(report.matched_requirements)
    related = len(report.weak_requirements)
    missing = len(report.missing_requirements)
    st.caption(
        f"{STATUS_ICON['exact']} {exact} exact · {STATUS_ICON['related']} {related} related "
        f"· {STATUS_ICON['missing']} {missing} missing. Each requirement counts once, "
        "however often the posting repeats it."
    )


def _render_gaps(report: AtsResponse) -> None:
    """Gaps first, above the detail. They are the actionable half of the report."""
    st.header("3 · Gaps")
    if not report.missing_requirements and not report.weak_requirements:
        st.success("Every requirement this check found has a match in your knowledge base.")
        return

    preferred = _preferred_terms(report)
    required_missing = [t for t in report.missing_requirements if t not in preferred]
    preferred_missing = [t for t in report.missing_requirements if t in preferred]
    if required_missing:
        st.error(f"**Missing ({len(required_missing)})**")
        st.markdown(" ".join(f"`{term}`" for term in required_missing))
    if preferred_missing:
        # Shown apart from the required gaps because it is weighed apart. A
        # nice-to-have gap listed beside a hard requirement reads as equally
        # urgent, and the score has already said it is not.
        weight = report.priority_weights.get("preferred", 1.0)
        st.info(
            f"**Missing, but only nice-to-have ({len(preferred_missing)})** — each counts "
            f"{weight:g} times as much as a required gap."
        )
        st.markdown(" ".join(f"`{term}`" for term in preferred_missing))
    if report.weak_requirements:
        st.warning(f"**Weak — matched only by a related term ({len(report.weak_requirements)})**")
        st.markdown(" ".join(f"`{term}`" for term in report.weak_requirements))
    st.caption(
        "A gap here means the term is absent from your stored knowledge, which is "
        "not the same as being absent from your experience. If you have it, add "
        "the document that says so under Profile knowledge."
    )


def _render_breakdown(report: AtsResponse) -> None:
    st.header("4 · Breakdown")
    st.caption(
        "Weights are fixed and published so the score can be recomputed by hand: "
        + ", ".join(f"{name} {weight:g}" for name, weight in report.weights.items())
        + ". Categories the posting never mentions are omitted and their weight is "
        "shared out over the rest. A requirement the posting marks as preferred counts "
        f"{report.priority_weights.get('preferred', 1.0):g} times a required one, and a category "
        "it lists only as preferred shrinks by the same factor."
    )

    for category in report.breakdown:
        with st.expander(_category_heading(category), expanded=False):
            for requirement in category.requirements:
                icon = STATUS_ICON[requirement.status]
                tag = " · *nice to have*" if requirement.priority == "preferred" else ""
                st.markdown(f"{icon} `{requirement.display}`{tag}")
                detail = requirement.detail
                if requirement.matched_term and requirement.matched_sources:
                    where = ", ".join(requirement.matched_sources)
                    detail = detail or f"matched `{requirement.matched_term}`"
                    detail = f"{detail} — found in: {where}"
                if requirement.occurrences > 1:
                    detail = f"{detail} · mentioned {requirement.occurrences} times (not scored)"
                if detail:
                    st.caption(detail)


def _preferred_terms(report: AtsResponse) -> set[str]:
    """Display strings of every requirement the posting marked as nice-to-have."""
    return {
        requirement.display
        for category in report.breakdown
        for requirement in category.requirements
        if requirement.priority == "preferred"
    }


def _category_heading(category: CategoryBreakdownOut) -> str:
    return (
        f"{category.label} — {category.score * 100:.0f}% "
        f"(weight {category.weight:g}) · "
        f"{category.exact_count} exact / {category.related_count} related / "
        f"{category.missing_count} missing"
    )
