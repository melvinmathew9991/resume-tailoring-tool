r"""The text-extraction check: does an ATS read what the page shows?

``docs/resume_automation_spec.md`` section 2, criterion 4, asks for formatting
compatibility to be "confirmed via actual text-extraction test ... to verify the
PDF parses cleanly". Everything else in this tool reasons about the resume from
the *source* -- the page-fit ladder counts pages, and the resume ATS score reads
``ResumeSpec``. Neither opens the compiled document and asks what a parser would
actually find in it.

That gap matters because the two can disagree. A term the score credits is only
worth credit if an applicant tracking system can extract it, and PDF text
extraction is not guaranteed to return what was typeset: a font without a
character map yields glyph indices instead of letters, a hyphenated line break
splits a word in half, and an ``\href`` that never became an annotation is a
link a human can read and a machine cannot follow. Each of those produces a
document that looks perfect and scores worse than it should.

So this module compiles nothing and re-renders nothing. It takes the PDF bytes
that were just produced, pulls the text back out with ``pypdf`` -- the same
library the page count already uses, so no new dependency and no second binary
-- and compares that text against what the spec says should be on the page.

The check is deliberately one-directional. It reports only text that was
*expected and not found*; extra text in the PDF is not a defect, because the
template legitimately emits labels ("GitHub:", section rules) that no model
holds. A check that flagged those would cry wolf on every run and be switched
off within a week.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any, Literal

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from resume_tailor.core.logging import get_logger
from resume_tailor.domain.latex import latex_to_display_text
from resume_tailor.domain.matching import normalize
from resume_tailor.domain.models import ResumeSpec

logger = get_logger(__name__)

Severity = Literal["fail", "warn"]
Status = Literal["pass", "warn", "fail"]

#: Section headings the template emits, each paired with the field whose
#: emptiness suppresses it. Declared here rather than inferred, and pinned to
#: the template by ``test_every_template_heading_is_declared`` -- a heading
#: added to the template and forgotten here would quietly stop being checked.
SECTION_HEADINGS: tuple[tuple[str, str], ...] = (
    ("Summary", "summary"),
    ("Experience", "experience"),
    ("Projects", "projects"),
    ("Skills", "skills"),
    ("Education", "education"),
)

#: Below this many extracted characters the document is empty for practical
#: purposes -- a scanned image, or a font the extractor could not decode at all.
MIN_CHARACTERS = 200

#: Coverage bands. A real compile should round-trip essentially everything, so
#: the warning threshold sits high; the failure threshold is where enough text
#: is gone that the resume no longer says what it was scored for saying.
WARN_COVERAGE = 0.99
FAIL_COVERAGE = 0.90

#: Tokens shorter than this are dropped from the comparison. Short words are
#: both the least informative to an ATS and the most likely to be swallowed by
#: punctuation on either side of a line break.
MIN_TOKEN_CHARS = 4

#: How many missing tokens to report. Enough to see the pattern -- one mangled
#: bullet, or a whole missing section -- without pasting the resume back.
MAX_REPORTED_TERMS = 25

#: A run of letters this long is not a word; it is two or more words that
#: extracted without the space between them, which is what a multi-column or
#: text-box layout does to a parser.
MAX_WORD_CHARS = 30

#: Unicode replacement character, plus the Basic Multilingual Plane private use
#: area. Both mean the extractor found a glyph it could not name -- the classic
#: symptom of a PDF whose fonts carry no ``/ToUnicode`` map.
_UNDECODED_RE = re.compile("[\ufffd\ue000-\uf8ff]")

#: Typographic ligatures, and the letters each one stands for. A word set with
#: one of these is on the page and readable to a person, but a literal keyword
#: scan for the letters will not match it -- the single most common reason a
#: LaTeX resume scores below its content.
LIGATURE_MAP = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}

#: En dash, em dash, and the ``--``/``---`` source forms LaTeX turns into them.
_DASH_RE = re.compile("[\u2013\u2014]|--+")

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#@./&_-]*")
_LONG_WORD_RE = re.compile(rf"[a-z]{{{MAX_WORD_CHARS + 1},}}")

NOTE = (
    "Text is extracted from the compiled PDF with pypdf and compared against "
    "the text the spec says is on the page. Coverage is the share of expected "
    "words that survived extraction; anything an ATS cannot read cannot be "
    "scored, however well it is typeset."
)


@dataclass(frozen=True)
class Finding:
    """One thing wrong with the compiled document, in the reader's terms."""

    code: str
    severity: Severity
    detail: str


