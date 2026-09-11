"""Document extraction tests.

The fixtures build real PDF and DOCX bytes rather than mocking the readers.
Mocking here would assert that the code calls a library, which is not the thing
that breaks -- what breaks is a real file from a real word processor producing
one run-on line, or an encrypted PDF surfacing as "no text could be read".
"""

from __future__ import annotations

import zipfile

import pytest

from resume_tailor.core.errors import ExtractionError
from resume_tailor.domain import extraction
from resume_tailor.domain.extraction import (
    extract_text,
    extract_text_from_paste,
    guess_kind,
    match_heading,
    normalize_text,
    split_sections,
)

pytestmark = pytest.mark.unit


# --- builders ---------------------------------------------------------------


def make_pdf(text: str) -> bytes:
    """A minimal but genuinely valid single-page PDF containing ``text``."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % index + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def make_docx(paragraphs: list[str], *, extra_files: bool = True) -> bytes:
    """A .docx holding one ``<w:p>`` per paragraph."""
    body = "".join(f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = __import__("io").BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if extra_files:
            archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


# --- normalisation ----------------------------------------------------------


class TestNormalizeText:
    def test_line_endings_and_nbsp_are_canonicalised(self) -> None:
        assert normalize_text("a\r\nb\rc\xa0d") == "a\nb\nc d"

    def test_control_characters_are_removed(self) -> None:
        assert "\x07" not in normalize_text("Python\x07 SQL")

    def test_long_blank_runs_collapse(self) -> None:
        assert normalize_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_is_idempotent(self) -> None:
        """Determinism downstream depends on this: two uploads of one file must
        normalise to identical text or de-duplication has nothing to compare."""
        once = normalize_text("  a\r\n\r\n\r\n b \t\n")
        assert normalize_text(once) == once


# --- type detection ---------------------------------------------------------


class TestGuessKind:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("cv.pdf", "pdf"),
            ("CV.PDF", "pdf"),
            ("profile.docx", "docx"),
            ("notes.txt", "text"),
            ("notes.md", "text"),
            ("notes.markdown", "text"),
        ],
    )
    def test_supported(self, filename: str, expected: str) -> None:
        assert guess_kind(filename) == expected

    def test_legacy_doc_is_refused_with_advice(self) -> None:
        with pytest.raises(ExtractionError, match=r"legacy \.doc"):
            guess_kind("resume.doc")

    def test_unknown_extension_names_itself(self) -> None:
        with pytest.raises(ExtractionError, match=r"\.pages"):
            guess_kind("resume.pages")

    def test_no_extension_is_refused(self) -> None:
        with pytest.raises(ExtractionError):
            guess_kind("resume")


# --- input guards -----------------------------------------------------------


class TestInputGuards:
    def test_empty_file(self) -> None:
        with pytest.raises(ExtractionError, match="empty"):
            extract_text(b"", "cv.txt")

    def test_oversized_file(self) -> None:
        with pytest.raises(ExtractionError, match="over the"):
            extract_text(b"x" * 100, "cv.txt", max_bytes=10)

    def test_non_bytes(self) -> None:
        with pytest.raises(ExtractionError, match="must be bytes"):
            extract_text("not bytes", "cv.txt")  # type: ignore[arg-type]

    def test_ole_magic_is_caught_even_when_renamed_to_docx(self) -> None:
        """A legacy .doc renamed by hand is a real and otherwise baffling case:
        without the magic-byte check it fails as 'corrupt zip'."""
        payload = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"padding"
        with pytest.raises(ExtractionError, match="legacy Microsoft Office"):
            extract_text(payload, "resume.docx")

    def test_empty_paste(self) -> None:
        with pytest.raises(ExtractionError, match="empty"):
            extract_text_from_paste("   \n  ")

    def test_non_string_paste(self) -> None:
        with pytest.raises(ExtractionError, match="must be a string"):
            extract_text_from_paste(b"bytes")  # type: ignore[arg-type]


# --- plain text -------------------------------------------------------------


class TestPlainText:
    def test_utf8(self) -> None:
        document = extract_text("Python — SQL".encode(), "cv.txt")
        assert "Python" in document.text
        assert document.kind == "text"

    def test_bom_is_stripped(self) -> None:
        document = extract_text("﻿Python".encode(), "cv.txt")
        assert document.text == "Python"

    def test_cp1252_fallback(self) -> None:
        document = extract_text("caf\xe9".encode("cp1252"), "cv.txt")
        assert document.text  # decoded rather than raising

    def test_hash_identifies_the_input(self) -> None:
        first = extract_text(b"Python", "cv.txt")
        second = extract_text(b"Python", "other.txt")
        assert first.sha256 == second.sha256


# --- pdf --------------------------------------------------------------------


class TestPdf:
    def test_text_is_read(self) -> None:
        document = extract_text(make_pdf("Python and SQL skills"), "cv.pdf")
        assert "Python and SQL skills" in document.text
        assert document.kind == "pdf"

    def test_pdf_without_text_is_an_error_not_an_empty_success(self) -> None:
        """An image-only scan must say so. Storing nothing silently is the one
        outcome the user cannot debug."""
        with pytest.raises(ExtractionError, match="no text could be read"):
            extract_text(make_pdf(""), "scan.pdf")

    def test_corrupt_pdf(self) -> None:
        with pytest.raises(ExtractionError, match="could not be opened"):
            extract_text(b"%PDF-1.4 truncated", "cv.pdf")


# --- docx -------------------------------------------------------------------


class TestDocx:
    def test_paragraphs_become_lines(self) -> None:
        document = extract_text(make_docx(["Skills", "Python, SQL"]), "cv.docx")
        assert document.text.split("\n") == ["Skills", "Python, SQL"]

    def test_xml_entities_are_unescaped(self) -> None:
        document = extract_text(make_docx(["R&amp;D and &lt;tags&gt;"]), "cv.docx")
        assert document.text == "R&D and <tags>"

    def test_numeric_character_references_are_decoded(self) -> None:
        """XML predefines only five named entities, so every other non-ASCII
        character arrives as `&#233;` or `&#xe9;`. Left alone, an accented
        university name is stored as the literal "Universit&#233;" -- shown in
        the UI, and matched against by the ATS scorer, which then never finds
        the word it was looking for."""
        document = extract_text(
            make_docx(["Universit&#233; de Paris &#8212; M&#xe9;canique"]), "cv.docx"
        )
        assert document.text == "Université de Paris — Mécanique"

    def test_a_malformed_numeric_reference_is_left_visible(self) -> None:
        """Out of range or a surrogate: not a character. Kept as written rather
        than swallowed, so a broken document looks broken instead of quietly
        losing text."""
        document = extract_text(make_docx(["out &#99999999; and &#xD800; here"]), "cv.docx")
        assert document.text == "out &#99999999; and &#xD800; here"

    def test_corrupt_archive(self) -> None:
        with pytest.raises(ExtractionError, match="corrupt"):
            extract_text(b"not a zip at all, but long enough", "cv.docx")

    def test_zip_without_a_document_body(self) -> None:
        buffer = __import__("io").BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("hello.txt", "hi")
        with pytest.raises(ExtractionError, match=r"no word/document\.xml"):
            extract_text(buffer.getvalue(), "cv.docx")

    def test_declared_member_size_is_checked_before_decompression(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The zip-bomb guard. A 10 kB .docx can declare a 4 GB body, so the
        declared size is refused before a byte is decompressed."""
        monkeypatch.setattr(extraction, "_MAX_DOCX_MEMBER_BYTES", 4)
        with pytest.raises(ExtractionError, match="too large"):
            extract_text(make_docx(["Python is a language"]), "cv.docx")


