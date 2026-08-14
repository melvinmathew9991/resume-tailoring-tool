"""The error contract.

Every failure the application can produce is one of these types. Each carries a
stable machine-readable ``code`` so the Streamlit UI (and any script) can branch
on the failure kind instead of pattern-matching English prose -- which is what
the old frontend had to do (``data.error || "Something went wrong"``).

Serialised over HTTP as RFC 7807 ``application/problem+json``.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected (non-bug) failure.

    An ``AppError`` reaching the API layer becomes a structured response. Any
    *other* exception reaching the API layer is a bug and becomes a 500 with the
    detail withheld from the client and logged with a traceback.
    """

    code: str = "internal_error"
    status_code: int = 500
    title: str = "Internal error"

    def __init__(self, detail: str, **context: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context = context

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        """Render as an RFC 7807 problem document."""
        problem: dict[str, Any] = {
            "type": f"https://resume-tailor.invalid/errors/{self.code}",
            "code": self.code,
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
        }
        if instance:
            problem["instance"] = instance
        if self.context:
            problem["context"] = self.context
        return problem


# --- client errors ---------------------------------------------------------


class InvalidInputError(AppError):
    code = "invalid_input"
    status_code = 400
    title = "Invalid input"


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404
    title = "Not found"


class UnknownProjectError(NotFoundError):
    code = "unknown_project"
    title = "Unknown project key"


class HiddenProjectError(InvalidInputError):
    code = "hidden_project"
    title = "Project is not selectable"
    """Raised when a caller asks to put a project marked ``hidden`` on a resume.

    The old code hid ``transformer_scratch`` from the listing and match
    endpoints but let /generate use it anyway (defect B3). Hiddenness is now a
    property of the data and is enforced at every entry point.
    """


class PayloadTooLargeError(AppError):
    code = "payload_too_large"
    status_code = 413
    title = "Request body too large"


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = 429
    title = "Too many requests"


class UnsafeContentError(AppError):
    code = "unsafe_content"
    status_code = 422
    title = "Content rejected by the LaTeX safety audit"
    """User text that survived escaping but still contains a dangerous LaTeX
    construct. Defence in depth behind :mod:`resume_tailor.domain.latex`
    (defect C1)."""


# --- data / configuration errors -------------------------------------------


class BankError(AppError):
    code = "bank_invalid"
    status_code = 500
    title = "Project bank is invalid"


class ProfileError(AppError):
    code = "profile_invalid"
    status_code = 500
    title = "Resume profile is invalid"


class TemplateRenderError(AppError):
    code = "template_render_failed"
    status_code = 500
    title = "Could not render the LaTeX template"


# --- compilation errors ----------------------------------------------------


class EngineUnavailableError(AppError):
    code = "engine_unavailable"
    status_code = 503
    title = "No PDF engine available"

    def __init__(self, detail: str, **context: Any) -> None:
        super().__init__(detail, **context)


class CompilationError(AppError):
    code = "compilation_failed"
    status_code = 422
    title = "LaTeX compilation failed"

    def __init__(self, detail: str, log_tail: str = "", **context: Any) -> None:
        super().__init__(detail, **context)
        self.log_tail = log_tail

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        problem = super().to_problem(instance)
        if self.log_tail:
            problem["log_tail"] = self.log_tail
        return problem


class CompileTimeoutError(CompilationError):
    code = "compile_timeout"
    status_code = 504
    title = "LaTeX compilation timed out"
    """A hung compile is a *distinct* failure from a syntax error: it usually
    means hostile or pathological input, not a bad template. The old code let
    ``subprocess.TimeoutExpired`` escape as an opaque 500 (defect B10)."""


class PageCountError(AppError):
    code = "page_count_failed"
    status_code = 500
    title = "Could not determine the page count of the compiled PDF"
