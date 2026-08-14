"""Resume preview, generation and download.

Generation is split into two requests on purpose: ``POST /resume/generate``
returns the metadata a client needs to make a decision (page count, whether it
fits, which font size was needed, any warning), and ``GET /resume/{id}``
streams the bytes. The original returned a base64 blob inside the JSON body,
which inflated every response by a third and meant the warning a user most
needed to read arrived wrapped around a megabyte of encoded PDF.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from resume_tailor.api.deps import ApiKeyDep, ServiceDep
from resume_tailor.api.schemas import GenerateResponse, PreviewResponse, ResumeRequest
from resume_tailor.domain.models import ResumeSpec
from resume_tailor.services.resume_service import ResumeService

router = APIRouter(tags=["resume"])


def _build_spec(payload: ResumeRequest, service: ResumeService) -> ResumeSpec:
    return service.build_spec(
        payload.selected_project_keys,
        max_pages=payload.max_pages,
        summary=payload.summary,
        personal_info=(
            payload.personal_info.model_dump(exclude_none=True) if payload.personal_info else None
        ),
    )


@router.post(
    "/resume/preview",
    response_model=PreviewResponse,
    summary="Render the LaTeX source without compiling",
    responses={
        400: {"description": "Invalid selection or options."},
        422: {"description": "Content rejected by the LaTeX safety audit."},
    },
)
def preview(payload: ResumeRequest, service: ServiceDep, _: ApiKeyDep = None) -> PreviewResponse:
    """Fast feedback that needs no PDF engine at all.

    Useful on a machine with no TeX toolchain, and useful generally: rendering
    is milliseconds where compiling is seconds.
    """
    spec = _build_spec(payload, service)
    tex, warnings = service.render_preview(spec)
    return PreviewResponse(
        tex=tex,
        warnings=warnings,
        project_keys=spec.project_keys,
        bank_version=spec.bank_version,
        character_count=len(tex),
    )


@router.post(
    "/resume/generate",
    response_model=GenerateResponse,
    summary="Compile a resume PDF with the page-fit guarantee",
    responses={
        400: {"description": "Invalid selection or options."},
        422: {"description": "LaTeX compilation failed, or content was rejected."},
        503: {"description": "No PDF engine available."},
        504: {"description": "Compilation timed out."},
    },
)
async def generate(
    payload: ResumeRequest, service: ServiceDep, _: ApiKeyDep = None
) -> GenerateResponse:
    """Compile, trying successively smaller fonts until the page limit is met.

    Always returns a PDF. If the content could not be made to fit even at the
    smallest font, ``fits`` is false and ``warning`` explains why -- the caller
    is never handed an over-length resume without being told.
    """
    spec = _build_spec(payload, service)
    result = await service.generate(spec)
    fit = result.fit

    return GenerateResponse(
        document_id=result.document.document_id,
        download_url=f"/api/v1/resume/{result.document.document_id}",
        filename=result.document.filename,
        page_count=fit.page_count,
        max_pages=payload.max_pages,
        fits=fit.fits,
        font_size_used=fit.font_size,
        line_spacing_used=fit.line_spacing,
        compile_attempts=fit.attempts,
        engine=fit.engine,
        warning=fit.warning,
        source_warnings=list(fit.source_warnings),
        bank_version=result.bank_version,
    )


@router.get(
    "/resume/{document_id}",
    summary="Download a generated PDF",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The PDF bytes."},
        404: {"description": "Unknown or expired document id."},
    },
)
def download(document_id: str, service: ServiceDep, _: ApiKeyDep = None) -> Response:
    document = service.documents.get(document_id)
    return Response(
        content=document.pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{document.filename}"',
            "Content-Length": str(len(document.pdf_bytes)),
            "X-Page-Count": str(document.page_count),
        },
    )
