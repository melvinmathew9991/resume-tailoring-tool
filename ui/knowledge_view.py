"""Profile knowledge view (feature 1).

Its own module rather than another branch inside ``app.py``: the two features
added here are independent of the tailoring flow and of each other, and keeping
each view's script in its own file is what makes that true in the code and not
just in the documentation.
"""

from __future__ import annotations

import streamlit as st

from resume_tailor.api.schemas import KnowledgeResponse, KnowledgeUpdateResponse
from ui import components
from ui.client import BackendClient, BackendError
from ui.state import AppState

#: What the uploader accepts. Kept in step with
#: ``resume_tailor.domain.extraction.SUPPORTED_EXTENSIONS`` -- Streamlit wants
#: them without the leading dot.
UPLOAD_TYPES = ["pdf", "docx", "txt", "md", "markdown"]

CATEGORY_LABELS = {
    "skill": "Skills",
    "tool": "Tools & technologies",
    "domain": "Domains",
    "responsibility": "Responsibilities",
    "certification": "Certifications",
    "education": "Education",
}

INTRO = (
    "What the system knows about you. This is **not** your resume: nothing here "
    "is ever printed onto a generated PDF. It is the candidate side of the ATS "
    "match check, and it is built only from documents you add here -- every "
    "entry keeps the line it came from, and nothing is inferred."
)


def render(client: BackendClient, state: AppState) -> None:
    st.title("Profile knowledge")
    st.caption(INTRO)

    # Fetched on every render rather than cached in session state. The store is
    # written by this app, by `tasks.py knowledge`, and potentially by another
    # session; a cached copy meant the view could keep showing counts that the
    # file on disk had not agreed with for some time -- and "the app did not
    # pick up my upload" is exactly what that looks like from the outside. The
    # read is a repository hit behind an mtime cache in embedded mode, and one
    # GET in http mode, on a page that already calls `readiness()` per rerun.
    try:
        state.knowledge = client.knowledge()
    except BackendError as exc:
        components.render_error(exc)
        st.stop()

    # `st.stop()` above raises, so state.knowledge is set by here.
    knowledge = state.knowledge

    _render_summary(knowledge)
    _render_entries(knowledge)
    _render_add_form(client, state)

    if state.knowledge_message:
        st.success(state.knowledge_message)
        # Shown once, then cleared. Session state survives every rerun, so a
        # message left in place would re-announce "Added 3 new entries" after a
        # later failed save, after a delete, and every time the user switched
        # back to this view -- asserting a change that did not just happen.
        state.knowledge_message = ""


def _render_summary(knowledge: KnowledgeResponse) -> None:
    st.header("1 · What is stored")
    if knowledge.is_empty:
        st.info(
            "Nothing yet. Add a resume, a project write-up or any profile document "
            "below -- the ATS match check needs this to compare a job description "
            "against."
        )
        return

    if not knowledge.warnings:
        st.success(
            f"Store looks healthy: {knowledge.entry_count} entries from "
            f"{len(knowledge.sources)} document(s), version `{knowledge.version}`."
        )

    # Warnings before the counts. A store can be structurally valid and still
    # obviously wrong, and the count on its own reads as reassurance -- 2,000
    # entries looks thorough and is in fact the signature of a document whose
    # prose was read as a skills list.
    for warning in knowledge.warnings:
        st.warning(warning, icon="⚠️")

    columns = st.columns(4)
    columns[0].metric("Entries", knowledge.entry_count)
    columns[1].metric("Documents", len(knowledge.sources))
    years, months = divmod(knowledge.experience_months, 12)
    columns[2].metric("Dated experience", f"{years}y {months}m")
    columns[3].metric("Store version", knowledge.version)


def _render_entries(knowledge: KnowledgeResponse) -> None:
    if knowledge.is_empty:
        return

    for category, count in knowledge.counts_by_category.items():
        label = CATEGORY_LABELS.get(category, category.title())
        with st.expander(f"{label} ({count})", expanded=False):
            for entry in knowledge.entries:
                if entry.category != category:
                    continue
                st.markdown(f"`{entry.display}`")
                if entry.evidence:
                    # The evidence line is shown, not hidden behind a tooltip.
                    # It is the whole basis for trusting that an entry was read
                    # rather than invented.
                    st.caption(f"from: {entry.evidence[:200]}")

    if knowledge.experience:
        with st.expander(f"Experience ({len(knowledge.experience)})", expanded=False):
            for fact in knowledge.experience:
                where = f" · {fact.organisation}" if fact.organisation else ""
                st.markdown(f"**{fact.title}**{where}")
                st.caption(f"{fact.dates} — {fact.months} month(s)")

    with st.expander(f"Documents ({len(knowledge.sources)})", expanded=False):
        for source in knowledge.sources:
            st.caption(
                f"`{source.label}` · {source.kind} · {source.characters:,} characters "
                f"· added {source.added_at}"
            )


