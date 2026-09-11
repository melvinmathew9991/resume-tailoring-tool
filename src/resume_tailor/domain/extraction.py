"""Plain text out of an uploaded document.

Feature 1 (candidate knowledge) accepts a PDF, a DOCX or pasted text. This
module is the only place that knows about file formats, so the knowledge
extractor downstream sees one thing -- a string -- regardless of what arrived.

Three deliberate non-choices:

``pypdf`` rather than a new PDF library
    It is already a dependency: :mod:`resume_tailor.render.pagefit` uses it to
    count the pages of a compiled resume. Reusing it keeps the dependency list
    unchanged.

stdlib ``zipfile`` plus a regex rather than ``python-docx``
    A ``.docx`` is a zip holding ``word/document.xml``. The text lives in
    ``<w:t>`` elements and paragraphs end at ``</w:p>``; pulling those out with
    two regexes is a dozen lines and adds nothing to install. It also sidesteps
    the XML parser entirely, so an uploaded file cannot reach an entity
    expansion (billion-laughs) surface -- there is no XML parser here to attack.

legacy ``.doc`` is refused, not parsed
    The pre-2007 format is an OLE compound document; reading it needs
    ``olefile`` or ``antiword``. Detecting the magic bytes and saying "save it
    as DOCX or PDF" is honest and costs nothing, whereas guessing at the binary
    would silently produce mojibake and store it as candidate facts.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass

from resume_tailor.core.errors import ExtractionError

#: What the uploader accepts. Anything else is refused by name so the user gets
#: "I do not read .pages" rather than an empty extraction they have to explain.
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx", ".txt", ".md", ".markdown")

#: Uncompressed ceiling for a single zip member. A 10 kB ``.docx`` can declare a
#: 4 GB ``document.xml``; checking ``ZipInfo.file_size`` *before* reading is what
#: turns that from an out-of-memory kill into a 422.
_MAX_DOCX_MEMBER_BYTES = 64 * 1024 * 1024

#: OLE2 compound-document signature -- a legacy ``.doc``, ``.xls`` or ``.ppt``.
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_DOCX_TEXT_RE = re.compile(rb"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.DOTALL)
_DOCX_PARAGRAPH_END_RE = re.compile(rb"</w:p>")
_DOCX_BREAK_RE = re.compile(rb"<w:(?:br|tab)\b[^>]*/?>")
_PARAGRAPH_MARK = b"\x00PARA\x00"

_XML_ENTITIES = (
    (b"&lt;", b"<"),
    (b"&gt;", b">"),
    (b"&quot;", b'"'),
    (b"&apos;", b"\x27"),
    # Ampersand last: unescaping it first would turn "&amp;lt;" into "<".
    (b"&amp;", b"&"),
)

#: ``&#233;`` / ``&#xe9;``. Applied to the decoded string, not the bytes.
_NUMERIC_REF_RE = re.compile(r"&#(?:([0-9]{1,7})|[xX]([0-9a-fA-F]{1,6}));")

#: Control characters that survive a bad PDF extraction. Kept out of the store
#: because they are invisible in the UI and would make two entries that look
#: identical compare unequal.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")


@dataclass(frozen=True)
class ExtractedDocument:
    """Normalised text plus enough provenance to attribute every fact to it."""

    text: str
    kind: str
    """``pdf``, ``docx``, ``text`` or ``paste`` -- reported back to the user."""
    label: str
    """Filename, or a caller-supplied label for pasted text."""
    sha256: str
    """Of the *input bytes*. Re-uploading the same file is detectable, which is
    what lets the UI say "already known" instead of appearing to do nothing."""

    @property
    def characters(self) -> int:
        return len(self.text)


def normalize_text(text: str) -> str:
    """Canonical whitespace, so the same document always extracts identically.

    Determinism matters here for the same reason it does in the ATS scorer: two
    uploads of one file must produce the same entries, or de-duplication has
    nothing stable to compare.
    """
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = _TRAILING_SPACE_RE.sub("\n", cleaned)
    cleaned = _BLANK_RUN_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def guess_kind(filename: str) -> str:
    """Map a filename to an extractor name, refusing anything unsupported."""
    lowered = (filename or "").strip().lower()
    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith(".docx"):
        return "docx"
    if lowered.endswith((".txt", ".md", ".markdown")):
        return "text"
    if lowered.endswith(".doc"):
        raise ExtractionError(
            "legacy .doc files cannot be read. Save it as .docx or .pdf, or paste the text instead."
        )
    suffix = lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    named = f".{suffix}" if suffix else repr(filename)
    raise ExtractionError(
        f"unsupported file type {named}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}."
    )


def extract_text(
    data: bytes,
    filename: str,
    *,
    max_bytes: int = 4 * 1024 * 1024,
) -> ExtractedDocument:
    """Read an uploaded document into normalised plain text.

    Every failure mode is an :class:`ExtractionError` carrying a message that
    names the fix. An upload producing no text is a failure, not an empty
    success -- silently storing nothing is the one outcome a user cannot debug.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ExtractionError(f"document must be bytes, got {type(data).__name__}")
    if not data:
        raise ExtractionError("the uploaded file is empty")
    if len(data) > max_bytes:
        raise ExtractionError(
            f"the uploaded file is {len(data):,} bytes, over the {max_bytes:,} byte limit"
        )

    payload = bytes(data)
    # Checked before the extension, because a legacy .doc renamed to .docx by
    # hand is a common upload and otherwise fails as "corrupt zip", which sends
    # the user looking for the wrong problem.
    if payload.startswith(_OLE_MAGIC):
        raise ExtractionError(
            "this is a legacy Microsoft Office (.doc) file, whatever its name says. "
            "Save it as .docx or .pdf, or paste the text instead."
        )

    kind = guess_kind(filename)
    if kind == "pdf":
        text = _extract_pdf(payload)
    elif kind == "docx":
        text = _extract_docx(payload)
    else:
        text = _extract_plain(payload)

    normalized = normalize_text(text)
    if not normalized:
        raise ExtractionError(
            f"no text could be read from {filename!r}. If it is a scan or an image-only "
            "PDF there is nothing to extract -- paste the text instead."
        )

    return ExtractedDocument(
        text=normalized,
        kind=kind,
        label=filename.strip() or f"upload.{kind}",
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def extract_text_from_paste(text: str, label: str = "pasted text") -> ExtractedDocument:
    """The copy/paste path. Same return type as an upload, so callers do not branch."""
    if not isinstance(text, str):
        raise ExtractionError(f"pasted text must be a string, got {type(text).__name__}")
    normalized = normalize_text(text)
    if not normalized:
        raise ExtractionError("the pasted text is empty")
    return ExtractedDocument(
        text=normalized,
        kind="paste",
        label=label.strip() or "pasted text",
        sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


# --- per-format extractors --------------------------------------------------


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, ValueError, OSError) as exc:
        raise ExtractionError(f"this PDF could not be opened: {exc}") from exc

    # An encrypted PDF opens fine and then yields an empty string for every
    # page, which would otherwise surface as the misleading "no text could be
    # read" and send the user hunting for a scanner problem they do not have.
    if getattr(reader, "is_encrypted", False):
        try:
            # An empty password is the common case for a PDF that is
            # "protected" only against printing. pypdf raises several unrelated
            # exception types on a malformed one, so the catch is total.
            opened = bool(reader.decrypt(""))
        except Exception:
            opened = False
        if not opened:
            raise ExtractionError(
                "this PDF is password protected. Remove the password, or paste the text instead."
            )

    pages: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:
            raise ExtractionError(f"page {number} of this PDF could not be read: {exc}") from exc
    return "\n\n".join(pages)


def _extract_docx(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExtractionError("this .docx file is corrupt or is not a Word document") from exc

    with archive:
        try:
            info = archive.getinfo("word/document.xml")
        except KeyError as exc:
            raise ExtractionError(
                "this .docx file has no word/document.xml, so it is not a Word document"
            ) from exc
        # The declared size, checked before a single byte is decompressed.
        if info.file_size > _MAX_DOCX_MEMBER_BYTES:
            raise ExtractionError(
                f"the document body inside this .docx expands to {info.file_size:,} bytes, "
                "which is far too large to be a resume"
            )
        try:
            xml = archive.read(info)
        except NotImplementedError as exc:
            # An unsupported compression method (deflate64, for instance).
            # Ordered *before* RuntimeError deliberately: NotImplementedError
            # is a subclass of it, so the other way round this case would be
            # reported as a password problem the user does not have.
            raise ExtractionError(
                f"this .docx file uses a compression method this tool cannot read ({exc}). "
                "Re-save it from Word, or paste the text instead."
            ) from exc
        except RuntimeError as exc:
            # zipfile signals an encrypted member with a bare RuntimeError. The
            # same case as a password-protected PDF, and it deserves the same
            # answer rather than escaping as a 500.
            raise ExtractionError(
                "this .docx file is password protected. Remove the password, or "
                "paste the text instead."
            ) from exc
        except (zipfile.BadZipFile, OSError) as exc:
            raise ExtractionError(f"this .docx file could not be unpacked: {exc}") from exc

    # Paragraph and line breaks become newlines *before* the text runs are
    # joined; without that every heading and bullet in the document collapses
    # into one line and the section splitter has nothing to work with.
    xml = _DOCX_PARAGRAPH_END_RE.sub(b"</w:p>" + _PARAGRAPH_MARK, xml)
    xml = _DOCX_BREAK_RE.sub(_PARAGRAPH_MARK, xml)

    parts: list[str] = []
    for chunk in xml.split(_PARAGRAPH_MARK):
        runs = [_unescape_xml(match.group(1)) for match in _DOCX_TEXT_RE.finditer(chunk)]
        parts.append("".join(runs))
    return "\n".join(parts)


def _unescape_xml(raw: bytes) -> str:
    for entity, char in _XML_ENTITIES:
        raw = raw.replace(entity, char)
    # Numeric character references, decoded *after* the named entities and on
    # the decoded string, because the code point may be non-ASCII.
    #
    # Not optional. The five named entities are the only ones XML predefines,
    # so every other non-ASCII character a writer produces arrives as `&#233;`
    # or `&#xe9;`. Leaving those alone stored "Universit&#233; de Paris" as a
    # candidate fact -- visible in the UI, and matched against by the ATS
    # scorer, which will never find the word it was looking for.
    return _NUMERIC_REF_RE.sub(_replace_numeric_ref, raw.decode("utf-8", errors="replace"))


def _replace_numeric_ref(match: re.Match[str]) -> str:
    """One ``&#NNN;`` or ``&#xHH;`` reference, or the text itself if it is not
    a usable code point -- a malformed reference is left visible rather than
    swallowed, so a broken document looks broken instead of losing characters."""
    digits, hex_digits = match.group(1), match.group(2)
    try:
        code_point = int(hex_digits, 16) if hex_digits else int(digits, 10)
    except ValueError:  # pragma: no cover - the pattern only matches digits
        return match.group(0)
    # Surrogates and out-of-range values are not characters; `chr` raises on
    # the latter and produces an unencodable string for the former.
    if code_point > 0x10FFFF or 0xD800 <= code_point <= 0xDFFF:
        return match.group(0)
    return chr(code_point)


def _extract_plain(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # latin-1 decodes any byte sequence, so this is unreachable today. It exists
    # so a future edit to that tuple cannot fall off the end silently.
    raise ExtractionError("this file is not text in any encoding this tool understands")


# --- section splitting ------------------------------------------------------

#: Headings a resume or profile document actually uses, mapped to the canonical
#: section name the knowledge extractor keys off.
_SECTION_HEADINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "education",
        ("education", "academic background", "academics", "qualifications", "degrees"),
    ),
    (
        "certifications",
        ("certifications", "certificates", "certification", "licenses", "courses"),
    ),
    (
        "experience",
        (
            "work experience",
            "professional experience",
            "employment history",
            "experience",
            "employment",
            "career history",
        ),
    ),
    (
        "projects",
        ("projects", "personal projects", "selected projects", "portfolio"),
    ),
    (
        "skills",
        (
            "technical skills",
            "core competencies",
            "skills and tools",
            "skills",
            "technologies",
            "tools",
            "tech stack",
        ),
    ),
    (
        "summary",
        ("professional summary", "summary", "profile", "objective", "about me", "about"),
    ),
    (
        "achievements",
        ("achievements", "awards", "publications", "highlights", "accomplishments"),
    ),
)

#: A heading is short. Requiring that is what stops the word "Skills" appearing
#: mid-sentence from being read as a section boundary, which would file the rest
#: of the paragraph under the wrong heading.
_MAX_HEADING_WORDS = 5


def match_heading(line: str) -> str | None:
    """Return the canonical section name if ``line`` is a section heading."""
    stripped = line.strip().strip(":").strip()
    if not stripped or len(stripped.split()) > _MAX_HEADING_WORDS:
        return None
    lowered = re.sub(r"[^a-z ]+", " ", stripped.lower())
    lowered = re.sub(r"\s+", " ", lowered).strip()
    if not lowered:
        return None
    for canonical, variants in _SECTION_HEADINGS:
        if lowered in variants:
            return canonical
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """Group lines under the resume section heading that precedes them.

    Lines before the first recognised heading land under ``preamble`` -- that is
    where a header block and an unlabelled summary live, and dropping them would
    throw away the candidate's name and contact details.
    """
    sections: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    for raw_line in text.split("\n"):
        heading = match_heading(raw_line)
        if heading is not None:
            current = heading
            sections.setdefault(current, [])
            continue
        line = raw_line.strip()
        if line:
            sections.setdefault(current, []).append(line)
    return {name: lines for name, lines in sections.items() if lines}