@dataclass(frozen=True)
class ParseCheck:
    """What a parser found in the PDF, and how far that is from the page."""

    characters: int
    words: int
    term_coverage: float
    """Share of expected words found in the extracted text, 0.0--1.0. Exactly
    1.0 when the document round-trips completely."""
    expected_terms: int
    missing_terms: tuple[str, ...]
    links: tuple[str, ...]
    """Clickable URI targets actually present in the PDF as link annotations."""
    findings: tuple[Finding, ...]
    note: str = NOTE

    @property
    def status(self) -> Status:
        if any(finding.severity == "fail" for finding in self.findings):
            return "fail"
        return "warn" if self.findings else "pass"

    @property
    def parses(self) -> bool:
        """True when an ATS would read this document as intended."""
        return self.status != "fail"


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Pull the text back out, in-process.

    Returns an empty string rather than raising when the document cannot be
    read: an unreadable PDF is a *finding* here, not an exception. Generation
    has already succeeded by the time this runs, and refusing to hand over a
    compiled resume because the check on it failed would be a worse outcome
    than reporting the problem beside it.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError, OSError, KeyError) as exc:
        logger.warning("parsecheck.extract_failed", error=str(exc))
        return ""


def extract_pdf_links(pdf_bytes: bytes) -> list[str]:
    """URI targets of every link annotation, in page order, de-duplicated.

    A link a human can read is not the same object as a link a machine can
    follow: the first is typeset text, the second is an annotation over it.
    ``hyperref`` produces both, so their absence means the link went onto the
    page as ink only.
    """
    found: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            for uri in _page_link_uris(page):
                if uri not in found:
                    found.append(uri)
    except (PdfReadError, ValueError, OSError, KeyError) as exc:
        logger.warning("parsecheck.links_failed", error=str(exc))
    return found


def _page_link_uris(page: Any) -> list[str]:
    annotations = page.get("/Annots")
    if not annotations:
        return []
    uris: list[str] = []
    for reference in annotations:
        uri = _link_uri(reference)
        if uri:
            uris.append(uri)
    return uris


def _link_uri(reference: Any) -> str | None:
    """One annotation's URI, or ``None`` for anything that is not a usable link.

    The catch is total because the input is a parsed third-party file: a
    malformed annotation is a property of the PDF, not a bug here, and one bad
    entry must not cost the links that follow it.
    """
    try:
        annotation = reference.get_object()
        if annotation.get("/Subtype") != "/Link":
            return None
        action = annotation.get("/A")
        if action is None:
            return None
        uri = action.get_object().get("/URI")
    except Exception as exc:
        logger.debug("parsecheck.annotation_unreadable", error=str(exc))
        return None
    return uri if isinstance(uri, str) and uri else None


def expected_text(spec: ResumeSpec) -> str:
    """Everything the template will print, as plain text.

    Built from the typed spec rather than by re-rendering and stripping the
    LaTeX, for the same reason ``_spec_corpus`` is: the markup is the thing
    under test, so deriving the expectation from it would compare the document
    against itself and pass unconditionally.
    """
    profile = spec.profile
    personal = profile.personal
    parts: list[str] = [personal.name, personal.title, personal.phone, personal.location]
    if personal.email:
        parts.append(personal.email)
    if personal.linkedin_url:
        parts.append(personal.linkedin_display or personal.linkedin_url)
    if personal.github_url:
        parts.append(personal.github_display or personal.github_url)

    parts.extend(heading for heading, _ in expected_sections(spec))

    parts.append(latex_to_display_text(profile.summary))
    for job in profile.experience:
        parts.extend((job.title, job.company, job.dates, latex_to_display_text(job.subtitle)))
        parts.extend(latex_to_display_text(bullet) for bullet in job.bullets)
    for project in spec.projects:
        parts.append(latex_to_display_text(project.title))
        parts.append(project.github)
        parts.extend(latex_to_display_text(bullet) for bullet in project.bullets)
    for skill in profile.skills:
        parts.append(f"{skill.name}: {latex_to_display_text(skill.content)}")
    for entry in profile.education:
        parts.append(f"{entry.degree} {entry.institution} {entry.dates}")

    return normalize(" \n ".join(part for part in parts if part))


