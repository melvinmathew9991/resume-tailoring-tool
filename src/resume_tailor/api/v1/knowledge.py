"""Candidate knowledge (feature 1).

Reading, updating and removing what the system knows about the candidate. This
router never touches ``profile.yaml`` or ``project_bank.json``: those hold
pre-verified text that a resume may print, and letting an uploaded document
write into them would put unchecked prose one click away from a PDF.
"""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Query

from resume_tailor.api.deps import ApiKeyDep, ServiceDep
from resume_tailor.api.schemas import (
    ExperienceFactOut,
    KnowledgeDeleteResponse,
    KnowledgeEntryOut,
    KnowledgeResponse,
    KnowledgeSourceOut,
    KnowledgeUpdateRequest,
    KnowledgeUpdateResponse,
)
from resume_tailor.core.errors import ExtractionError
from resume_tailor.domain.knowledge import CATEGORIES, KnowledgeBase, lint_knowledge
from resume_tailor.services.resume_service import KnowledgeUpdateResult

router = APIRouter(tags=["knowledge"])


def _knowledge_out(knowledge: KnowledgeBase) -> KnowledgeResponse:
    grouped = knowledge.by_category()
    return KnowledgeResponse(
        entries=[
            KnowledgeEntryOut(
                category=entry.category,
                value=entry.value,
                display=entry.display,
                evidence=entry.evidence,
                source_id=entry.source_id,
            )
            for entry in knowledge.entries
        ],
        experience=[
            ExperienceFactOut(
                title=fact.title,
                organisation=fact.organisation,
                dates=fact.dates,
                months=fact.months,
                evidence=fact.evidence,
                source_id=fact.source_id,
            )
            for fact in knowledge.experience
        ],
        sources=[
            KnowledgeSourceOut(
                source_id=source.source_id,
                label=source.label,
                kind=source.kind,
                characters=source.characters,
                added_at=source.added_at,
            )
            for source in knowledge.sources
        ],
        counts_by_category={name: len(entries) for name, entries in grouped.items()},
        entry_count=len(knowledge.entries),
        experience_months=knowledge.total_experience_months(),
        version=knowledge.version,
        is_empty=knowledge.is_empty,
        warnings=lint_knowledge(knowledge),
    )


def _summarise(result: KnowledgeUpdateResult) -> str:
    """A sentence the UI can show without recomputing anything.

    Written here rather than in the UI because the API is the thing that knows
    whether "0 added" means "you have uploaded this before" or "this document
    contained nothing recognisable", and those need different next steps.
    """
    stats = result.stats
    if result.mode == "supersede":
        # Removals lead, because they are what this mode exists for and the
        # one thing merge could never report.
        return (
            f"Updated {result.document.label}: removed {stats.removed_entries} "
            f"entr{'y' if stats.removed_entries == 1 else 'ies'} and "
            f"{stats.removed_experience} experience item(s) the new version no longer "
            f"contains, added {stats.added_entries} new "
            f"entr{'y' if stats.added_entries == 1 else 'ies'} and "
            f"{stats.added_experience} experience item(s); "
            f"{stats.duplicate_entries} were unchanged."
        )
    if stats.added_entries or stats.added_experience:
        return (
            f"Added {stats.added_entries} new entr{'y' if stats.added_entries == 1 else 'ies'} "
            f"and {stats.added_experience} experience item(s) from {result.document.label}; "
            f"{stats.duplicate_entries} were already known."
        )
    if stats.source_already_known:
        # Naming Replace here is the difference between a dead end and a next
        # step. Merge is additive, so re-uploading a document to *fix* a bad
        # store does exactly nothing -- every junk entry is already known, so
        # nothing is added and nothing is removed. Observed in the field: the
        # file was rewritten byte-identical and the user reasonably concluded
        # the app had ignored the upload.
        return (
            f"{result.document.label} has been added before and contained nothing new, so "
            "your knowledge base is unchanged. Merge only ever adds -- to rebuild the store "
            "from this document and discard what is there now, choose Replace."
        )
    if stats.duplicate_entries:
        return (
            f"Nothing new was found in {result.document.label}; all "
            f"{stats.duplicate_entries} extracted item(s) were already known."
        )
    # A new document that yielded nothing at all. Telling this user their
    # content "was already known" would be false and would point them at the
    # wrong fix -- the text was read, and nothing in it was recognisable.
    return (
        f"{result.document.label} was read ({result.document.characters:,} characters) but no "
        "skills, tools, education or dated experience could be recognised in it. Check that it "
        "has the detail you expected, or paste the relevant section directly."
    )


