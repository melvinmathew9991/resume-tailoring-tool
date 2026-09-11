"""Typed session state.

Streamlit re-runs the whole script top to bottom on every interaction, so
anything that must survive a click lives in ``st.session_state``. Keeping the
keys in one dataclass instead of scattering string literals through the app is
what stops the classic Streamlit bug where a typo'd key silently creates a
second, empty piece of state that shadows the real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import streamlit as st

from resume_tailor.api.schemas import (
    AtsResponse,
    GenerateResponse,
    KnowledgeResponse,
    MatchResponse,
    MetaResponse,
    ResumeAtsResponse,
)

STATE_KEY = "resume_tailor_state"

#: The three top-level features, in sidebar order. The tailoring flow is first
#: because it is what the tool is for; the other two are independent of it and
#: of each other.
TAILOR_VIEW = "Tailor resume"
KNOWLEDGE_VIEW = "Profile knowledge"
ATS_VIEW = "ATS match check"
VIEWS = (TAILOR_VIEW, KNOWLEDGE_VIEW, ATS_VIEW)


@dataclass
class AppState:
    jd_text: str = ""
    match: MatchResponse | None = None
    meta: MetaResponse | None = None
    selected_keys: list[str] = field(default_factory=list)
    summary_override: str = ""
    max_pages: int = 2
    result: GenerateResponse | None = None
    pdf_bytes: bytes | None = None
    """The filename deliberately does not live here. It comes from
    ``result.filename``, which the backend derives from the profile name, and a
    second copy in session state would be one more thing to keep in sync -- the
    stale one would silently name the download ``resume.pdf``."""
    resume_ats: ResumeAtsResponse | None = None
    """How the *assembled resume* scores, recomputed on every rerun because it
    is pure text matching and costs nothing. Distinct from ``ats``, which is
    the standalone check against the whole knowledge base -- the two answer
    different questions and must not share a slot."""
    generating: bool = False
    """Guards the Generate button. Without it, a double-click queues a second
    compile behind the first -- two multi-second TeX runs for one intent."""
    bank_version: str = ""
    error: str = ""

    # -- feature 1: candidate knowledge -------------------------------------
    knowledge: KnowledgeResponse | None = None
    """Cached so the view can render without a fetch on every rerun. Replaced
    wholesale by the response to an update, never patched in place -- a locally
    edited copy would be a second source of truth for the same store."""
    knowledge_paste: str = ""
    knowledge_message: str = ""

    # -- feature 2: ats match check -----------------------------------------
    ats_jd_text: str = ""
    """Kept separate from ``jd_text``. The two features are independent, and
    sharing one box would mean checking a job description silently reset the
    tailoring flow's match results."""
    ats: AtsResponse | None = None


def get_state() -> AppState:
    if STATE_KEY not in st.session_state:
        st.session_state[STATE_KEY] = AppState()
    state: AppState = st.session_state[STATE_KEY]
    return state


def reset_results(state: AppState) -> None:
    """Clear anything derived from a previous generation.

    Called whenever the inputs change, so a stale PDF from the previous
    selection can never sit on screen next to a new one -- the user download
    would silently be the wrong document.
    """
    state.result = None
    state.pdf_bytes = None
    state.error = ""


def sync_bank_version(state: AppState, bank_version: str) -> bool:
    """Detect the project bank changing underneath an open session.

    Returns True when it changed. The bank is a hand-edited file the user is
    encouraged to edit; a session holding match results from the previous
    version is showing stale scores.
    """
    if state.bank_version and state.bank_version != bank_version:
        state.bank_version = bank_version
        return True
    state.bank_version = bank_version
    return False


def as_dict(state: AppState) -> dict[str, Any]:
    """Debug view, used by the diagnostics expander."""
    return {
        "jd_chars": len(state.jd_text),
        "selected": state.selected_keys,
        "max_pages": state.max_pages,
        "has_result": state.result is not None,
        "bank_version": state.bank_version,
    }