def expected_sections(spec: ResumeSpec) -> list[tuple[str, str]]:
    """The headings this particular spec will actually produce."""
    sources: dict[str, object] = {
        "summary": spec.profile.summary,
        "experience": spec.profile.experience,
        "projects": spec.projects,
        "skills": spec.profile.skills,
        "education": spec.profile.education,
    }
    return [(heading, key) for heading, key in SECTION_HEADINGS if sources[key]]


def expected_links(spec: ResumeSpec) -> list[str]:
    r"""Every ``\href`` target the renderer will emit, de-duplicated."""
    personal = spec.profile.personal
    targets: list[str] = []
    if personal.email:
        targets.append(f"mailto:{personal.email}")
    for url in (personal.linkedin_url, personal.github_url):
        if url:
            targets.append(url)
    targets.extend(project.github_url for project in spec.projects)
    return list(dict.fromkeys(targets))


def tokenize(text: str) -> set[str]:
    """Comparable words: normalised, trimmed of edge punctuation, 4+ chars.

    Punctuation is stripped from the ends but kept inside, so ``scikit-learn``,
    ``node.js`` and an email address each stay one token. That matters: a
    parser that splits them is doing the thing this check exists to catch, and
    a tokenizer that split them first would hide it.

    Normalisation happens here rather than at the call sites. The pattern only
    starts a token at a lowercase letter or digit, so handing it raw text
    silently yields ``ython`` for ``Python`` -- a missing term on one side and
    a spurious one on the other, from a single forgotten call.

    Dashes are cut first. LaTeX turns ``--`` into an en dash, so the source's
    ``8--13`` and the page's ``8-13`` are the same authored text written twice;
    treating both as a separator makes them agree. A *single* hyphen is left
    alone, because ``scikit-learn`` is one word to a keyword scanner and
    splitting it would quietly turn a real match into two false ones.
    """
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(_DASH_RE.sub(" ", normalize(text))):
        token = raw.strip("./&_-")
        if len(token) >= MIN_TOKEN_CHARS:
            tokens.add(token)
    return tokens


def check_parse(pdf_bytes: bytes, spec: ResumeSpec) -> ParseCheck:
    """Compare the compiled PDF against the page it was supposed to be."""
    text = extract_pdf_text(pdf_bytes)
    normalised = normalize(text)
    links = tuple(extract_pdf_links(pdf_bytes))

    expected = tokenize(expected_text(spec))
    extracted = tokenize(normalised)
    missing = sorted(expected - extracted)
    coverage = 1.0 if not expected else (len(expected) - len(missing)) / len(expected)

    findings: list[Finding] = []
    if not text.strip():
        findings.append(
            Finding(
                "no_text",
                "fail",
                "No text could be extracted from the PDF at all. An applicant "
                "tracking system would read this resume as an empty document.",
            )
        )
    elif len(normalised) < MIN_CHARACTERS:
        findings.append(
            Finding(
                "little_text",
                "fail",
                f"Only {len(normalised)} characters could be extracted, below the "
                f"{MIN_CHARACTERS}-character floor. Most of the page is not "
                "machine-readable.",
            )
        )
    else:
        findings.extend(_missing_term_findings(coverage, missing, text, normalised))
        findings.extend(_encoding_findings(text))

    findings.extend(_link_findings(links, expected_links(spec)))

    check = ParseCheck(
        characters=len(normalised),
        words=len(normalised.split()),
        term_coverage=round(coverage, 4),
        expected_terms=len(expected),
        missing_terms=tuple(missing[:MAX_REPORTED_TERMS]),
        links=links,
        findings=tuple(findings),
    )
    logger.debug(
        "parsecheck.completed",
        status=check.status,
        coverage=check.term_coverage,
        characters=check.characters,
        findings=[finding.code for finding in check.findings],
    )
    return check


def _expand_ligatures(text: str) -> str:
    """Rewrite typographic ligatures as the letters they stand for."""
    for char, letters in LIGATURE_MAP.items():
        text = text.replace(char, letters)
    return text


