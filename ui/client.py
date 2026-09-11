"""Backend client, in two interchangeable modes.

``http``
    Talks to a running FastAPI server. The real topology: two processes, a
    genuine network boundary, and the API contract exercised on every click.

``embedded``
    Calls the service layer in-process. For a single-user local session where
    running two processes to tailor one resume is pure overhead.

Both modes return the *same* response models and raise the same
:class:`BackendError`, so no UI code anywhere branches on which one is active.
Selected with ``RT_UI_MODE``.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from resume_tailor.api.schemas import (
    AtsResponse,
    GenerateResponse,
    KnowledgeDeleteResponse,
    KnowledgeResponse,
    KnowledgeUpdateResponse,
    MatchResponse,
    MetaResponse,
    PreviewResponse,
    ProjectListResponse,
    ReadinessResponse,
    ResumeAtsRequest,
    ResumeAtsResponse,
    ResumeRequest,
)

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=5.0)
"""Generous read timeout: a real LaTeX compile walking the whole font ladder is
seconds to tens of seconds, and a UI that gives up early looks like a crash."""


class BackendError(RuntimeError):
    """A failure the UI can present to the user.

    Carries the machine-readable ``code`` from the API's problem document, so
    the UI branches on a stable identifier rather than on message text -- which
    is exactly what the old JavaScript frontend could not do.
    """

    def __init__(
        self, detail: str, *, code: str = "error", status: int = 0, title: str = ""
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status = status
        self.title = title or "Request failed"


@dataclass(frozen=True)
class DownloadedPdf:
    content: bytes
    filename: str


class BackendClient(Protocol):
    mode: str

    def readiness(self) -> ReadinessResponse: ...
    def meta(self) -> MetaResponse: ...
    def projects(self, include_hidden: bool = False) -> ProjectListResponse: ...
    def match(self, jd_text: str) -> MatchResponse: ...
    def preview(self, request: ResumeRequest) -> PreviewResponse: ...
    def generate(self, request: ResumeRequest) -> GenerateResponse: ...
    def download(self, document_id: str) -> DownloadedPdf: ...

    # Feature 1 -- candidate knowledge.
    def knowledge(self) -> KnowledgeResponse: ...
    def update_knowledge(
        self,
        *,
        text: str | None = None,
        document: bytes | None = None,
        filename: str | None = None,
        label: str | None = None,
        mode: str = "merge",
    ) -> KnowledgeUpdateResponse: ...
    def delete_knowledge_entry(self, category: str, value: str) -> KnowledgeDeleteResponse: ...
    def clear_knowledge(self) -> KnowledgeResponse: ...

    # Feature 2 -- ATS match check.
    def ats_check(self, jd_text: str) -> AtsResponse: ...

    # The resume-scoped score: what the assembled document covers, not what
    # the candidate covers. Part of the tailoring workflow, not of feature 2.
    def resume_ats(self, request: ResumeAtsRequest) -> ResumeAtsResponse: ...


# --- http mode --------------------------------------------------------------


class HttpBackendClient:
    mode = "http"

    def __init__(
        self, base_url: str, api_key: str | None = None, timeout: httpx.Timeout | None = None
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout or DEFAULT_TIMEOUT, headers=headers
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise BackendError(
                f"could not reach the API at {self._client.base_url}. Is it running? "
                f"Start it with `python tasks.py api`. ({exc.__class__.__name__})",
                code="unreachable",
            ) from exc

        if response.is_success:
            return response

        # Every API failure is an RFC 7807 problem document; fall back only if
        # something else (a proxy, say) answered instead.
        try:
            problem = response.json()
        except ValueError:
            problem = {}
        raise BackendError(
            problem.get("detail", response.text[:400] or "the API returned an error"),
            code=problem.get("code", "http_error"),
            status=response.status_code,
            title=problem.get("title", ""),
        )

    def readiness(self) -> ReadinessResponse:
        """Not-ready is an answer, not an error.

        ``/health/ready`` answers 503 when a dependency is unusable, and the
        body carries the detail the UI needs to *show* the user. Routing it
        through ``_request`` would turn that useful payload into an exception.
        """
        try:
            response = self._client.get("/health/ready")
        except httpx.RequestError as exc:
            raise BackendError(
                f"could not reach the API at {self._client.base_url}. Is it running? "
                "Start it with `python tasks.py api`.",
                code="unreachable",
            ) from exc
        return ReadinessResponse.model_validate(response.json())

    def meta(self) -> MetaResponse:
        return MetaResponse.model_validate(self._request("GET", "/api/v1/meta").json())

    def projects(self, include_hidden: bool = False) -> ProjectListResponse:
        response = self._request(
            "GET", "/api/v1/projects", params={"include_hidden": include_hidden}
        )
        return ProjectListResponse.model_validate(response.json())

    def match(self, jd_text: str) -> MatchResponse:
        response = self._request("POST", "/api/v1/match", json={"jd_text": jd_text})
        return MatchResponse.model_validate(response.json())

    def preview(self, request: ResumeRequest) -> PreviewResponse:
        response = self._request(
            "POST", "/api/v1/resume/preview", json=request.model_dump(exclude_none=True)
        )
        return PreviewResponse.model_validate(response.json())

    def generate(self, request: ResumeRequest) -> GenerateResponse:
        response = self._request(
            "POST", "/api/v1/resume/generate", json=request.model_dump(exclude_none=True)
        )
        return GenerateResponse.model_validate(response.json())

    def knowledge(self) -> KnowledgeResponse:
        return KnowledgeResponse.model_validate(self._request("GET", "/api/v1/knowledge").json())

    def update_knowledge(
        self,
        *,
        text: str | None = None,
        document: bytes | None = None,
        filename: str | None = None,
        label: str | None = None,
        mode: str = "merge",
    ) -> KnowledgeUpdateResponse:
        # base64 rather than multipart: see KnowledgeUpdateRequest for why the
        # API takes the document in the JSON body at all.
        body: dict[str, Any] = {"mode": mode}
        if document is not None:
            body["document_b64"] = base64.b64encode(document).decode("ascii")
            # Passed through empty rather than defaulted to a placeholder. The
            # extension selects the reader, so an invented "upload" guarantees
            # a confusing "unsupported file type" instead of the API's own
            # "filename is required with document_b64".
            body["filename"] = filename or ""
        else:
            body["text"] = text
            if label:
                body["label"] = label
        response = self._request("POST", "/api/v1/knowledge", json=body)
        return KnowledgeUpdateResponse.model_validate(response.json())

    def delete_knowledge_entry(self, category: str, value: str) -> KnowledgeDeleteResponse:
        response = self._request(
            "DELETE", "/api/v1/knowledge/entries", params={"category": category, "value": value}
        )
        return KnowledgeDeleteResponse.model_validate(response.json())

    def clear_knowledge(self) -> KnowledgeResponse:
        return KnowledgeResponse.model_validate(self._request("DELETE", "/api/v1/knowledge").json())

    def ats_check(self, jd_text: str) -> AtsResponse:
        response = self._request("POST", "/api/v1/ats/check", json={"jd_text": jd_text})
        return AtsResponse.model_validate(response.json())

    def resume_ats(self, request: ResumeAtsRequest) -> ResumeAtsResponse:
        response = self._request(
            "POST", "/api/v1/resume/ats", json=request.model_dump(exclude_none=True)
        )
        return ResumeAtsResponse.model_validate(response.json())

    def download(self, document_id: str) -> DownloadedPdf:
        response = self._request("GET", f"/api/v1/resume/{document_id}")
        disposition = response.headers.get("content-disposition", "")
        filename = "resume.pdf"
        if 'filename="' in disposition:
            filename = disposition.split('filename="', 1)[1].split('"', 1)[0]
        return DownloadedPdf(content=response.content, filename=filename)


# --- embedded mode ----------------------------------------------------------


class EmbeddedBackendClient:
    """In-process client. Same responses, no network.

    Reuses the v1 route handlers rather than reimplementing the mapping from
    domain objects to response models -- two implementations of that mapping
    would drift, and the drift would only show up as the UI disagreeing with
    the API about what a resume looks like.
    """

    mode = "embedded"

    def __init__(self, service: Any | None = None) -> None:
        from resume_tailor.api.main import build_service
        from resume_tailor.core.config import get_settings

        self._settings = get_settings()
        self._service = service or build_service(self._settings)

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        from resume_tailor.core.errors import AppError

        try:
            return fn(*args, **kwargs)
        except AppError as exc:
            raise BackendError(
                exc.detail, code=exc.code, status=exc.status_code, title=exc.title
            ) from exc

    def readiness(self) -> ReadinessResponse:
        report = self._service.readiness()
        return ReadinessResponse(ready=report["ready"], checks=report["checks"])

    def meta(self) -> MetaResponse:
        from resume_tailor.api.v1.meta import get_meta

        return self._call(get_meta, self._service, self._settings)  # type: ignore[no-any-return]

    def projects(self, include_hidden: bool = False) -> ProjectListResponse:
        from resume_tailor.api.v1.projects import list_projects

        return self._call(list_projects, self._service, include_hidden)  # type: ignore[no-any-return]

    def match(self, jd_text: str) -> MatchResponse:
        from resume_tailor.api.schemas import MatchRequest
        from resume_tailor.api.v1.match import match as match_route

        return self._call(match_route, MatchRequest(jd_text=jd_text), self._service)  # type: ignore[no-any-return]

    def preview(self, request: ResumeRequest) -> PreviewResponse:
        from resume_tailor.api.v1.resume import preview as preview_route

        return self._call(preview_route, request, self._service)  # type: ignore[no-any-return]

    def generate(self, request: ResumeRequest) -> GenerateResponse:
        from resume_tailor.api.v1.resume import _build_spec

        spec = self._call(_build_spec, request, self._service)
        result = self._call(self._service.generate_sync, spec)
        fit = result.fit
        return GenerateResponse(
            document_id=result.document.document_id,
            download_url=f"/api/v1/resume/{result.document.document_id}",
            filename=result.document.filename,
            page_count=fit.page_count,
            max_pages=request.max_pages,
            fits=fit.fits,
            font_size_used=fit.font_size,
            line_spacing_used=fit.line_spacing,
            compile_attempts=fit.attempts,
            engine=fit.engine,
            warning=fit.warning,
            source_warnings=list(fit.source_warnings),
            bank_version=result.bank_version,
        )

    def knowledge(self) -> KnowledgeResponse:
        from resume_tailor.api.v1.knowledge import build_knowledge_response

        return build_knowledge_response(self._call(self._service.knowledge))

    def update_knowledge(
        self,
        *,
        text: str | None = None,
        document: bytes | None = None,
        filename: str | None = None,
        label: str | None = None,
        mode: str = "merge",
    ) -> KnowledgeUpdateResponse:
        """Straight to the service, with the route module's own response mapping.

        The one place an embedded call does not go through the route handler:
        that handler takes the document base64-encoded, and encoding bytes this
        process already holds only to decode them again would be waste. The
        *mapping* is still shared, so the two modes cannot drift.
        """
        from resume_tailor.api.v1.knowledge import build_update_response

        result = self._call(
            self._service.update_knowledge,
            text=text,
            document=document,
            filename=filename,
            label=label,
            mode=mode,
        )
        return build_update_response(result)

    def delete_knowledge_entry(self, category: str, value: str) -> KnowledgeDeleteResponse:
        from resume_tailor.api.v1.knowledge import delete_knowledge_entry as delete_route

        return self._call(delete_route, self._service, category, value)  # type: ignore[no-any-return]

    def clear_knowledge(self) -> KnowledgeResponse:
        from resume_tailor.api.v1.knowledge import clear_knowledge as clear_route

        return self._call(clear_route, self._service)  # type: ignore[no-any-return]

    def ats_check(self, jd_text: str) -> AtsResponse:
        from resume_tailor.api.schemas import AtsRequest
        from resume_tailor.api.v1.ats import check as check_route

        return self._call(check_route, AtsRequest(jd_text=jd_text), self._service)  # type: ignore[no-any-return]

    def resume_ats(self, request: ResumeAtsRequest) -> ResumeAtsResponse:
        from resume_tailor.api.v1.resume import resume_ats as resume_ats_route

        return self._call(resume_ats_route, request, self._service)  # type: ignore[no-any-return]

    def download(self, document_id: str) -> DownloadedPdf:
        document = self._call(self._service.documents.get, document_id)
        return DownloadedPdf(content=document.pdf_bytes, filename=document.filename)


# --- selection --------------------------------------------------------------


def build_client(mode: str | None = None) -> BackendClient:
    """Return the client named by ``RT_UI_MODE`` (default: ``http``)."""
    resolved = (mode or os.environ.get("RT_UI_MODE") or "http").strip().lower()
    if resolved == "embedded":
        return EmbeddedBackendClient()
    if resolved == "http":
        from resume_tailor.core.config import get_settings

        settings = get_settings()
        return HttpBackendClient(settings.api_base_url, api_key=settings.api_key)
    raise ValueError(f"unknown RT_UI_MODE {resolved!r}; expected 'http' or 'embedded'")


def source_fingerprint() -> str:
    """A token that changes whenever this module changes on disk.

    Exists for one caller: the Streamlit UI passes it to its cached
    ``get_client`` factory so that editing this file invalidates the cache.

    ``st.cache_resource`` survives a rerun *and* a hot reload, and Streamlit
    only invalidates it when the decorated function's own source changes. Add a
    method here and ``get_client``'s body is untouched, so the cache keeps
    handing back an instance of the *previous* class -- which then fails with
    ``AttributeError`` on the new method, in a process whose source on disk
    plainly has it. That is a genuinely confusing five minutes, and it is
    entirely avoidable.

    mtime plus size: the same stamp ``BankRepository`` and ``ProfileRepository``
    use to notice a content file changing under them. In a deployment nothing
    rewrites this file, so the cache is exactly as stable as it was before; the
    cost is one ``stat`` per rerun.
    """
    try:
        stat = Path(__file__).stat()
    except OSError:
        # No fingerprint available is not a reason to fail. Falling back to a
        # constant restores the previous cache-forever behaviour, which is
        # correct everywhere except a live edit.
        return "unknown"
    return f"{stat.st_mtime_ns}:{stat.st_size}"
