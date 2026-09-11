"""Is this job description the whole job description?

``docs/resume_automation_spec.md`` section 1, step 1: "Confirm the JD text is
complete (not truncated). If a JD is cut off mid-sentence or missing a
Requirements/Qualifications section, say so explicitly and ask for the full
text rather than guessing at what's missing."

That step exists because of how job descriptions are actually acquired --
selected with a mouse on a careers page and pasted. A selection that stops at a
fold, a posting behind a "Show more" control, or a description whose
requirements live in a collapsed panel all produce text that reads like a
complete posting and is not one.

The consequence is specific and silent. Everything downstream is honest about
what it was given: the matcher ranks projects against the terms present, and
the ATS score reports the requirements it found. Neither can know about a
requirement that was never pasted, so a truncated posting produces a *higher*
score than the real one -- the missing half is all the demands the candidate
was never measured against. The failure looks like good news.

So this module reports, and does not refuse. A short posting is sometimes
genuinely short, an internal blurb is a legitimate thing to match against, and
refusing to score one would trade a silent overstatement for a hard stop on
work the user has every right to do. Every signal here names what it saw and
leaves the judgement where it belongs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from resume_tailor.domain.matching import normalize

#: Below this, the text cannot be a complete posting -- a real one carries a
#: role summary, responsibilities and qualifications. Deliberately well under
#: the shortest plausible posting: this should fire on a fragment, not on a
#: terse one.
MIN_POSTING_CHARS = 400

#: Literal endings that a truncated paste leaves behind. These are the strongest
#: signal in the module -- nobody ends a job posting with "Show more".
_TRUNCATION_MARKER_RE = re.compile(
    r"(?:\.\.\.|…|\[…\]|\[\.\.\.\]"
    r"|\b(?:read|show|see|view|learn)\s+(?:more|full(?:\s+\w+)?)"
    r"|\bcontinue reading\b)\s*$",
    re.IGNORECASE,
)

#: A posting that ends on one of these ends mid-thought. Kept to words that
#: cannot close a sentence in English, so a bullet list ending "Strong SQL
#: skills" -- no full stop, and perfectly complete -- is not flagged.
_CONTINUATION_WORDS = frozenset(
    {
        "and",
        "or",
        "with",
        "to",
        "for",
        "of",
        "in",
        "on",
        "at",
        "by",
        "from",
        "as",
        "the",
        "a",
        "an",
        "including",
        "such",
        "using",
        "who",
        "that",
        "which",
        "while",
        "plus",
        "both",
        "either",
        "neither",
        "between",
    }
)

#: Trailing punctuation that itself says the sentence was still going.
_DANGLING_PUNCTUATION = (",", ";", "-", "\u2013", "\u2014", "(", "[", "/", "&", "+")

#: Quotes and brackets around the final word, so that ``(and`` is read as
#: the word it wraps.
_WORD_EDGE_RE = re.compile(r"^\W+|\W+$")

#: Characters that legitimately end a posting.
_TERMINAL_PUNCTUATION = (".", "!", "?", ":", ")", "]", '"', "'", "”", "%")

#: Headings that open the section this check is looking for. Broader than
#: ``ats._REQUIRED_HEADING_RE``, which is answering a different question
#: (required versus preferred, within a posting already assumed complete);
#: here the question is only whether the posting states its asks anywhere, so
#: "skills", "what you bring" and "experience" count too.
_REQUIREMENTS_RE = re.compile(
    r"\b(?:requirements?|qualifications?|must[\s-]haves?|minimum qualifications"
    r"|basic qualifications|what you\W?ll need|what you need|what you bring"
    r"|who you are|about you|skills? (?:and|&) experience|required skills"
    r"|desired skills|your (?:profile|experience|skills)|we\W?re looking for"
    r"|you (?:will )?(?:have|bring)|essential|the ideal candidate)\b",
    re.IGNORECASE,
)

NOTE = (
    "Completeness is judged from the shape of the text, not its meaning: how it "
    "ends, how long it is, and whether it states its requirements anywhere. A "
    "truncated posting scores higher than the real one, because the half that "
    "was not pasted is all the demands nobody measured against."
)


@dataclass(frozen=True)
class PostingSignal:
    """One reason to doubt the posting is complete."""

    code: str
    detail: str


@dataclass(frozen=True)
class PostingCheck:
    """What the shape of the pasted text says about its completeness."""

    characters: int
    words: int
    has_requirements_section: bool
    signals: tuple[PostingSignal, ...]
    note: str = NOTE

    @property
    def complete(self) -> bool:
        """True when nothing about the text suggests it was cut short."""
        return not self.signals


def _ends_mid_sentence(text: str) -> str | None:
    """Why the final line looks unfinished, or ``None`` if it looks finished.

    Precision matters more than recall here. A posting whose last line is a
    bullet with no full stop is the normal case, not a truncated one, so an
    ending is only called unfinished when it ends on a word or a mark that
    cannot close a thought.
    """
    stripped = text.rstrip()
    if not stripped:
        return None
    if stripped.endswith(_DANGLING_PUNCTUATION):
        return f"it ends on {stripped[-1]!r}"
    if stripped.endswith(_TERMINAL_PUNCTUATION):
        return None
    last_word = _WORD_EDGE_RE.sub("", stripped.split()[-1]).lower()
    if last_word in _CONTINUATION_WORDS:
        return f"it ends on the word {last_word!r}"
    return None


def check_posting(jd_text: str) -> PostingCheck:
    """Report every sign that this posting was pasted incomplete."""
    normalised = normalize(jd_text)
    has_requirements = bool(_REQUIREMENTS_RE.search(jd_text))
    signals: list[PostingSignal] = []

    if _TRUNCATION_MARKER_RE.search(jd_text.rstrip()):
        signals.append(
            PostingSignal(
                "truncation_marker",
                "The text ends with a 'show more' or ellipsis marker, so the rest of "
                "the posting is still on the page it was copied from. Expand it and "
                "paste again.",
            )
        )
    else:
        unfinished = _ends_mid_sentence(jd_text)
        if unfinished is not None:
            signals.append(
                PostingSignal(
                    "ends_mid_sentence",
                    f"The text stops mid-sentence -- {unfinished}. The selection "
                    "probably missed the end of the posting.",
                )
            )

    if len(normalised) < MIN_POSTING_CHARS:
        signals.append(
            PostingSignal(
                "too_short",
                f"At {len(normalised)} characters this is shorter than a full posting "
                f"(under {MIN_POSTING_CHARS}). Scores from a fragment describe the "
                "fragment, not the job.",
            )
        )

    if not has_requirements:
        signals.append(
            PostingSignal(
                "no_requirements_section",
                "No requirements or qualifications section was found. If the posting "
                "keeps its requirements behind a tab or a collapsed panel, they are "
                "missing here -- and they are the part that gets scored.",
            )
        )

    return PostingCheck(
        characters=len(normalised),
        words=len(normalised.split()),
        has_requirements_section=has_requirements,
        signals=tuple(signals),
    )