# --- sections ---------------------------------------------------------------


class TestSections:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("Skills", "skills"),
            ("TECHNICAL SKILLS", "skills"),
            ("Work Experience:", "experience"),
            ("Education", "education"),
            ("Certifications", "certifications"),
            ("Professional Summary", "summary"),
            ("Selected Projects", "projects"),
        ],
    )
    def test_headings_are_recognised(self, line: str, expected: str) -> None:
        assert match_heading(line) == expected

    def test_a_sentence_mentioning_skills_is_not_a_heading(self) -> None:
        """The length guard. Without it, a sentence containing the word
        'skills' would split the document and misfile everything after it."""
        assert match_heading("You will use these skills every day at work") is None

    def test_lines_are_grouped_under_their_heading(self) -> None:
        sections = split_sections("Jane Doe\n\nSkills\nPython\n\nEducation\nBSc, 2020")
        assert sections["preamble"] == ["Jane Doe"]
        assert sections["skills"] == ["Python"]
        assert sections["education"] == ["BSc, 2020"]

    def test_empty_sections_are_dropped(self) -> None:
        assert "certifications" not in split_sections("Skills\nPython\n\nCertifications\n")


class TestDocxUnreadableMembers:
    """Two zipfile failures that are not `BadZipFile` and used to escape as 500s.

    Both are provoked by patching `ZipFile.read`, because `zipfile` cannot
    write an encrypted or deflate64 member -- the point under test is the
    handler, not our ability to build the file.
    """

    def test_an_encrypted_docx_is_refused_with_advice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def encrypted(self: object, name: object) -> bytes:
            raise RuntimeError("File word/document.xml is encrypted, password required")

        monkeypatch.setattr(zipfile.ZipFile, "read", encrypted)
        with pytest.raises(ExtractionError, match="password protected"):
            extract_text(make_docx(["Python"]), "cv.docx")

    def test_an_unsupported_compression_method_is_refused_with_advice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unsupported(self: object, name: object) -> bytes:
            raise NotImplementedError("compression type 9 (deflate64)")

        monkeypatch.setattr(zipfile.ZipFile, "read", unsupported)
        with pytest.raises(ExtractionError, match="compression method"):
            extract_text(make_docx(["Python"]), "cv.docx")