def _appears_split(term: str, text: str) -> bool:
    """Is the term present but with a space somewhere inside it?

    Tries every single-space insertion rather than searching for a general
    pattern, because the position matters: it is the wide kern pairs (``F r``,
    ``T o``, ``V a``) that an extractor mistakes for a word boundary, and only
    the whole word tells you whether it is that or two genuinely separate ones.
    """
    return any(f"{term[:index]} {term[index:]}" in text for index in range(1, len(term)))


def _missing_term_findings(
    coverage: float, missing: list[str], text: str, normalised: str
) -> list[Finding]:
    """Name the *cause* of each loss, not just the size of it.

    A single "3.4% of words are missing" tells a reader nothing they can act
    on. These three causes have three different remedies -- change the font,
    accept a parser quirk, or look at the content -- and lumping them together
    was the difference between a number and a diagnosis.
    """
    if not missing:
        return []

    folded = tokenize(_expand_ligatures(text))
    ligature_hit = [term for term in missing if term in folded]
    rest = [term for term in missing if term not in folded]
    split_hit = [term for term in rest if _appears_split(term, normalised)]
    absent = [term for term in rest if term not in split_hit]

    # Losing a tenth of the page is a failure whatever caused it: the resume no
    # longer says what it was scored for saying.
    severity: Severity = "fail" if coverage < FAIL_COVERAGE else "warn"
    findings: list[Finding] = []

    if ligature_hit:
        findings.append(
            Finding(
                "ligatures",
                severity,
                f"{len(ligature_hit)} word(s) are typeset with ligatures and extract as "
                f"single glyphs, not letters ({', '.join(ligature_hit[:6])}). A scanner "
                'searching for "classification" will not match "classi<fi>cation". '
                "Fixable in the template, by turning common ligatures off.",
            )
        )
    if split_hit:
        findings.append(
            Finding(
                "split_words",
                severity,
                f"{len(split_hit)} word(s) extract with a space inside them "
                f"({', '.join(split_hit[:6])}). Wide kerning pairs read as word "
                "boundaries to a text extractor, so the keyword is on the page and "
                "not in the parsed text.",
            )
        )
    if absent:
        findings.append(
            Finding(
                "text_loss",
                severity,
                f"{len(absent)} word(s) are on the page but could not be extracted at "
                f"all ({', '.join(absent[:6])}); {coverage:.1%} of the resume round-"
                "tripped. Words an ATS cannot read cannot be scored, so the resume "
                "will match a posting less well than its content suggests.",
            )
        )
    if coverage >= WARN_COVERAGE and severity == "warn":
        # Above the warning threshold the losses are noise, not a defect worth
        # interrupting for -- but they still belong in ``missing_terms``.
        return []
    return findings


def _encoding_findings(text: str) -> list[Finding]:
    findings: list[Finding] = []
    undecoded = len(_UNDECODED_RE.findall(text))
    if undecoded:
        findings.append(
            Finding(
                "undecoded_glyphs",
                "warn",
                f"{undecoded} character(s) extracted as unnamed glyphs. The font "
                "is missing a character map, so an ATS sees symbols where the "
                "page shows letters.",
            )
        )
    glued = _LONG_WORD_RE.findall(text.lower())
    if glued:
        findings.append(
            Finding(
                "missing_spaces",
                "warn",
                f"{len(glued)} run(s) of more than {MAX_WORD_CHARS} letters with no "
                f"space, starting with {glued[0][:40]!r}. Words are running together "
                "on extraction, which breaks keyword matching.",
            )
        )
    return findings


def _link_findings(found: tuple[str, ...], expected: list[str]) -> list[Finding]:
    """Links are reported, never failed.

    A resume with unclickable links is still a readable resume, and the cause
    is usually the engine rather than the content -- so this must not be the
    thing that stops a document being called parseable.
    """
    findings: list[Finding] = []
    missing = [target for target in expected if target not in found]
    if missing:
        findings.append(
            Finding(
                "links_not_clickable",
                "warn",
                f"{len(missing)} of {len(expected)} link(s) are printed but not "
                f"clickable, starting with {missing[0]}. A reader can retype them; "
                "an automated one will not.",
            )
        )
    malformed = [uri for uri in found if not uri.startswith(("http://", "https://", "mailto:"))]
    if malformed:
        findings.append(
            Finding(
                "link_scheme",
                "warn",
                f"{len(malformed)} link target(s) use an unexpected scheme, "
                f"starting with {malformed[0][:80]}.",
            )
        )
    return findings
