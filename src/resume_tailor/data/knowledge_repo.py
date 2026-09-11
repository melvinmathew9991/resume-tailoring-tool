"""Candidate-knowledge persistence.

Follows :class:`resume_tailor.data.bank_repo.BankRepository` deliberately --
same lock, same mtime/size stamp, same "reload when the file changed" rule --
because a second, subtly different caching strategy in the same application is
how one of them ends up serving stale content that nobody can reproduce.

Two differences, both forced by the fact that this file is *written* by the
application rather than hand-edited:

Missing is not an error
    A fresh checkout has no ``knowledge.json``. That is an empty knowledge
    base, not a failure -- the project bank cannot say the same, because a
    missing bank means the tool has nothing to put on a resume.

Writes are atomic
    Content is written to a temporary file in the same directory and then
    :func:`os.replace`\\ d over the target, which is atomic on both POSIX and
    Windows. A crash mid-write therefore leaves the previous store intact
    instead of a truncated JSON file that fails to parse on next boot -- which
    for a store the user has been adding to for weeks is data loss.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from resume_tailor.core.errors import KnowledgeError
from resume_tailor.core.logging import get_logger
from resume_tailor.domain.knowledge import KnowledgeBase

logger = get_logger(__name__)


def parse_knowledge(text: str, source: str = "<string>") -> KnowledgeBase:
    """Parse and validate a knowledge store, raising :class:`KnowledgeError`."""
    try:
        raw: Any = json.loads(text)
    except ValueError as exc:
        raise KnowledgeError(f"{source} is not valid knowledge JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise KnowledgeError(f"{source} must contain a JSON object")

    try:
        return KnowledgeBase.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()[:10]
        )
        raise KnowledgeError(f"{source} failed validation: {details}") from exc


class KnowledgeRepository:
    """Thread-safe, mtime-invalidated cache over ``data/knowledge.json``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cache: KnowledgeBase | None = None
        self._stamp: tuple[float, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> KnowledgeBase:
        """Return the stored knowledge, or an empty base if nothing is stored yet."""
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return KnowledgeBase()
        except OSError as exc:
            raise KnowledgeError(f"cannot read knowledge store at {self._path}: {exc}") from exc

        stamp = (stat.st_mtime, stat.st_size)
        with self._lock:
            if self._cache is not None and self._stamp == stamp:
                return self._cache
            try:
                text = self._path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise KnowledgeError(f"cannot read knowledge store at {self._path}: {exc}") from exc

            knowledge = parse_knowledge(text, source=str(self._path))
            self._cache = knowledge
            self._stamp = stamp
            logger.info(
                "knowledge.loaded",
                path=str(self._path),
                entries=len(knowledge.entries),
                sources=len(knowledge.sources),
                version=knowledge.version,
            )
            return knowledge

    def save(self, knowledge: KnowledgeBase) -> KnowledgeBase:
        """Persist the store atomically and return what was written."""
        payload = json.dumps(knowledge.model_dump(mode="json"), indent=2, ensure_ascii=False)
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                # mkstemp rather than NamedTemporaryFile: the file must survive
                # being closed so it can be moved into place, and mkstemp says
                # so plainly instead of relying on delete=False.
                descriptor, name = tempfile.mkstemp(
                    dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
                )
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        handle.write(payload)
                        handle.flush()
                        # fsync before the rename: os.replace is atomic with
                        # respect to *visibility*, not durability, so without
                        # this a crash can leave the new name pointing at an
                        # empty file.
                        os.fsync(handle.fileno())
                    os.replace(temporary, self._path)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
            except OSError as exc:
                raise KnowledgeError(
                    f"cannot write knowledge store at {self._path}: {exc}"
                ) from exc

            self._cache = knowledge
            try:
                stat = self._path.stat()
                self._stamp = (stat.st_mtime, stat.st_size)
            except OSError:
                # Losing the stamp only costs one redundant reload; refusing the
                # write that already succeeded would be worse.
                self._stamp = None

        logger.info(
            "knowledge.saved",
            path=str(self._path),
            entries=len(knowledge.entries),
            sources=len(knowledge.sources),
            version=knowledge.version,
        )
        return knowledge

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._stamp = None