def build_update_response(result: KnowledgeUpdateResult) -> KnowledgeUpdateResponse:
    """Map a service result to the wire model.

    Public, and called by the embedded UI client as well as by the route below.
    Embedded mode cannot go through the HTTP handler without base64-encoding
    bytes it already holds, so it calls the service directly -- but it must not
    grow a second copy of this mapping, or the UI and the API would eventually
    disagree about what an update did.
    """
    stats = result.stats
    return KnowledgeUpdateResponse(
        knowledge=_knowledge_out(result.knowledge),
        mode=result.mode,
        source_label=result.document.label,
        source_kind=result.document.kind,
        characters_read=result.document.characters,
        added_entries=stats.added_entries,
        duplicate_entries=stats.duplicate_entries,
        added_experience=stats.added_experience,
        duplicate_experience=stats.duplicate_experience,
        added_by_category=dict(stats.added_by_category),
        removed_entries=stats.removed_entries,
        removed_experience=stats.removed_experience,
        superseded_sources=stats.superseded_sources,
        source_already_known=stats.source_already_known,
        message=_summarise(result),
    )


def build_knowledge_response(knowledge: KnowledgeBase) -> KnowledgeResponse:
    """The read-side mapping, shared with the embedded client for the same reason."""
    return _knowledge_out(knowledge)


@router.get(
    "/knowledge",
    response_model=KnowledgeResponse,
    summary="Everything the system knows about the candidate",
)
def get_knowledge(service: ServiceDep, _: ApiKeyDep = None) -> KnowledgeResponse:
    return _knowledge_out(service.knowledge())


@router.post(
    "/knowledge",
    response_model=KnowledgeUpdateResponse,
    summary="Add candidate knowledge from a document or pasted text",
    responses={
        400: {"description": "Empty, oversized or contradictory input."},
        422: {"description": "The document could not be read (see extraction_failed)."},
    },
)
def update_knowledge(
    payload: KnowledgeUpdateRequest,
    service: ServiceDep,
    _: ApiKeyDep = None,
) -> KnowledgeUpdateResponse:
    document: bytes | None = None
    if payload.document_b64 is not None:
        try:
            # validate=True so stray characters are an error rather than being
            # silently dropped into a corrupt file that fails later with a much
            # less useful message.
            document = base64.b64decode(payload.document_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ExtractionError(f"document_b64 is not valid base64: {exc}") from exc

    return build_update_response(
        service.update_knowledge(
            text=payload.text,
            document=document,
            filename=payload.filename,
            label=payload.label,
            mode=payload.mode,
        )
    )


@router.delete(
    "/knowledge/entries",
    response_model=KnowledgeDeleteResponse,
    summary="Remove one knowledge entry",
    responses={400: {"description": "Missing category or value."}},
)
def delete_knowledge_entry(
    service: ServiceDep,
    category: str = Query(description=f"One of: {', '.join(CATEGORIES)}"),
    value: str = Query(description="The entry's normalised value, as returned by GET."),
    _: ApiKeyDep = None,
) -> KnowledgeDeleteResponse:
    """Removal is one entry at a time, by name.

    ``removed`` is reported rather than assumed: asking to delete something the
    store never held is answered honestly with a zero instead of a cheerful
    success that leaves the caller believing the store changed.
    """
    knowledge, removed = service.remove_knowledge_entry(category, value)
    return KnowledgeDeleteResponse(knowledge=_knowledge_out(knowledge), removed=removed)


@router.delete(
    "/knowledge",
    response_model=KnowledgeResponse,
    summary="Empty the candidate knowledge store",
)
def clear_knowledge(service: ServiceDep, _: ApiKeyDep = None) -> KnowledgeResponse:
    """The only wholesale delete, and it is a separate verb on a separate path
    so it cannot be reached by getting a query parameter wrong."""
    return _knowledge_out(service.clear_knowledge())
