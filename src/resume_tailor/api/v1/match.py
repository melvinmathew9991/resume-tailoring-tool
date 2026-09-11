"""JD matching.

The ``note`` field is part of the contract, not decoration. This endpoint ranks
by literal keyword overlap; a project can score zero and still belong on the
resume because the JD simply uses different vocabulary. Saying so in the
response is what stops the score being read as a verdict.
"""

from __future__ import annotations

from fastapi import APIRouter

from resume_tailor.api.deps import ApiKeyDep, ServiceDep
from resume_tailor.api.schemas import (
    KeywordHitOut,
    MatchRequest,
    MatchResponse,
    MatchResultOut,
    PostingCheckOut,
    PostingSignalOut,
)
from resume_tailor.domain.posting import PostingCheck

router = APIRouter(tags=["match"])


def posting_out(check: PostingCheck) -> PostingCheckOut:
    """Map the completeness check to the wire model.

    Shared with ``/ats/check``, which asks the same question of the same input.
    Two copies would eventually disagree about whether a posting is complete,
    and the disagreement would surface as one endpoint warning where the other
    stayed silent about the very same text.
    """
    return PostingCheckOut(
        complete=check.complete,
        characters=check.characters,
        words=check.words,
        has_requirements_section=check.has_requirements_section,
        signals=[
            PostingSignalOut(code=signal.code, detail=signal.detail) for signal in check.signals
        ],
        note=check.note,
    )


@router.post(
    "/match",
    response_model=MatchResponse,
    summary="Rank projects against a job description",
    responses={400: {"description": "Empty or oversized job description."}},
)
def match(payload: MatchRequest, service: ServiceDep, _: ApiKeyDep = None) -> MatchResponse:
    report = service.match(payload.jd_text, include_hidden=payload.include_hidden)
    return MatchResponse(
        ranked_projects=[
            MatchResultOut(
                key=result.key,
                title=result.title,
                score=result.score,
                coverage=result.coverage,
                matched_keywords=result.matched_keywords,
                keyword_hits=[
                    KeywordHitOut(keyword=hit.keyword, occurrences=hit.occurrences)
                    for hit in result.keyword_hits
                ],
                matched_domains=result.matched_domains,
                domain_match=result.domain_match,
            )
            for result in report.ranked_projects
        ],
        gap_terms=report.gap_terms,
        bank_version=report.bank_version,
        posting=posting_out(service.posting_check(payload.jd_text)),
        note=report.note,
    )
