"""ATS match scoring: a job description against what the system knows about you.

Independent of resume generation by construction -- nothing in this module can
reach the renderer, the template or the PDF engine, and no function here
returns anything a resume could be built from. It answers one question: how
well does this job description line up with the stored candidate knowledge, and
what is missing.

Why it is arithmetic rather than a model
----------------------------------------
Three properties are worth more here than sophistication:

*Deterministic.* The same job description and the same knowledge produce the
same report, today and next month. Nothing consults the clock (durations are
resolved once, at extraction time, and stored), nothing consults a network, and
every list is explicitly ordered rather than left to set iteration.

*Explainable.* Every requirement says what it matched, which candidate term
matched it and which source that term came from. A score you cannot audit is a
number, not information.

*Not inflatable.* A requirement is credited **once**, no matter how many times
the job description repeats it. Occurrence counts are reported because they are
interesting, and are deliberately not an input to the score. This is the
central design constraint: a scorer that counts frequency rewards a job
description for being repetitive, which tells the candidate nothing.

The honest limitation, stated in :data:`ATS_NOTE` and repeated in every
response: this is literal and alias-aware term matching, not comprehension. It
mirrors how most real ATS keyword screens work, which is the point, but a low
score means "these words are not in your knowledge base", not "you cannot do
this job".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from resume_tailor.domain.matching import (
    STOPWORDS,
    count_matches,
    extract_meaningful_terms,
    keyword_pattern,
    normalize,
)
from resume_tailor.domain.models import StrictModel
from resume_tailor.domain.vocabulary import (
    CERTIFICATION_TERMS,
    DOMAINS,
    EDUCATION_TERMS,
    RESPONSIBILITIES,
    SKILLS,
    TOOLS,
    related_terms,
)

MatchStatus = Literal["exact", "related", "missing"]
GateStatus = Literal["pass", "fail", "unverified"]

#: Category weights. Fixed, published in every response, and summing to 1.0.
#:
#: Skills lead because they are what a screen actually filters on; keywords sit
#: last because that category is the catch-all for terms no vocabulary
#: recognised, and is therefore the noisiest.
WEIGHTS: dict[str, float] = {
    "skills": 0.30,
    "tools": 0.20,
    "experience": 0.15,
    "responsibilities": 0.15,
    "education": 0.10,
    "keywords": 0.10,
}

#: Presentation order, and the order every list in a report is built in.
CATEGORY_ORDER: tuple[str, ...] = tuple(WEIGHTS)

#: Human-readable label per category, so the UI does not invent its own.
CATEGORY_LABELS: dict[str, str] = {
    "skills": "Skills",
    "tools": "Tools & technologies",
    "experience": "Experience requirements",
    "responsibilities": "Responsibilities & domain",
    "education": "Education & certifications",
    "keywords": "Other keywords & phrases",
}

#: Credit per status. A related match is worth half: the candidate has adjacent
#: evidence (PyTorch against a TensorFlow requirement), which is neither a hit
#: nor a blank. Rounding it up to 1.0 would overstate; down to 0.0 would tell
#: the candidate to learn something they half know.
CREDIT: dict[str, float] = {"exact": 1.0, "related": 0.5, "missing": 0.0}

Priority = Literal["required", "preferred"]

#: Weight of one requirement within its category, by how the posting ranks it.
#:
#: A posting that lists Kubernetes under "Nice to have" is saying it will not
#: screen anyone out for lacking it, and a score that treats that gap like a
#: missing "Required" skill overstates what is at stake. A quarter is a
#: judgement, not a measurement: large enough that a preferred skill you have
#: still moves the score, small enough that a long wish list cannot drag down a
#: candidate who meets every requirement.
PRIORITY_WEIGHTS: dict[str, float] = {"required": 1.0, "preferred": 0.25}

#: Score at or above which each band starts.
BANDS: tuple[tuple[int, str], ...] = (
    (80, "Strong match"),
    (60, "Moderate match"),
    (40, "Partial match"),
    (0, "Weak match"),
)

#: Where a failed hard filter caps the score: the top of "Weak match". A screen
#: that requires a master's degree rejects a bachelor's holder before it reads
#: a single skill, so no amount of keyword coverage should make that
#: application look like a moderate or strong match. Capped rather than zeroed:
#: below the cap, the score still says how much else lines up.
GATE_CAP = 39

ATS_NOTE = (
    "This score is arithmetic, not judgement. Requirements are read out of the "
    "job description by literal and alias-aware term matching, compared against "
    "your stored knowledge, and each requirement is counted exactly once -- "
    "repeating a word in the job description cannot raise the score. Requirements "
    "the posting marks as preferred or nice-to-have count for less than required "
    "ones (see priority_weights). A required degree or years of experience that "
    "your record clearly falls short of caps the score at the top of Weak match, "
    "because a screen that filters on it rejects the application whatever else "
    "matches. A missing "
    "requirement means the term is absent from your knowledge base, which is not "
    "the same as being absent from your experience: add the missing detail under "
    "Profile knowledge and re-run the check."
)

#: Keyword requirements are capped. The catch-all category otherwise grows with
#: the length of the job description, and a 2,000-word posting would drown the
#: five requirements that actually matter.
MAX_KEYWORD_REQUIREMENTS = 25

#: A year requirement outside this range is a typo, a salary, or a year of
#: incorporation -- not a demand for experience.
_MIN_YEARS, _MAX_YEARS = 1, 40

_YEARS_RANGE_RE = re.compile(
    # The alternation carries a hyphen, a double hyphen, an en dash, an em dash and the word 'to' -- every range separator a posting actually uses.
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|--|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b",  # noqa: RUF001 - the en/em dash is data, not prose: postings write date ranges with it
    re.IGNORECASE,
)
_YEARS_RE = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:years?|yrs?)\b", re.IGNORECASE)

#: Tokens too short or too common to carry a partial match on their own.
_MIN_PARTIAL_TOKEN = 4

#: A word shared by at least this many multi-word vocabulary terms is too
#: common in the field to stand in for any one of them.
_GENERIC_TOKEN_TERMS = 3


def _build_generic_tokens() -> frozenset[str]:
    """Words that cannot earn partial credit for a phrase on their own.

    Derived from the vocabulary rather than listed by hand, so it grows with
    it. "data" is part of eighteen vocabulary phrases, "model" of eight and
    "management" of seven; without this, a candidate with *any* data experience
    earned half credit for "data governance", "model risk management" and
    "stakeholder management" alike -- the score claiming evidence the candidate
    never showed.
    """
    vocabulary = SKILLS | TOOLS | DOMAINS | RESPONSIBILITIES | EDUCATION_TERMS | CERTIFICATION_TERMS
    counts: dict[str, int] = {}
    for term in vocabulary:
        words = term.split()
        if len(words) < 2:
            continue
        for word in set(words):
            counts[word] = counts.get(word, 0) + 1
    return frozenset(word for word, count in counts.items() if count >= _GENERIC_TOKEN_TERMS)


#: See :func:`_build_generic_tokens`.
GENERIC_TOKENS: frozenset[str] = _build_generic_tokens()


# --- the candidate side -----------------------------------------------------


@dataclass(frozen=True)
class NamedCorpus:
    """One provenance-tagged body of candidate text."""

    name: str
    """``knowledge``, ``profile`` or ``bank`` -- reported on every match so a
    hit can be traced back to the file it came from."""
    text: str
    """Already normalised by :func:`resume_tailor.domain.matching.normalize`."""


@dataclass(frozen=True)
class CandidateCorpus:
    """Everything the system knows about the candidate, ready to match against.

    Assembled by the service from all three knowledge surfaces. Keeping them
    separate rather than concatenating one string is what lets a report say
    *where* a match came from, which is the difference between "you match" and
    "you match, and here is the line that proves it".
    """

    sources: tuple[NamedCorpus, ...]
    experience_months: int = 0
    """Dated experience across every source, overlapping roles counted once."""
    experience_sources: tuple[str, ...] = ()
    """Which sources contributed a dated range. Reported on a years match, so
    the answer is not hardcoded to one file that may not be where it came
    from."""
    education_text: str = ""
    """Education lines only, normalised. Kept apart from ``sources`` because
    degree words are ordinary English everywhere else -- a "scrum master" or a
    project on "master data" is not a master's degree, and the degree gate
    must not read one as it."""
    location: str = ""
    """Where the candidate is based, as the profile header states it."""

    @property
    def is_empty(self) -> bool:
        return not any(source.text.strip() for source in self.sources)

    def find(self, term: str) -> list[str]:
        """Names of the sources containing ``term``, in the order given."""
        return [source.name for source in self.sources if count_matches(source.text, term) > 0]


# --- report models ----------------------------------------------------------


class Requirement(StrictModel):
    """One thing the job description asks for, and how the candidate stands."""

    term: str
    """Normalised requirement text -- the thing being looked for."""
    display: str
    """The requirement as a human should read it."""
    status: MatchStatus
    matched_term: str = ""
    """The candidate-side term that satisfied it. Empty when missing."""
    matched_sources: list[str] = Field(default_factory=list)
    """Which of ``knowledge`` / ``profile`` / ``bank`` contained the match."""
    occurrences: int = Field(default=0, ge=0)
    """How often the job description mentions it. Reported for context and
    deliberately excluded from the score -- see the module docstring."""
    detail: str = ""
    """Why this status, in words, for anything the term alone does not explain
    (the years comparison, most importantly)."""
    priority: Priority = "required"
    """``preferred`` when every mention sits under a nice-to-have heading or in
    a sentence that says so; ``required`` otherwise, including when unsure."""

    @property
    def credit(self) -> float:
        return CREDIT[self.status]

    @property
    def priority_weight(self) -> float:
        return PRIORITY_WEIGHTS[self.priority]


class CategoryBreakdown(StrictModel):
    """One scored factor of the match."""

    name: str
    label: str
    weight: float = Field(ge=0.0, le=1.0)
    """This category's weight in the overall score: its base weight from
    :data:`WEIGHTS`, scaled by the average priority weight of its requirements.
    A category the posting only mentions as nice-to-have carries a fraction of
    its usual weight, so a wish list cannot sink the score through a category
    of its own."""
    score: float = Field(ge=0.0, le=1.0)
    """Priority-weighted average credit of the requirements."""
    requirements: list[Requirement] = Field(default_factory=list)

    @property
    def preferred(self) -> list[Requirement]:
        return [r for r in self.requirements if r.priority == "preferred"]

    @property
    def exact(self) -> list[Requirement]:
        return [r for r in self.requirements if r.status == "exact"]

    @property
    def related(self) -> list[Requirement]:
        return [r for r in self.requirements if r.status == "related"]

    @property
    def missing(self) -> list[Requirement]:
        return [r for r in self.requirements if r.status == "missing"]


class Gate(StrictModel):
    """One hard filter, reported pass/fail rather than as a percentage.

    A screen that requires a master's degree rejects a bachelor's holder
    whatever their keyword coverage, so averaging this into a category would
    understate it. ``unverified`` means the stored record cannot answer the
    question; it never caps the score, because a missing fact is not a
    shortfall.
    """

    name: Literal["degree", "experience", "location"]
    label: str
    status: GateStatus
    required: str
    """What the posting asks for, in words."""
    found: str = ""
    """What the candidate side holds, in words. Empty when nothing is recorded."""
    detail: str = ""


class AtsReport(StrictModel):
    """The complete answer. No resume, no project selection, no side effects."""

    score: int = Field(ge=0, le=100)
    band: str
    breakdown: list[CategoryBreakdown]
    missing_requirements: list[str] = Field(default_factory=list)
    weak_requirements: list[str] = Field(default_factory=list)
    """Requirements matched only by a related term -- half credit, and the
    cheapest gaps to close."""
    matched_requirements: list[str] = Field(default_factory=list)
    requirement_count: int = Field(default=0, ge=0)
    knowledge_version: str = ""
    bank_version: str = ""
    note: str = ATS_NOTE
    weights: dict[str, float] = Field(default_factory=lambda: dict(WEIGHTS))
    priority_weights: dict[str, float] = Field(default_factory=lambda: dict(PRIORITY_WEIGHTS))
    gates: list[Gate] = Field(default_factory=list)
    """Degree, years and location, for whichever the posting states as required."""
    uncapped_score: int = Field(default=0, ge=0, le=100)
    """The weighted score before any gate cap -- what the breakdown adds up to."""
    capped: bool = False
    """True when a failed gate actually lowered the score."""
    gate_cap: int = GATE_CAP


# --- requirement extraction -------------------------------------------------


@dataclass(frozen=True)
class _Spec:
    """A requirement before it has been compared with anything."""

    term: str
    display: str
    occurrences: int
    priority: Priority = "required"


# --- required vs preferred --------------------------------------------------

#: Opens a nice-to-have section. Anchored to the start of the line so that
#: "Python (preferred)" reads as one preferred item, not as a heading that turns
#: the rest of the posting preferred.
_PREFERRED_HEADING_RE = re.compile(
    r"^(?:preferred|desired|desirable|bonus|optional|pluses|plus points"
    r"|nice[\s-]to[\s-]haves?|good[\s-]to[\s-]haves?)\b",
    re.IGNORECASE,
)

#: Opens a section of things the posting needs. Only consulted for a heading
#: with no colon: any other short line ending in one ("Benefits:", "Tech
#: stack:") closes a preferred section too, since a posting rarely puts
#: anything but its wish list under the wish-list heading.
_REQUIRED_HEADING_RE = re.compile(
    r"^(?:required|requirements|must[\s-]haves?|minimum|basic|essential|mandatory"
    r"|qualifications|responsibilities|key responsibilities"
    # `\W?` rather than an apostrophe: Word and Google Docs emit the curly one,
    # and this keeps the pattern ASCII while matching either.
    r"|what you\W?ll (?:need|do|bring)|who you are|about you|about the role)\b",
    re.IGNORECASE,
)

#: A sentence that marks itself optional, wherever it sits.
_PREFERRED_CUE_RE = re.compile(
    r"\b(?:preferred|preferably|ideally|desirable|advantageous|bonus"
    r"|an? (?:big |strong |definite )?(?:plus|advantage)"
    r"|nice[\s-]to[\s-]have|good[\s-]to[\s-]have|(?:is|are|would be) welcome)\b",
    re.IGNORECASE,
)

#: A sentence that marks itself required. Beats a preferred cue in the same
#: sentence, and beats a preferred heading above it.
_REQUIRED_CUE_RE = re.compile(
    r"\b(?:required|requires?|must|mandatory|essential|minimum)\b", re.IGNORECASE
)

_BULLET_RE = re.compile(r"^\s*[-*•·‣●]")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")
_MAX_HEADING_WORDS = 6


@dataclass(frozen=True)
class JdSentence:
    """One sentence of a job description, and how the posting ranks it."""

    text: str
    """Normalised by :func:`resume_tailor.domain.matching.normalize`."""
    priority: Priority


def _heading_priority(line: str) -> Priority | None:
    """The section a heading line opens, or ``None`` if it is not a heading."""
    stripped = line.strip()
    body = stripped.rstrip(":").strip()
    if not body or ":" in body or _BULLET_RE.match(stripped):
        return None
    if len(body.split()) > _MAX_HEADING_WORDS:
        return None
    if _PREFERRED_HEADING_RE.match(body):
        return "preferred"
    if stripped.endswith(":") or _REQUIRED_HEADING_RE.match(body):
        return "required"
    return None


def split_by_priority(jd_text: str) -> list[JdSentence]:
    """Every sentence of the posting, tagged required or preferred.

    Two signals, both read literally. A heading ("Nice to have", "Preferred
    qualifications:") sets the section every following line belongs to until
    the next heading. A cue inside a sentence ("... is a plus", "X is
    required") overrides the section for that sentence alone.

    Anything ambiguous is required. That is the direction that cannot inflate
    a score: mistaking a wish for a need makes a gap cost more than it should,
    while the reverse would hide a gap the screen will actually apply.
    """
    region: Priority = "required"
    sentences: list[JdSentence] = []
    for line in jd_text.split("\n"):
        heading = _heading_priority(line)
        if heading is not None:
            region = heading
        for sentence in _CLAUSE_SPLIT_RE.split(line.strip()):
            if not sentence:
                continue
            priority: Priority = region
            if _REQUIRED_CUE_RE.search(sentence):
                priority = "required"
            elif _PREFERRED_CUE_RE.search(sentence):
                priority = "preferred"
            sentences.append(JdSentence(text=normalize(sentence), priority=priority))
    return sentences


def _priority_of(sentences: list[JdSentence], mentions: Callable[[JdSentence], bool]) -> Priority:
    """``preferred`` only if every sentence that mentions the requirement is.

    A requirement named once as a need and once as a wish is a need. One found
    in the whole text but in no single sentence -- a phrase broken across a
    line -- is required too, for the same reason as everything ambiguous here.
    """
    found = [sentence.priority for sentence in sentences if mentions(sentence)]
    return "preferred" if found and all(p == "preferred" for p in found) else "required"


def _term_priority(sentences: list[JdSentence], term: str) -> Priority:
    return _priority_of(sentences, lambda sentence: count_matches(sentence.text, term) > 0)


def _year_priority(sentences: list[JdSentence], years: int) -> Priority:
    return _priority_of(
        sentences, lambda sentence: years in extract_year_requirements(sentence.text)
    )


def _vocabulary_requirements(
    haystack: str, vocabulary: frozenset[str], sentences: list[JdSentence]
) -> list[_Spec]:
    """Every vocabulary term the job description literally mentions.

    Sorted rather than iterated in set order: a frozenset's iteration order is
    stable within a process but not across runs, and a report whose rows move
    between runs cannot be diffed.
    """
    found: list[_Spec] = []
    for term in sorted(vocabulary):
        occurrences = count_matches(haystack, term)
        if occurrences:
            found.append(
                _Spec(
                    term=term,
                    display=term,
                    occurrences=occurrences,
                    priority=_term_priority(sentences, term),
                )
            )
    return found


def extract_year_requirements(jd_text: str) -> list[int]:
    """Distinct "N years" demands, smallest first.

    A range ("3-5 years") is read as its lower bound, because that is what the
    posting actually requires; treating it as five would invent a bar the
    employer did not set.
    """
    years: set[int] = set()
    consumed: list[tuple[int, int]] = []
    for found in _YEARS_RANGE_RE.finditer(jd_text):
        lower = int(found.group(1))
        if _MIN_YEARS <= lower <= _MAX_YEARS:
            years.add(lower)
        consumed.append(found.span())
    for found in _YEARS_RE.finditer(jd_text):
        # Skip anything already read as a range, so "3-5 years" does not also
        # register a bare "5 years" requirement.
        if any(start <= found.start() < end for start, end in consumed):
            continue
        value = int(found.group(1))
        if _MIN_YEARS <= value <= _MAX_YEARS:
            years.add(value)
    return sorted(years)


def _keyword_requirements(
    jd_text: str, haystack: str, already: set[str], sentences: list[JdSentence]
) -> list[_Spec]:
    """JD terms no vocabulary recognised: the catch-all, so nothing is dropped.

    Reuses the gap-term heuristic the matcher already applies to job
    descriptions, rather than inventing a second definition of "an interesting
    term" that would disagree with the tailoring view.

    Ordered by where the posting first mentions each term, then capped.
    ``extract_meaningful_terms`` returns its terms alphabetically -- which is
    right for a gap list a human reads, and wrong for a list that gets
    truncated, because taking the first 25 of an alphabetical list keeps
    "Airflow" and "AWS" and silently drops "Snowflake" and "Terraform". First
    mention is a defensible proxy for importance (postings lead with what
    matters), and unlike frequency it cannot be gamed by repetition.
    """
    candidates: list[tuple[int, str, str]] = []
    for term in extract_meaningful_terms(jd_text):
        lowered = term.lower()
        if lowered in already or lowered in STOPWORDS or len(lowered) < 3:
            continue
        # "Master's" is the education term "master" in the possessive; scoring
        # it again here would count one requirement twice.
        if re.sub(r"\Ws$", "", lowered) in already:
            continue
        already.add(lowered)
        found = keyword_pattern(lowered).search(jd_text)
        # A term that came out of the JD is in the JD; the fallback only keeps
        # the sort total if the two functions ever disagree about boundaries.
        candidates.append((found.start() if found else len(jd_text), lowered, term))

    candidates.sort()
    return [
        _Spec(
            term=lowered,
            display=display,
            occurrences=count_matches(haystack, lowered),
            priority=_term_priority(sentences, lowered),
        )
        for _, lowered, display in candidates[:MAX_KEYWORD_REQUIREMENTS]
    ]


def extract_requirements(jd_text: str) -> dict[str, list[_Spec]]:
    """Read a job description into requirements, grouped by scoring category.

    A term is claimed by the first category that recognises it and removed from
    consideration for the rest. The vocabularies are disjoint by design (a test
    enforces it), so this is a belt-and-braces guard rather than routine
    filtering -- but the cost of getting it wrong is a term scored twice in two
    weighted categories, which breaks the "credited once" property this module
    is built around.
    """
    haystack = normalize(jd_text)
    sentences = split_by_priority(jd_text)
    claimed: set[str] = set()

    def take(vocabulary: frozenset[str]) -> list[_Spec]:
        found = [
            spec
            for spec in _vocabulary_requirements(haystack, vocabulary, sentences)
            if spec.term not in claimed
        ]
        claimed.update(spec.term for spec in found)
        return found

    skills = take(SKILLS)
    tools = take(TOOLS)
    responsibilities = take(RESPONSIBILITIES | DOMAINS)
    education = take(EDUCATION_TERMS | CERTIFICATION_TERMS)
    # The "Location:" line belongs to the location gate. Reading it for keywords
    # too scored "Bangalore" and "Hybrid" as skills -- counting the location
    # twice, and faulting the candidate for never having written "hybrid".
    keywords = _keyword_requirements(
        _LOCATION_LINE_RE.sub("", jd_text), haystack, claimed, sentences
    )

    experience = [
        _Spec(
            term=f"{years} years experience",
            display=f"{years}+ years of experience",
            occurrences=1,
            priority=_year_priority(sentences, years),
        )
        for years in extract_year_requirements(jd_text)
    ]

    return {
        "skills": skills,
        "tools": tools,
        "experience": experience,
        "responsibilities": responsibilities,
        "education": education,
        "keywords": keywords,
    }


# --- classification ---------------------------------------------------------


def classify(spec: _Spec, corpus: CandidateCorpus) -> Requirement:
    """Decide whether the candidate meets one requirement, and say why.

    Three outcomes, in the order they are tried:

    ``exact``
        The term itself (or one of the matcher's known aliases) is in the
        candidate corpus.

    ``related``
        Either a term the vocabulary marks as adjacent is present -- PyTorch
        against a TensorFlow requirement -- or the requirement is a phrase and
        the candidate has part of it. Half credit.

    ``missing``
        Neither. This is a gap in the *knowledge base*, which the note in every
        report is careful to distinguish from a gap in the person.
    """
    sources = corpus.find(spec.term)
    if sources:
        return Requirement(
            term=spec.term,
            display=spec.display,
            status="exact",
            matched_term=spec.term,
            matched_sources=sources,
            occurrences=spec.occurrences,
            priority=spec.priority,
        )

    for peer in sorted(related_terms(spec.term)):
        peer_sources = corpus.find(peer)
        if peer_sources:
            return Requirement(
                term=spec.term,
                display=spec.display,
                status="related",
                matched_term=peer,
                matched_sources=peer_sources,
                occurrences=spec.occurrences,
                priority=spec.priority,
                detail=f"no direct match; you have {peer}, which is adjacent",
            )

    tokens = [token for token in spec.term.split() if len(token) >= _MIN_PARTIAL_TOKEN]
    if len(spec.term.split()) > 1:
        for token in tokens:
            if token in STOPWORDS or token in GENERIC_TOKENS:
                continue
            token_sources = corpus.find(token)
            if token_sources:
                return Requirement(
                    term=spec.term,
                    display=spec.display,
                    status="related",
                    matched_term=token,
                    matched_sources=token_sources,
                    occurrences=spec.occurrences,
                    priority=spec.priority,
                    detail=f"partial phrase match on '{token}'",
                )

    return Requirement(
        term=spec.term,
        display=spec.display,
        status="missing",
        occurrences=spec.occurrences,
        priority=spec.priority,
    )


def classify_years(spec: _Spec, required_years: int, corpus: CandidateCorpus) -> Requirement:
    """Compare a years-of-experience demand with the stored, dated history.

    Overlapping roles are already counted once by
    :meth:`resume_tailor.domain.knowledge.KnowledgeBase.total_experience_months`,
    so two concurrent jobs cannot double the total. Within a year of the demand
    scores as related rather than missing -- an employer asking for five years
    is not screening out four years and ten months, and pretending otherwise
    would make the report less useful than the person reading it.
    """
    required_months = required_years * 12
    held = corpus.experience_months
    if held == 0:
        return Requirement(
            term=spec.term,
            display=spec.display,
            status="missing",
            occurrences=spec.occurrences,
            priority=spec.priority,
            detail=(
                "no dated experience is stored, so this cannot be verified. Add a "
                "document with dated roles under Profile knowledge."
            ),
        )

    where = ", ".join(corpus.experience_sources) or "your stored knowledge"
    summary = f"{held // 12} year(s) {held % 12} month(s) of dated experience in {where}"
    if held >= required_months:
        status: MatchStatus = "exact"
    elif held >= required_months - 12:
        status = "related"
    else:
        status = "missing"
    return Requirement(
        term=spec.term,
        display=spec.display,
        status=status,
        matched_term=f"{held} months" if status != "missing" else "",
        matched_sources=list(corpus.experience_sources) if status != "missing" else [],
        occurrences=spec.occurrences,
        priority=spec.priority,
        detail=summary,
    )


# --- hard filters -----------------------------------------------------------
#
# Degree level, years of experience and location are checked pass/fail and
# apart from the weighted categories, because an ATS usually applies them as
# filters before it reads a single skill. Only *required* demands gate: a
# posting that says "Master's preferred" is not filtering on it.

#: Degree levels, lowest first. A higher degree satisfies a demand for a lower one.
DEGREE_LEVELS: dict[int, str] = {1: "bachelor's degree", 2: "master's degree", 3: "doctorate"}

#: Each level's spellings. "master" and "bachelor" only count with degree
#: context ("master's", "master of", "bachelor degree"): alone they are
#: ordinary words, and "Scrum Master certification required" is not a demand
#: for a master's degree.
_DEGREE_LEVEL_RES: tuple[tuple[int, re.Pattern[str]], ...] = (
    (3, re.compile(r"\b(?:ph\.?\s?d\.?s?|doctorate|doctoral)(?![a-z])", re.IGNORECASE)),
    (
        2,
        re.compile(
            r"\b(?:master\W?s\b|master\s+(?:of|in|degree)\b|m\.?tech\b|m\.?sc\b|m\.s\."
            r"|m\.e\.|mca\b|mba\b|m\.?com\b|post\W?graduate\b)",
            re.IGNORECASE,
        ),
    ),
    (
        1,
        re.compile(
            r"\b(?:bachelor\W?s\b|bachelor\s+(?:of|in|degree)\b|b\.?tech\b|b\.?sc\b|b\.s\."
            r"|b\.e\.|b\.a\.|bca\b|b\.?com\b|undergraduate\b)",
            re.IGNORECASE,
        ),
    ),
)

#: "A degree in computer science": a degree of unstated level, read as the
#: lowest. "A high degree of ownership" is not a degree.
_BARE_DEGREE_RE = re.compile(r"\bdegree\b(?!\s+of\b)", re.IGNORECASE)

#: "Or equivalent experience" turns a degree demand from a filter into a
#: preference, so a sentence that says it does not gate.
_EQUIVALENT_RE = re.compile(r"\bequivalent\b", re.IGNORECASE)

#: A "Location:" line -- where most postings state it outright.
_LOCATION_LINE_RE = re.compile(
    r"^\s*(?:job\s+|work\s+|office\s+)?locations?\s*[:|]\s*(\S[^\n]{0,100})$",
    re.IGNORECASE | re.MULTILINE,
)

#: A location stated in a sentence instead. The capture stops at the end of
#: the clause, so "based in Pune. You will..." yields "Pune".
_BASED_IN_RE = re.compile(
    r"\b(?:based (?:in|out of)|located in|on\W?site (?:in|at)|relocate to)\s+([^.;:\n()]{2,60})",
    re.IGNORECASE,
)

#: A role that is remote outright. A bare "remote" is not enough: "work with
#: remote teams" says nothing about where this person sits.
_REMOTE_ROLE_RE = re.compile(
    r"\b(?:fully remote|remote\W?first|remote (?:role|position|job|opportunity)"
    r"|work from (?:home|anywhere))\b|\b100% remote\b",
    re.IGNORECASE,
)

#: Renamed cities, so "Bengaluru" in a profile matches "Bangalore" in a posting.
_CITY_ALIASES: tuple[tuple[str, str], ...] = (
    ("bengaluru", "bangalore"),
    ("mumbai", "bombay"),
    ("chennai", "madras"),
    ("kolkata", "calcutta"),
    ("gurugram", "gurgaon"),
    ("kochi", "cochin"),
    ("thiruvananthapuram", "trivandrum"),
    ("mysuru", "mysore"),
    ("puducherry", "pondicherry"),
    ("vadodara", "baroda"),
    ("new delhi", "delhi"),
)


def _degree_level(text: str, *, lowest: bool) -> int | None:
    """The degree level ``text`` names: the lowest for a demand ("Bachelor's or
    Master's" asks for a bachelor's), the highest for a candidate's record."""
    levels = [level for level, pattern in _DEGREE_LEVEL_RES if pattern.search(text)]
    if levels:
        return min(levels) if lowest else max(levels)
    return 1 if _BARE_DEGREE_RE.search(text) else None


def _degree_gate(sentences: list[JdSentence], corpus: CandidateCorpus) -> Gate | None:
    demanded = [
        level
        for sentence in sentences
        if sentence.priority == "required" and not _EQUIVALENT_RE.search(sentence.text)
        for level in (_degree_level(sentence.text, lowest=True),)
        if level is not None
    ]
    if not demanded:
        return None
    # Two required sentences naming different levels are read as the lower,
    # like a "3-5 years" range: the posting accepts that level somewhere.
    needed = min(demanded)
    required = DEGREE_LEVELS[needed]
    held = _degree_level(corpus.education_text, lowest=False) if corpus.education_text else None
    if held is None:
        return Gate(
            name="degree",
            label="Degree",
            status="unverified",
            required=required,
            detail=(
                "no degree was found in your stored education, so this cannot be checked. "
                "Add your education under Profile knowledge."
            ),
        )
    found = DEGREE_LEVELS[held]
    if held >= needed:
        return Gate(name="degree", label="Degree", status="pass", required=required, found=found)
    return Gate(
        name="degree",
        label="Degree",
        status="fail",
        required=required,
        found=found,
        detail=(
            f"the posting requires a {required} and the highest degree on record is a "
            f"{found}. A screen that filters on this rejects the application before it "
            "reads a single skill."
        ),
    )


def _experience_gate(
    breakdown: list[CategoryBreakdown], years_by_term: dict[str, int], corpus: CandidateCorpus
) -> Gate | None:
    category = next((c for c in breakdown if c.name == "experience"), None)
    demands = [r for r in category.requirements if r.priority == "required"] if category else []
    if not demands:
        return None
    # The largest required figure binds: a posting asking for two years of SQL
    # and five of Python is not satisfied by three years in all.
    hardest = max(demands, key=lambda requirement: years_by_term[requirement.term])
    years = years_by_term[hardest.term]
    required = f"{years}+ years of experience"
    held = corpus.experience_months
    if held == 0:
        return Gate(
            name="experience",
            label="Years of experience",
            status="unverified",
            required=required,
            detail=(
                "no dated experience is stored, so this cannot be checked. Add a document "
                "with dated roles under Profile knowledge."
            ),
        )
    found = f"{held // 12} year(s) {held % 12} month(s)"
    if hardest.status == "missing":
        return Gate(
            name="experience",
            label="Years of experience",
            status="fail",
            required=required,
            found=found,
            detail=(
                f"more than a year short of the {years}-year demand. A screen that filters "
                "on years of experience rejects this before it reads a single skill."
            ),
        )
    return Gate(
        name="experience",
        label="Years of experience",
        status="pass",
        required=required,
        found=found,
        detail=(
            ""
            if hardest.status == "exact"
            else f"within a year of the {years}-year demand, which most screens accept; "
            "a strict one may not."
        ),
    )


def _location_tokens(location: str) -> list[str]:
    """The parts of a profile location worth matching, with renamed-city aliases.

    The last comma-separated part is dropped when there is more than one: it
    is almost always the country, and "India" in both places says nothing about
    whether Kochi is Bengaluru.
    """
    parts = [part.strip().lower() for part in location.split(",") if part.strip()]
    if len(parts) > 1:
        parts = parts[:-1]
    tokens = set(parts)
    for left, right in _CITY_ALIASES:
        if left in tokens:
            tokens.add(right)
        if right in tokens:
            tokens.add(left)
    return sorted(tokens)


def _location_gate(jd_text: str, candidate_location: str) -> Gate | None:
    """Pass, or flag for review -- never fail.

    Whether a different city is a dealbreaker depends on whether the candidate
    would relocate, which nothing in the store records. Failing it would cap
    the score on a guess; saying nothing would hide a filter many screens
    apply. Flagging it is the honest middle.
    """
    found_places = [match.group(1).strip() for match in _LOCATION_LINE_RE.finditer(jd_text)]
    found_places += [match.group(1).strip() for match in _BASED_IN_RE.finditer(jd_text)]
    places = list(dict.fromkeys(place for place in found_places if place))
    remote = bool(_REMOTE_ROLE_RE.search(jd_text)) or any("remote" in p.lower() for p in places)
    if not places and not remote:
        return None

    required = " / ".join(places) or "remote"
    if remote:
        return Gate(
            name="location",
            label="Location",
            status="pass",
            required=required,
            found=candidate_location,
            detail="the posting offers remote work, so location should not filter this out.",
        )
    if not candidate_location.strip():
        return Gate(
            name="location",
            label="Location",
            status="unverified",
            required=required,
            detail="your profile has no location, so this cannot be checked.",
        )
    haystack = normalize(" ".join(places))
    if any(
        keyword_pattern(token).search(haystack) for token in _location_tokens(candidate_location)
    ):
        return Gate(
            name="location",
            label="Location",
            status="pass",
            required=required,
            found=candidate_location,
        )
    return Gate(
        name="location",
        label="Location",
        status="unverified",
        required=required,
        found=candidate_location,
        detail=(
            "your profile places you somewhere else. Many screens filter on location, but "
            "only you know whether you would relocate, so this is flagged rather than failed."
        ),
    )


# --- the report -------------------------------------------------------------


def band_for(score: int) -> str:
    for threshold, label in BANDS:
        if score >= threshold:
            return label
    return BANDS[-1][1]  # pragma: no cover - the final threshold is 0


def build_report(
    jd_text: str,
    corpus: CandidateCorpus,
    *,
    knowledge_version: str = "",
    bank_version: str = "",
) -> AtsReport:
    """Score a job description against the candidate corpus.

    Categories with no requirements are omitted and their weight is
    redistributed over the rest. A posting that never mentions education is not
    evidence that the candidate lacks any, so scoring it as a zero would punish
    the candidate for the employer's choice of words.

    Hard filters run after the weighted score, and a failed one caps it at
    :data:`GATE_CAP`. The uncapped figure is kept on the report, so the
    breakdown still adds up to something the reader can check.
    """
    specs = extract_requirements(jd_text)
    sentences = split_by_priority(jd_text)
    years_by_term = {
        f"{years} years experience": years for years in extract_year_requirements(jd_text)
    }

    breakdown: list[CategoryBreakdown] = []
    weighted_total = 0.0
    weight_total = 0.0

    for name in CATEGORY_ORDER:
        category_specs = specs.get(name, [])
        if not category_specs:
            continue
        if name == "experience":
            requirements = [
                classify_years(spec, years_by_term[spec.term], corpus) for spec in category_specs
            ]
        else:
            requirements = [classify(spec, corpus) for spec in category_specs]

        mass = sum(requirement.priority_weight for requirement in requirements)
        score = sum(r.credit * r.priority_weight for r in requirements) / mass
        # Scaling the score alone would not be enough: a category made only of
        # preferred items averages to the same percentage however lightly each
        # item is weighted, so the category's own weight has to shrink with it.
        weight = WEIGHTS[name] * mass / len(requirements)
        weighted_total += weight * score
        weight_total += weight
        breakdown.append(
            CategoryBreakdown(
                name=name,
                label=CATEGORY_LABELS[name],
                weight=round(weight, 4),
                score=round(score, 4),
                requirements=requirements,
            )
        )

    uncapped = round(100 * weighted_total / weight_total) if weight_total else 0
    gates = [
        gate
        for gate in (
            _degree_gate(sentences, corpus),
            _experience_gate(breakdown, years_by_term, corpus),
            _location_gate(jd_text, corpus.location),
        )
        if gate is not None
    ]
    capped = uncapped > GATE_CAP and any(gate.status == "fail" for gate in gates)
    overall = GATE_CAP if capped else uncapped

    missing: list[str] = []
    weak: list[str] = []
    matched: list[str] = []
    for category in breakdown:
        for requirement in category.requirements:
            target = {"missing": missing, "related": weak, "exact": matched}[requirement.status]
            target.append(requirement.display)

    return AtsReport(
        score=overall,
        band=band_for(overall),
        breakdown=breakdown,
        missing_requirements=missing,
        weak_requirements=weak,
        matched_requirements=matched,
        requirement_count=sum(len(category.requirements) for category in breakdown),
        knowledge_version=knowledge_version,
        bank_version=bank_version,
        gates=gates,
        uncapped_score=uncapped,
        capped=capped,
    )
