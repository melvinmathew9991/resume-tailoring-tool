"""Project-bank loading, validation and caching.

The original cached the bank in a bare module-level global that was populated
once and never invalidated (defect B11). The README told the reader to "just
edit ``project_bank.json``", but a running server would keep serving the old
content until it was restarted -- a silent staleness bug in the documented
workflow. This module reloads whenever the file's mtime or size changes, and
does it under a lock so concurrent requests cannot race each other into two
half-built caches.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from resume_tailor.core.errors import BankError
from resume_tailor.core.logging import get_logger
from resume_tailor.domain.models import ProjectBank

logger = get_logger(__name__)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.load`` silently keeps the last of duplicated keys.

    For a file whose whole purpose is being hand-edited, that means a
    copy-paste mistake quietly deletes a project. Rejecting duplicates turns a
    silent data loss into a startup error.
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate project key {key!r}")
        seen[key] = value
    return seen


def parse_bank(text: str, source: str = "<string>") -> ProjectBank:
    """Parse and validate bank JSON, raising :class:`BankError` on any problem."""
    try:
        raw = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:  # JSONDecodeError and the duplicate-key ValueError
        raise BankError(f"{source} is not valid project-bank JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise BankError(f"{source} must contain a JSON object mapping key -> project")
    if not raw:
        raise BankError(f"{source} contains no projects")

    try:
        return ProjectBank.from_raw(raw)
    except ValidationError as exc:
        raise BankError(f"{source} failed validation: {_format_errors(exc)}") from exc


def _format_errors(exc: ValidationError) -> str:
    """Turn pydantic's error list into something a human can act on directly."""
    parts = []
    for error in exc.errors()[:10]:
        location = ".".join(str(item) for item in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def lint_bank(bank: ProjectBank) -> list[str]:
    """Content warnings that are not hard errors.

    Deliberately separate from validation: these describe content that will
    compile fine but is probably not what the author intended. Surfaced on
    ``/health/ready`` and by the CLI, never fatal.
    """
    warnings: list[str] = []
    for key, project in bank.projects.items():
        if "/" not in project.github:
            warnings.append(
                f"{key}: github is '{project.github}' (a profile, not a repository) -- "
                "the resume will link to your GitHub home page rather than the project"
            )
        if not project.keywords:
            warnings.append(f"{key}: no keywords, so it can never match a job description")
        if not project.domain:
            warnings.append(f"{key}: no domain tags, so it can never earn the domain bonus")
        if len(project.bullets) < 2:
            warnings.append(f"{key}: only {len(project.bullets)} bullet(s)")
    return warnings


class BankRepository:
    """Thread-safe, mtime-invalidated cache over a project-bank file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cache: ProjectBank | None = None
        self._stamp: tuple[float, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _current_stamp(self) -> tuple[float, int]:
        try:
            stat = self._path.stat()
        except OSError as exc:
            raise BankError(f"cannot read project bank at {self._path}: {exc}") from exc
        return (stat.st_mtime, stat.st_size)

    def load(self) -> ProjectBank:
        """Return the bank, reloading it if the file changed on disk."""
        stamp = self._current_stamp()
        with self._lock:
            if self._cache is not None and self._stamp == stamp:
                return self._cache

            try:
                # utf-8-sig transparently strips a BOM, which Windows editors
                # add and which json.loads otherwise chokes on.
                text = self._path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise BankError(f"cannot read project bank at {self._path}: {exc}") from exc

            bank = parse_bank(text, source=str(self._path))
            self._cache = bank
            self._stamp = stamp
            logger.info(
                "project_bank.loaded",
                path=str(self._path),
                projects=len(bank),
                hidden=len(bank.projects) - len(bank.visible()),
                version=bank.version,
            )
            return bank

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._stamp = None
