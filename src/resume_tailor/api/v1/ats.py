"""ATS match check (feature 2).

One endpoint, one input, no side effects. It does not import the renderer, the
templates or the PDF engine, and there is no document to download afterwards --
the independence from resume generation is structural, not a convention the
caller has to respect.

``note`` and ``weights`` are part of the contract rather than decoration. A
score with no published methodology is a number someone will over-trust; with
the weights in hand the reader can recompute it from the breakdown by hand.
"""

from __future__ import annotations

from fastapi import APIRouter

from resume_tailor.api.deps import ApiKeyDep, ServiceDep
from resume_tailor.api.schemas import (
    AtsRequest,
    AtsResponse,
    CategoryBreakdownOut,
    GateOut,
    RequirementOut,
)
from resume_tailor.api.v1.match import posting_out
from resume_tailor.domain.ats import AtsReport, CategoryBreakdown

router = APIRouter(tags=["ats"])


def breakdown_out(category: CategoryBreakdown) -> CategoryBreakdownOut:
    """Map one scored category to the wire model.

    Shared with ``/resume/ats``, which reports the same breakdown for a
    different candidate side. Two copies of this mapping would eventually
    disagree about what a category contains, and the disagreement would show up
    as two endpoints describing the same JD differently.
    """
    return CategoryBreakdownOut(
        name=category.name,
        label=category.label,
        weight=category.weight,
        score=category.score,
        exact_count=len(category.exact),
        related_count=len(category.related),
        missing_count=len(category.missing),
        preferred_count=len(category.preferred),
        requirements=[
            RequirementOut(
                term=requirement.term,
                display=requirement.display,
                status=requirement.status,
                matched_term=requirement.matched_term,
                matched_sources=requirement.matched_sources,
                occurrences=requirement.occurrences,
                detail=requirement.detail,
                priority=requirement.priority,
            )
            for requirement in category.requirements
        ],
    )


def gates_out(report: AtsReport) -> list[GateOut]:
    """Map the hard filters to the wire model. Shared with ``/resume/ats`` for
    the same reason as :func:`breakdown_out`."""
    return [
        GateOut(
            name=gate.name,
            label=gate.label,
            status=gate.status,
            required=gate.required,
            found=gate.found,
            detail=gate.detail,
        )
        for gate in report.gates
    ]


@router.post(
    "/ats/check",
    response_model=AtsResponse,
    summary="Score a job description against stored candidate knowledge",
    responses={
        400: {
            "description": (
                "Empty or oversized job description, or an empty knowledge base "
                "with nothing to match against."
            )
        }
    },
)
def check(payload: AtsRequest, service: ServiceDep, _: ApiKeyDep = None) -> AtsResponse:
    report = service.ats_check(payload.jd_text)
    return AtsResponse(
        score=report.score,
        band=report.band,
        breakdown=[breakdown_out(category) for category in report.breakdown],
        missing_requirements=report.missing_requirements,
        weak_requirements=report.weak_requirements,
        matched_requirements=report.matched_requirements,
        requirement_count=report.requirement_count,
        weights=report.weights,
        priority_weights=report.priority_weights,
        gates=gates_out(report),
        uncapped_score=report.uncapped_score,
        capped=report.capped,
        gate_cap=report.gate_cap,
        knowledge_version=report.knowledge_version,
        bank_version=report.bank_version,
        posting=posting_out(service.posting_check(payload.jd_text)),
        note=report.note,
    )
