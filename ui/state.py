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

from resume_tailor.api.schemas import GenerateResponse, MatchResponse, MetaResponse

STATE_KEY = "resume_tailor_state"


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
    pdf_filename: str = "resume.pdf"
    generating: bool = False
    """Guards the Generate button. Without it, a double-click queues a second
    compile behind the first -- two multi-second TeX runs for one intent."""
    bank_version: str = ""
    error: str = ""


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
