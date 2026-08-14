"""Resume profile loading (everything that is not a project).

The header, summary, work experience, skills and education used to be
module-level Python constants inside ``resume_builder.py`` (defect S2), which
meant changing a phone number was a code edit and a person's home address was
in the import path of the web server. They now live in ``data/profile.yaml``.

YAML rather than JSON specifically because this file is long, prose-heavy and
hand-edited: block scalars keep the LaTeX-formatted bullets readable instead of
collapsing them into one escaped line.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from resume_tailor.core.errors import ProfileError
from resume_tailor.core.logging import get_logger
from resume_tailor.domain.models import Profile

logger = get_logger(__name__)


def parse_profile(text: str, source: str = "<string>") -> Profile:
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"{source} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ProfileError(f"{source} must contain a YAML mapping at the top level")

    try:
        return Profile.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()[:10]
        )
        raise ProfileError(f"{source} failed validation: {details}") from exc


class ProfileRepository:
    """Thread-safe, mtime-invalidated cache over the profile file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cache: Profile | None = None
        self._stamp: tuple[float, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Profile:
        try:
            stat = self._path.stat()
        except OSError as exc:
            raise ProfileError(f"cannot read profile at {self._path}: {exc}") from exc
        stamp = (stat.st_mtime, stat.st_size)

        with self._lock:
            if self._cache is not None and self._stamp == stamp:
                return self._cache
            try:
                text = self._path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise ProfileError(f"cannot read profile at {self._path}: {exc}") from exc

            profile = parse_profile(text, source=str(self._path))
            self._cache = profile
            self._stamp = stamp
            logger.info(
                "profile.loaded",
                path=str(self._path),
                experience_entries=len(profile.experience),
                skill_categories=len(profile.skills),
            )
            return profile

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._stamp = None
