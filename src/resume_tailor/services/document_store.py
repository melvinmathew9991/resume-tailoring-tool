"""Short-lived store for generated PDFs.

Exists so ``/resume/generate`` can return metadata immediately and the bytes can
be *streamed* from a second request, instead of base64-encoding the PDF into a
JSON body (defect S7) -- which inflated every response by a third and forced
both ends to buffer the whole document in memory as a string.

Deliberately in-memory, bounded and TTL'd: this is a single-user tool, and a
generated resume has no value ten minutes after it was generated. Nothing here
is persisted, so nothing here needs cleaning up on disk.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from resume_tailor.core.errors import NotFoundError


@dataclass(frozen=True)
class StoredDocument:
    document_id: str
    pdf_bytes: bytes
    filename: str
    page_count: int
    created_at: float


class DocumentStore:
    """Thread-safe bounded TTL cache of generated PDFs."""

    def __init__(self, ttl_s: int = 900, max_documents: int = 32) -> None:
        self._ttl_s = ttl_s
        self._max = max_documents
        self._lock = threading.Lock()
        self._items: OrderedDict[str, StoredDocument] = OrderedDict()

    def put(self, pdf_bytes: bytes, filename: str, page_count: int) -> StoredDocument:
        document = StoredDocument(
            document_id=secrets.token_urlsafe(16),
            pdf_bytes=pdf_bytes,
            filename=filename,
            page_count=page_count,
            created_at=time.monotonic(),
        )
        with self._lock:
            self._evict_expired()
            while len(self._items) >= self._max:
                self._items.popitem(last=False)
            self._items[document.document_id] = document
        return document

    def get(self, document_id: str) -> StoredDocument:
        with self._lock:
            self._evict_expired()
            document = self._items.get(document_id)
        if document is None:
            raise NotFoundError(
                "no such document, or it has expired -- generate the resume again",
                document_id=document_id,
            )
        return document

    def _evict_expired(self) -> None:
        cutoff = time.monotonic() - self._ttl_s
        expired = [key for key, item in self._items.items() if item.created_at < cutoff]
        for key in expired:
            del self._items[key]

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired()
            return len(self._items)
