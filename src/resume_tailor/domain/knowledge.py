"""Candidate knowledge: the model, the extractor and the merge rules.

This is the candidate side of the system. It is deliberately *not* the resume
side: nothing here is ever printed onto a PDF. ``data/profile.yaml`` and
``data/project_bank.json`` hold pre-verified, LaTeX-formatted text that a human
wrote and fact-checked, and they stay hand-edited. This store holds what the
system *knows* about the person, extracted from documents they upload, and it
exists so the ATS checker has a candidate side to compare a job description
against.

Keeping those two apart is the point. If an uploaded document could write into
the project bank, an unverified sentence from a stale resume could end up on a
generated resume, which is the one failure this tool is built to prevent.

Extraction is rule-based, and only rule-based
---------------------------------------------
Every entry is either a term from :mod:`resume_tailor.domain.vocabulary` that
literally appears in the document, or a line the document literally contains.
Every entry carries the line it came from. Nothing is inferred, generalised or
levelled up -- a document that says "exposure to Spark" yields the term
``spark`` and the sentence that proves it, never "experienced in Spark".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from resume_tailor.domain.extraction import ExtractedDocument, split_sections
from resume_tailor.domain.matching import STOPWORDS, count_matches, keyword_pattern, normalize
from resume_tailor.domain.models import StrictModel, compute_version
from resume_tailor.domain.vocabulary import ALL_TERMS, category_of

Category = Literal["skill", "tool", "domain", "responsibility", "certification", "education"]

#: Report order. Also the order the UI renders expanders in, so the two cannot
#: disagree about which category comes first.
CATEGORIES: tuple[Category, ...] = (
    "skill",
    "tool",
    "domain",
    "responsibility",
    "certification",
    "education",
)

#: Vocabulary category name -> knowledge category name. The vocabulary is
#: plural (it describes groups of terms); an entry is singular (it is one fact).
_VOCAB_TO_CATEGORY: dict[str, Category] = {
    "skills": "skill",
    "tools": "tool",
    "domains": "domain",
    "responsibilities": "responsibility",
}

_MAX_EVIDENCE_CHARS = 400
_MAX_VALUE_CHARS = 120

# Hard structural ceilings, deliberately well above the configurable limits in
# ``core/config.py`` -- the same two-layer scheme ``api/schemas.py`` uses and
# for the same reason. These exist to stop an absurd store being constructed at
# all; the *configured* limit is lower, is checked in the service, and is the
# one that produces a message naming the fix. If a hard cap were equal to the
# configured one, the friendly error would be unreachable and the user would
# get a raw ``ValidationError`` from a layer that cannot explain itself.
MAX_ENTRIES_HARD = 20_000
MAX_EXPERIENCE_HARD = 2_000
MAX_SOURCES_HARD = 1_000

#: Signature of the local collector `extract_knowledge` hands to each rule:
#: (category, value, display, evidence).
AddEntry = Callable[[Category, str, str, str], None]


def _label_key(label: str) -> str:
    """How two document names are compared: case and spacing do not count."""
    return re.sub(r"\s+", " ", label).strip().lower()


class KnowledgeSource(StrictModel):
    """One document that was ingested. Entries point back at these."""

    source_id: str = Field(min_length=4, max_length=64)
    label: str = Field(min_length=1, max_length=300)
    kind: str = Field(min_length=1, max_length=16)
    characters: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    added_at: str = Field(min_length=1, max_length=64)


class KnowledgeEntry(StrictModel):
    """One extracted fact, with the text that proves it.

    ``value`` is the normalised, lower-cased key used for de-duplication and
    matching. ``display`` is how the document actually wrote it, which is what
    the UI shows -- so a candidate who wrote "PyTorch" sees "PyTorch" rather
    than the internal key.
    """

    category: Category
    value: str = Field(min_length=1, max_length=_MAX_VALUE_CHARS)
    display: str = Field(min_length=1, max_length=300)
    evidence: str = Field(default="", max_length=_MAX_EVIDENCE_CHARS)
    """The verbatim line the fact came from. Required by the no-fabrication
    rule: an entry that cannot point at its own source is a bug, not a fact."""
    source_id: str = Field(min_length=4, max_length=64)

    @field_validator("value")
    @classmethod
    def _normalise_value(cls, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value).strip().lower()
        if not cleaned:
            raise ValueError("entry value must not be empty")
        return cleaned

    @property
    def key(self) -> tuple[str, str]:
        """De-duplication key. Two entries with the same key are the same fact."""
        return (self.category, self.value)


class ExperienceFact(StrictModel):
    """A dated line from an experience or projects section.

    ``months`` is resolved and stored at extraction time, including for an
    open-ended "Present" range. That is what keeps the ATS report deterministic:
    if the duration were recomputed against the clock on every request, the same
    job description would score differently tomorrow.
    """

    title: str = Field(min_length=1, max_length=300)
    organisation: str = Field(default="", max_length=300)
    dates: str = Field(default="", max_length=120)
    months: int = Field(default=0, ge=0, le=1200)
    evidence: str = Field(default="", max_length=_MAX_EVIDENCE_CHARS)
    source_id: str = Field(min_length=4, max_length=64)
    start_month: int | None = Field(default=None, ge=0)
    """Months since year 0, so two ranges can be compared and merged without
    re-parsing the text."""
    end_month: int | None = Field(default=None, ge=0)

    @property
    def key(self) -> str:
        return normalize(f"{self.title}|{self.organisation}|{self.dates}")


class KnowledgeBase(StrictModel):
    """Everything the system knows about the candidate."""

    entries: list[KnowledgeEntry] = Field(default_factory=list, max_length=MAX_ENTRIES_HARD)
    experience: list[ExperienceFact] = Field(default_factory=list, max_length=MAX_EXPERIENCE_HARD)
    sources: list[KnowledgeSource] = Field(default_factory=list, max_length=MAX_SOURCES_HARD)

    # -- identity -----------------------------------------------------------

    @property
    def version(self) -> str:
        """Content hash, computed the same way the project bank computes its own.

        Lets a UI notice that the store changed underneath an open session,
        exactly as ``bank_version`` already does for the project bank.
        """
        return compute_version(self.model_dump(mode="json"))

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def is_empty(self) -> bool:
        return not self.entries and not self.experience

    # -- views --------------------------------------------------------------

    def by_category(self) -> dict[str, list[KnowledgeEntry]]:
        """Entries grouped in :data:`CATEGORIES` order, empty groups omitted."""
        grouped: dict[str, list[KnowledgeEntry]] = {}
        for category in CATEGORIES:
            matching_entries = [entry for entry in self.entries if entry.category == category]
            if matching_entries:
                grouped[category] = matching_entries
        return grouped

    def terms(self, *categories: str) -> list[str]:
        """Sorted, de-duplicated values, optionally restricted to categories."""
        wanted = set(categories) or set(CATEGORIES)
        return sorted({e.value for e in self.entries if e.category in wanted})

    def corpus_text(self) -> str:
        """All stored text as one normalised haystack, for keyword matching.

        Evidence lines are included, not just the extracted values. A skills
        line reading "Python, Airflow, dbt" contributes every word it holds,
        so a term the vocabulary does not know is still findable -- which is
        what stops the ATS checker reporting a false gap for an unusual tool.
        """
        parts: list[str] = []
        for entry in self.entries:
            parts.append(entry.value)
            parts.append(entry.display)
            if entry.evidence:
                parts.append(entry.evidence)
        for fact in self.experience:
            parts.extend((fact.title, fact.organisation, fact.evidence))
        return normalize(" \n ".join(part for part in parts if part))

    def month_intervals(self) -> list[tuple[int, int]]:
        """Every dated range this store holds, as (start, end) month indices."""
        return [
            (fact.start_month, fact.end_month)
            for fact in self.experience
            if fact.start_month is not None and fact.end_month is not None
        ]

    def total_experience_months(self) -> int:
        """Months of experience in this store, overlapping ranges counted once."""
        return merge_month_intervals(self.month_intervals())

    # -- mutation (returning new instances -- StrictModel is frozen) ---------

    def merged_with(self, addition: KnowledgeBase) -> tuple[KnowledgeBase, MergeStats]:
        """Add everything in ``addition`` that is not already known.

        The default and only non-destructive path. Existing entries are never
        rewritten, never re-ordered and never dropped: a re-upload of the same
        document adds nothing and reports so, rather than silently rebuilding
        the store.
        """
        stats = MergeStats()

        known_entries = {entry.key for entry in self.entries}
        merged_entries = list(self.entries)
        for entry in addition.entries:
            if entry.key in known_entries:
                stats.duplicate_entries += 1
                continue
            known_entries.add(entry.key)
            merged_entries.append(entry)
            stats.added_entries += 1
            stats.added_by_category[entry.category] = (
                stats.added_by_category.get(entry.category, 0) + 1
            )

        known_experience = {fact.key for fact in self.experience}
        merged_experience = list(self.experience)
        for fact in addition.experience:
            if fact.key in known_experience:
                stats.duplicate_experience += 1
                continue
            known_experience.add(fact.key)
            merged_experience.append(fact)
            stats.added_experience += 1

        known_sources = {source.sha256 for source in self.sources}
        merged_sources = list(self.sources)
        for source in addition.sources:
            if source.sha256 in known_sources:
                stats.source_already_known = True
                continue
            known_sources.add(source.sha256)
            merged_sources.append(source)

        return (
            KnowledgeBase(
                entries=merged_entries,
                experience=merged_experience,
                sources=merged_sources,
            ),
            stats,
        )

    def without(self, category: str, value: str) -> tuple[KnowledgeBase, int]:
        """Drop one entry. Returns the new base and how many were removed.

        Explicit, one at a time, and the count is reported so a caller that
        asked to remove something that was not there is told rather than left
        assuming it worked.
        """
        target = (category, re.sub(r"\s+", " ", value).strip().lower())
        kept = [entry for entry in self.entries if entry.key != target]
        return (
            KnowledgeBase(entries=kept, experience=self.experience, sources=self.sources),
            len(self.entries) - len(kept),
        )

    def sources_labelled(self, label: str) -> list[KnowledgeSource]:
        """Stored documents with this name, ignoring case and spacing."""
        target = _label_key(label)
        return [source for source in self.sources if _label_key(source.label) == target]

    def superseded_by(
        self, addition: KnowledgeBase, label: str
    ) -> tuple[KnowledgeBase, MergeStats]:
        """Replace every earlier version of the document called ``label``.

        The source document always wins: when it changes, the new version is
        the truth and the old one is not. Merge cannot express that -- it only
        adds, so a fact deleted from the document stayed in the store forever
        and kept scoring. This drops everything the earlier versions
        contributed, merges the new version in, and leaves every other
        document alone.

        Stats are measured against the store as it was, not against the
        emptied intermediate: a fact in both versions is "already known", not
        "added", so the report says what actually changed.

        One limitation, inherent in how entries are stored: an entry records
        only the first document it was found in. A fact that another document
        also contains, but that was credited to the old version, leaves with
        it. Re-adding that other document restores it.
        """
        old_ids = {source.source_id for source in self.sources_labelled(label)}
        kept = KnowledgeBase(
            entries=[entry for entry in self.entries if entry.source_id not in old_ids],
            experience=[fact for fact in self.experience if fact.source_id not in old_ids],
            sources=[source for source in self.sources if source.source_id not in old_ids],
        )
        merged, _ = kept.merged_with(addition)

        before = {entry.key for entry in self.entries}
        after = {entry.key for entry in merged.entries}
        before_experience = {fact.key for fact in self.experience}
        after_experience = {fact.key for fact in merged.experience}
        known_hashes = {source.sha256 for source in self.sources}

        stats = MergeStats(
            added_entries=len(after - before),
            duplicate_entries=sum(1 for entry in addition.entries if entry.key in before),
            added_experience=len(after_experience - before_experience),
            duplicate_experience=sum(
                1 for fact in addition.experience if fact.key in before_experience
            ),
            removed_entries=len(before - after),
            removed_experience=len(before_experience - after_experience),
            superseded_sources=len(old_ids),
            source_already_known=any(s.sha256 in known_hashes for s in addition.sources),
        )
        for entry in merged.entries:
            if entry.key not in before:
                stats.added_by_category[entry.category] = (
                    stats.added_by_category.get(entry.category, 0) + 1
                )
        return merged, stats


#: Above this share of unrecognised free text, a skills list is probably prose.
_PROSE_SHARE = 0.6
#: Below this many skill entries the share above is not yet meaningful.
_PROSE_MIN_ENTRIES = 40
#: A skill is a short noun phrase; longer values are usually torn from a line.
_LONG_VALUE_CHARS = 60


def lint_knowledge(knowledge: KnowledgeBase) -> list[str]:
    """Content warnings for a stored knowledge base.

    The counterpart of :func:`resume_tailor.data.bank_repo.lint_bank`, and it
    exists for the same reason: a store can be structurally valid and still be
    obviously wrong, and the person who uploaded it is the only one who can
    decide what to do about that.

    This is the answer to "how do I know the upload worked?". Counts alone do
    not tell you -- 2,000 entries looks like a thorough document and is in fact
    the signature of a document whose prose was read as a skills list. These
    checks name that shape explicitly, because it is the failure that actually
    happened and the one a user cannot diagnose from the entry list alone.

    Never fatal. A warning describes content, not an error.
    """
    warnings: list[str] = []
    if knowledge.is_empty:
        return ["nothing is stored yet, so an ATS check has no candidate side from this store"]

    declared = [entry for entry in knowledge.entries if entry.category in ("skill", "tool")]
    unrecognised = [entry for entry in declared if category_of(entry.value) is None]
    if len(declared) >= _PROSE_MIN_ENTRIES:
        share = len(unrecognised) / len(declared)
        if share > _PROSE_SHARE:
            warnings.append(
                f"{len(unrecognised)} of {len(declared)} skill/tool entries "
                f"({share:.0%}) are not recognised terms. That usually means a document's "
                "prose was read as a skills list -- re-upload it with mode 'replace'"
            )

    long_values = [entry for entry in knowledge.entries if len(entry.value) > _LONG_VALUE_CHARS]
    if long_values:
        warnings.append(
            f"{len(long_values)} entr{'y' if len(long_values) == 1 else 'ies'} are longer than "
            f"{_LONG_VALUE_CHARS} characters, which is a sentence rather than a term "
            f"(for example: {long_values[0].value[:60]!r})"
        )

    malformed = [
        entry
        for entry in knowledge.entries
        if _URL_RE.search(entry.value) or entry.value[:1].isdigit()
    ]
    if malformed:
        warnings.append(
            f"{len(malformed)} entr{'y' if len(malformed) == 1 else 'ies'} look like links or "
            f"numbers rather than skills (for example: {malformed[0].value[:40]!r})"
        )

    if not knowledge.experience:
        warnings.append(
            "no dated roles were found, so a 'N years of experience' requirement cannot be "
            "checked against this store"
        )

    for source in knowledge.sources:
        owned = sum(1 for entry in knowledge.entries if entry.source_id == source.source_id)
        if owned > 500:
            warnings.append(
                f"{source.label!r} contributed {owned} entries on its own, which is far more "
                "than a resume or profile document should yield"
            )
    return warnings


@dataclass
class MergeStats:
    """What a merge actually did, so the UI can say more than "saved"."""

    added_entries: int = 0
    duplicate_entries: int = 0
    added_experience: int = 0
    duplicate_experience: int = 0
    source_already_known: bool = False
    added_by_category: dict[str, int] = field(default_factory=dict)
    removed_entries: int = 0
    """Only ever non-zero when a document is superseded."""
    removed_experience: int = 0
    superseded_sources: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.added_entries
            or self.added_experience
            or self.removed_entries
            or self.removed_experience
        )


# --- extraction -------------------------------------------------------------

#: Separators inside a declared skills line. ``/`` is deliberately absent:
#: splitting on it would tear ``ci/cd`` and ``a/b testing`` in half, which is
#: the same mistake the original matcher made with intra-token punctuation.
_LIST_SPLIT_RE = re.compile(r"[,;|]|\s+[•·‣●]\s*|\t")
_LEADING_BULLET_RE = re.compile(r"^\s*[-•·‣●*]+\s*")
_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z /&+.-]{0,40}:\s*")

#: A declared skill is a short noun phrase. Longer than this and the line is a
#: sentence that happens to contain commas, not a skills list.
_MAX_DECLARED_WORDS = 5
_MIN_DECLARED_CHARS = 2
_MAX_DECLARED_CHARS = 64

#: Lines after a "Skills" heading that are read as a list. A real skills block
#: is a handful of lines; this bounds the damage when a heading in a long
#: document has no heading after it and so captures the rest of the file.
_MAX_SKILL_SECTION_LINES = 25

#: A line longer than this is prose, whatever heading it sits under.
_MAX_LIST_LINE_WORDS = 20

#: Ceiling on declared skills taken from one document.
_MAX_DECLARED_SKILLS = 150

_URL_RE = re.compile(r"https?://|www\.|//", re.IGNORECASE)
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
#: A bracket with no partner: the fragment was cut out of a longer phrase.
_UNBALANCED_RE = re.compile(r"^[^(]*\)|\([^)]*$")
#: Sentence punctuation: a full stop, question mark or colon followed by space.
_SENTENCE_PUNCTUATION_RE = re.compile(r"[.?!:;]\s")

_CERTIFICATION_RE = re.compile(
    r"\b(certified|certification|certificate|credential|licen[cs]ed)\b", re.IGNORECASE
)
#: A qualification mentioned *outside* an Education section.
#:
#: Precision matters more than recall here, because this is the fallback path:
#: every line under an "Education" heading is already captured verbatim by the
#: section scan above it. This regex only has to catch a degree named somewhere
#: else, so a miss costs one duplicate-ish entry while a false positive files an
#: arbitrary sentence under "Education".
#:
#: Hence the split. The abbreviations name a qualification and nothing else, so
#: they stand alone. "bachelor", "master", "diploma" and "degree" are ordinary
#: English words that appear constantly in technical prose -- "not on master"
#: (a git branch), "the scrum master", "a degree of confidence", "degree of
#: freedom" -- so they only count when a qualifier puts them in the education
#: sense. Observed: a document of engineering notes contributed two Education
#: entries, each a whole sentence, purely because it mentioned a branch named
#: master.
_DEGREE_RE = re.compile(
    r"\b(?:"
    # Unambiguous on their own.
    r"ph\.?d|doctorate|mba|b\.?tech|m\.?tech|b\.?sc|m\.?sc|bca|mca|b\.?com|m\.?com"
    # Ambiguous alone, so they require education context.
    # U+2019 is the curly apostrophe, written as an escape so this pattern
    # stays pure ASCII. It is needed because Word and Google Docs both emit
    # the curly form rather than the typewriter one.
    r"|(?:bachelor|master)(?:'|\u2019)?s\b"
    r"|(?:bachelor|master)\s+(?:of|in)\b"
    r"|(?:bachelor|master|honou?rs|associate)s?\s+degree\b"
    r"|(?:under)?graduate\s+degree\b"
    r"|degree\s+in\b"
    r"|diploma\s+(?:in|from)\b"
    r")",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_YEAR_RE = re.compile(
    r"\b(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?"
    r"((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"\b(0?[1-9]|1[0-2])[/-]((?:19|20)\d{2})\b")
_PRESENT_RE = re.compile(r"\b(present|current|currently|now|ongoing|to date)\b", re.IGNORECASE)

#: Sections whose lines are candidate experience entries. Project sections are
#: included because a dated personal project is experience the candidate can
#: point at, and the JD side does not distinguish.
_EXPERIENCE_SECTIONS = ("experience", "projects")

_SPLIT_ORGANISATION_RE = re.compile(r"\s+(?:at|@)\s+|\s+[|–—]\s+|\s+--\s+", re.IGNORECASE)  # noqa: RUF001 - the en/em dash is data, not prose: postings write date ranges with it

#: Fifty years. Longer than any single role, and the ceiling that separates a
#: real date range from two unrelated years that happen to share a line.
_MAX_PLAUSIBLE_ROLE_MONTHS = 600


def extract_knowledge(
    document: ExtractedDocument,
    *,
    max_entries: int = 2000,
) -> KnowledgeBase:
    """Turn one extracted document into candidate knowledge.

    Deterministic: the same document always produces the same entries in the
    same order, which is what makes "nothing new" a meaningful answer on a
    re-upload.
    """
    source = KnowledgeSource(
        source_id=document.sha256[:12],
        label=document.label,
        kind=document.kind,
        characters=document.characters,
        sha256=document.sha256,
        added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    sections = split_sections(document.text)
    lines = [line for line in (raw.strip() for raw in document.text.split("\n")) if line]

    entries: list[KnowledgeEntry] = []
    seen: set[tuple[str, str]] = set()

    def add(category: Category, value: str, display: str, evidence: str) -> None:
        key = (category, re.sub(r"\s+", " ", value).strip().lower())
        if not key[1] or key in seen or len(entries) >= max_entries:
            return
        # Dropped rather than truncated. `value` is the entry's *identity* --
        # it is the de-duplication key, the thing the ATS scorer matches on and
        # the parameter DELETE /knowledge/entries takes. Cutting a sentence off
        # at 120 characters lands mid-word and yields a key that matches
        # nothing, de-duplicates against nothing and reads as garbage in the
        # UI. Anything this long is a paragraph the extractor misread, not a
        # fact worth keeping half of.
        if len(key[1]) > _MAX_VALUE_CHARS:
            return
        seen.add(key)
        entries.append(
            KnowledgeEntry(
                category=category,
                value=key[1],
                display=(display.strip() or key[1])[:300],
                evidence=evidence.strip()[:_MAX_EVIDENCE_CHARS],
                source_id=source.source_id,
            )
        )

    _add_vocabulary_terms(document.text, lines, add)
    _add_declared_skills(sections, add)
    _add_certifications(sections, lines, add)
    _add_education(sections, lines, add)

    experience = _extract_experience(sections, source.source_id)

    return KnowledgeBase(entries=entries, experience=experience, sources=[source])


def _add_vocabulary_terms(text: str, lines: list[str], add: AddEntry) -> None:
    """Record every vocabulary term the document literally contains.

    Scanned longest-term-first so the evidence attaches to the most specific
    phrase present: a document saying "Azure ML" records ``azure ml`` with that
    line, and ``azure`` with the same line, rather than only the vaguer one.
    """
    haystack = normalize(text)
    for term in ALL_TERMS:
        if count_matches(haystack, term) == 0:
            continue
        vocab_category = category_of(term)
        if vocab_category is None:  # pragma: no cover - ALL_TERMS is built from the categories
            continue
        evidence, display = _find_evidence(lines, term)
        add(_VOCAB_TO_CATEGORY[vocab_category], term, display or term, evidence)


def _find_evidence(lines: list[str], term: str) -> tuple[str, str]:
    """First line containing ``term``, and the term exactly as that line spells it."""
    pattern = keyword_pattern(term)
    for line in lines:
        found = pattern.search(line)
        if found:
            return line, found.group(0)
    return "", ""


def looks_like_a_declared_term(item: str) -> bool:
    """Is this fragment a skill someone listed, or a piece of a sentence?

    The distinction matters because a skills *list* is comma-separated and so is
    a paragraph. Splitting prose on commas yields fragments syntactically
    identical to short skill names, so the filter has to be on shape: a term is
    a short noun phrase, not a clause, a URL or a number.

    Written as a predicate with one test per rule rather than a single dense
    condition, because every rule here is a judgement call a future reader will
    want to reconsider on its own.
    """
    if not item:
        return False
    if not (_MIN_DECLARED_CHARS <= len(item) <= _MAX_DECLARED_CHARS):
        return False
    if not any(character.isalpha() for character in item):
        return False  # "~2", "129", "000)"
    if _URL_RE.search(item):
        return False  # "https://github.com/..." -- a link, not a skill
    if item[0].isdigit():
        return False  # "5 resume bullet points", "19-test pytest suite"
    if _UNBALANCED_RE.search(item):
        return False  # "000 breast cancer patients)" -- torn out of a phrase
    words = item.split()
    if len(words) > _MAX_DECLARED_WORDS:
        return False
    if all(word.strip(".,").lower() in STOPWORDS for word in words):
        return False  # "and", "with the"
    if any(word.strip(".,").isdigit() for word in words):
        return False  # "covering 129 admissions" -- a measurement, not a skill
    # A clause, not a noun phrase: "applying Kaplan-Meier estimation".
    # Safe to be strict here, because a *vocabulary* term is captured
    # independently by `_add_vocabulary_terms` and does not rely on this rule --
    # so rejecting a bare "monitoring" costs nothing, while rejecting a
    # participle clause removes a whole class of prose fragment.
    if len(words[0]) > 5 and words[0].lower().endswith("ing") and len(words) > 1:
        return False
    # A skills list carries no sentence punctuation. ".NET" and "node.js" keep
    # their dots, because the check is for a dot *followed by whitespace*.
    return not _SENTENCE_PUNCTUATION_RE.search(item)


def _add_declared_skills(sections: dict[str, list[str]], add: AddEntry) -> None:
    """Take a skills section at its word -- where it really is a list.

    A line under "Technical Skills" is the candidate declaring a skill, so it is
    stored whether or not the vocabulary knows the term. That is the mechanism
    by which a niche tool the curated list has never heard of still reaches the
    store -- without it the vocabulary would silently cap what the system can
    learn about a person.

    Three bounds stop that swallowing a document whole. `split_sections` ends a
    section only at the *next* recognised heading, so a long document with one
    "Skills" heading and none after it puts everything to end-of-file in this
    section; comma-splitting then turns paragraphs into thousands of fragments.
    That is not hypothetical -- a 581 kB project-notes file produced 2,057
    "skills", of which 2,001 were sentence fragments, URLs and stray numbers,
    and it poisoned the ATS candidate corpus because every entry carries its
    evidence line.

    So only the opening lines of the section are read as a list, only lines
    short enough to *be* a list are considered, and the rule is capped per
    document. A real skills block is a handful of lines; past that it is prose
    that happens to sit under the heading.
    """
    taken = 0
    for section in ("skills", "tools"):
        for line in sections.get(section, [])[:_MAX_SKILL_SECTION_LINES]:
            body = _LABEL_RE.sub("", _LEADING_BULLET_RE.sub("", line))
            if len(body.split()) > _MAX_LIST_LINE_WORDS:
                continue  # a paragraph, not a list
            for raw_item in _LIST_SPLIT_RE.split(body):
                # A parenthetical gloss is dropped rather than rejected, so
                # "lifelines (survival analysis)" still records "lifelines".
                item = _PARENTHETICAL_RE.sub(" ", raw_item)
                item = re.sub(r"\s+", " ", item).strip(" .:-–—")  # noqa: RUF001 - the dashes are data: list separators
                if not looks_like_a_declared_term(item):
                    continue
                if taken >= _MAX_DECLARED_SKILLS:
                    return
                taken += 1
                category: Category = "tool" if category_of(item.lower()) == "tools" else "skill"
                add(category, item.lower(), item, line)


def _add_certifications(sections: dict[str, list[str]], lines: list[str], add: AddEntry) -> None:
    """Certification lines, from the section and from anywhere else that says so."""
    for line in sections.get("certifications", []):
        cleaned = _LEADING_BULLET_RE.sub("", line).strip()
        if cleaned:
            add("certification", cleaned.lower(), cleaned, line)
    for line in lines:
        if _CERTIFICATION_RE.search(line) and len(line) <= 200:
            cleaned = _LEADING_BULLET_RE.sub("", line).strip()
            if cleaned:
                add("certification", cleaned.lower(), cleaned, line)


def _add_education(sections: dict[str, list[str]], lines: list[str], add: AddEntry) -> None:
    """Education lines, from the section and from any degree mention elsewhere."""
    for line in sections.get("education", []):
        cleaned = _LEADING_BULLET_RE.sub("", line).strip()
        if cleaned:
            add("education", cleaned.lower(), cleaned, line)
    for line in lines:
        if _DEGREE_RE.search(line) and len(line) <= 200:
            cleaned = _LEADING_BULLET_RE.sub("", line).strip()
            if cleaned:
                add("education", cleaned.lower(), cleaned, line)


# --- experience dates -------------------------------------------------------


def merge_month_intervals(intervals: list[tuple[int, int]]) -> int:
    """Total months covered by a set of ranges, counting overlaps once.

    Two jobs held in the same year are one year of experience, not two. A naive
    sum is how a scorer ends up claiming twelve years for a six-year career,
    and it is exactly the kind of inflation the PRD forbids.

    A free function rather than a method because the candidate's dated history
    lives in two places -- the knowledge store and ``profile.yaml`` -- and they
    have to be merged *together* to be counted correctly. Merging each
    separately and adding the totals would reintroduce the double count this
    exists to prevent.
    """
    total = 0
    current_start: int | None = None
    current_end = 0
    for start, end in sorted(intervals):
        if current_start is None:
            current_start, current_end = start, end
            continue
        if start <= current_end + 1:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start + 1
            current_start, current_end = start, end
    if current_start is not None:
        total += current_end - current_start + 1
    return total


def _month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _date_tokens(line: str) -> list[tuple[int, int]]:
    """(position, month index) for every date in the line, in reading order.

    The two patterns overlap on purpose -- ``01/2020`` contains the year
    ``2020``, which the month-name pattern also matches. Numeric dates are
    collected first and any later match whose span overlaps one is discarded,
    so ``01/2020 - 06/2020`` reads as two dates six months apart rather than
    four dates one month apart.
    """
    spans: list[tuple[int, int, int]] = []  # (start, end, month index)
    for found in _NUMERIC_DATE_RE.finditer(line):
        spans.append(
            (found.start(), found.end(), _month_index(int(found.group(2)), int(found.group(1))))
        )
    for found in _MONTH_YEAR_RE.finditer(line):
        if any(start < found.end() and found.start() < end for start, end, _ in spans):
            continue
        month_name = (found.group(1) or "").lower()
        month = _MONTHS.get(month_name[:4], _MONTHS.get(month_name[:3], 1))
        spans.append((found.start(), found.end(), _month_index(int(found.group(2)), month)))

    spans.sort()
    return [(start, index) for start, _, index in spans]


def parse_experience_line(line: str, source_id: str) -> ExperienceFact | None:
    """Parse one dated line into an experience fact, or ``None`` if it has no dates.

    An open-ended range ("Aug 2024 -- Present") is closed against the clock
    *here*, once, and the resulting month count is stored. Every later read is
    then a stored number rather than a fresh calculation, so a report generated
    today and the same report generated next month agree.
    """
    tokens = _date_tokens(line)
    present = _PRESENT_RE.search(line)
    if not tokens:
        return None

    start = tokens[0][1]
    if len(tokens) >= 2:
        end = tokens[1][1]
    elif present is not None:
        now = datetime.now(timezone.utc)
        end = _month_index(now.year, now.month)
    else:
        # A single date with no "present" is a graduation year or a publication
        # date, not a range. Recording it as a one-month job would be an
        # invention, so it is skipped.
        return None

    if end < start:
        start, end = end, start

    # A line can hold a date that is not part of the range -- "Analyst, Acme
    # (founded 1919), Jan 2024 - Present" spans a century. Treating that as one
    # role is obviously wrong, and it used to be worse than wrong: the span
    # exceeded the bound on `ExperienceFact.months`, so the ValidationError
    # aborted the *entire* upload and the user had no way to tell which line
    # caused it. A span no human career can contain means this line is not a
    # date range, so it is skipped like any other undated line.
    if end - start + 1 > _MAX_PLAUSIBLE_ROLE_MONTHS:
        return None

    dates = line[tokens[0][0] :][:120].strip()
    title_text = (line[: tokens[0][0]] or line).strip(" ,;:|-–—")  # noqa: RUF001 - the en/em dash is data, not prose: postings write date ranges with it
    title_text = _LEADING_BULLET_RE.sub("", title_text).strip()
    if not title_text:
        title_text = line.strip()[:300]

    parts = _SPLIT_ORGANISATION_RE.split(title_text, maxsplit=1)
    title = parts[0].strip()[:300] or title_text[:300]
    organisation = parts[1].strip()[:300] if len(parts) > 1 else ""

    return ExperienceFact(
        title=title,
        organisation=organisation,
        dates=dates,
        months=end - start + 1,
        evidence=line[:_MAX_EVIDENCE_CHARS],
        source_id=source_id,
        start_month=start,
        end_month=end,
    )


def _extract_experience(sections: dict[str, list[str]], source_id: str) -> list[ExperienceFact]:
    facts: list[ExperienceFact] = []
    seen: set[str] = set()
    for section in _EXPERIENCE_SECTIONS:
        for line in sections.get(section, []):
            try:
                fact = parse_experience_line(line, source_id)
            except ValidationError:
                # One unparseable line must not cost the user the whole upload.
                # The plausibility guard above catches the known case; this is
                # the backstop for the next one, and skipping the line loses a
                # single fact rather than the entire document.
                continue
            if fact is None or fact.key in seen:
                continue
            seen.add(fact.key)
            facts.append(fact)
    return facts