def _render_add_form(client: BackendClient, state: AppState) -> None:
    st.header("2 · Add or update")

    method = st.radio(
        "How do you want to add it?",
        ("Upload a document", "Paste text"),
        horizontal=True,
        key="knowledge_method",
    )

    mode_label = st.radio(
        "Update mode",
        (
            "Merge — keep everything already stored",
            "Update — swap in a new version of one document",
            "Replace — delete everything first",
        ),
        key="knowledge_mode",
    )
    mode = {"Merge": "merge", "Update": "supersede", "Replace": "replace"}[
        mode_label.split(" ", 1)[0]
    ]
    if mode == "merge" and state.knowledge is not None and state.knowledge.warnings:
        st.info(
            "Your stored knowledge has warnings above. **Merge cannot fix them** -- it only "
            "adds what is new, so re-uploading the same document changes nothing. Choose "
            "Update to swap in a corrected version of one document, or Replace to rebuild "
            "the store from scratch.",
            icon="💡",
        )
    if mode == "supersede":
        # Said before the click, because the match is by name and a renamed
        # file is refused rather than guessed at.
        st.info(
            "Update finds the earlier version by name -- the filename for an upload, "
            '"pasted text" for a paste -- removes everything it contributed, and adds this '
            "version in its place. Every other document is kept. The file must have the "
            "same name as the one it replaces.",
            icon="🔄",
        )
    if mode == "replace":
        st.error(
            "Replace deletes every entry, every experience item and every document "
            "record now stored, and rebuilds the knowledge base from this one input.",
            icon="⚠️",
        )

    upload = None
    if method == "Upload a document":
        upload = st.file_uploader(
            "Resume, profile or project document",
            type=UPLOAD_TYPES,
            help="PDF, DOCX, TXT or MD. Legacy .doc is not readable -- save it as "
            ".docx or .pdf first, or paste the text.",
            key="knowledge_upload",
        )
    else:
        state.knowledge_paste = st.text_area(
            "Paste your profile, resume or project details",
            value=state.knowledge_paste,
            height=240,
            placeholder="Skills\nPython, SQL, Airflow\n\nExperience\nData Scientist at ...",
            key="knowledge_paste_box",
        )

    # Validated on click rather than through `disabled=`: a text area does not
    # commit its value until it loses focus, so disabling the button on the
    # box's contents leaves it greyed out after a paste. The same reasoning as
    # the Analyse button in the tailoring view.
    if not st.button("Save to knowledge base", type="primary"):
        return

    try:
        if method == "Upload a document":
            if upload is None:
                st.warning("Choose a file first.")
                return
            response = client.update_knowledge(
                document=upload.getvalue(), filename=upload.name, mode=mode
            )
        else:
            if not state.knowledge_paste.strip():
                st.warning("Paste some text first.")
                return
            response = client.update_knowledge(
                text=state.knowledge_paste, label="pasted text", mode=mode
            )
    except BackendError as exc:
        components.render_error(exc)
        return

    state.knowledge = response.knowledge
    # Both ATS reports were computed against the previous store, so they are now
    # stale by definition. Dropping them is what stops the user comparing a
    # fresh knowledge base against a score that predates it -- which reads as
    # the two features disagreeing.
    state.ats = None
    state.resume_ats = None
    # Built into a string and re-run rather than rendered here: the summary and
    # the entry list at the top of the page were drawn before this update
    # happened, so without the re-run the counts on screen would contradict the
    # message directly beneath them.
    state.knowledge_message = _update_detail(response)
    st.rerun()


def _update_detail(response: KnowledgeUpdateResponse) -> str:
    """What changed, per category, as one sentence plus the counts.

    A silent success on a re-upload reads as a broken button, so this is
    produced even when every count is zero.
    """
    parts = [
        response.message,
        f"Read {response.characters_read:,} characters from {response.source_label} "
        f"({response.source_kind}), mode {response.mode}.",
    ]
    if response.added_by_category:
        parts.append(
            "New by category: "
            + ", ".join(
                f"{CATEGORY_LABELS.get(name, name)} +{count}"
                for name, count in sorted(response.added_by_category.items())
            )
        )
    return " ".join(parts)
