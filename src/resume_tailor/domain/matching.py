"""JD-to-project keyword matching.

Deliberately literal rather than semantic: most ATS keyword scanning is literal
too, so this mirrors the system it is meant to help pass. That design decision
is unchanged from the original. What *has* changed is the matching primitive.

The bug this module was rewritten to fix
----------------------------------------
The original normalised text by deleting punctuation and then asked
``if keyword in jd_text``. Substring containment means a one-character keyword
matches almost any input. The bank genuinely contains the keyword ``r`` (the
language), so a job description mentioning "R&D, Rust and Ruby" scored the
ARIMA project above every actually-relevant one -- reproduced against the real
bank during the audit. ``ai``, ``cte``, ``psi`` and ``mlp`` had the same
problem in milder form.

Matching is now boundary-anchored, so ``r`` matches the standalone token *R*
and nothing else -- and specifically not ``R&D``, which is a different thing
that happens to start with the same letter.
"""

from __future__ import annotations

import functools
import re

from resume_tailor.domain.latex import latex_to_display_text
from resume_tailor.domain.models import (
    KeywordHit,
    MatchResult,
    Project,
    ProjectBank,
)

#: Weight added when the JD mentions one of the project's domain tags. Domain
#: overlap is a stronger signal than any single keyword, so it is worth more
#: than one keyword hit. Carried over from the original scoring.
DOMAIN_BONUS = 2

STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "you",
        "your",
        "our",
        "are",
        "will",
        "this",
        "that",
        "from",
        "have",
        "has",
        "into",
        "who",
        "what",
        "why",
        "how",
        "role",
        "job",
        "team",
        "work",
        "years",
        "year",
        "experience",
        "strong",
        "skills",
        "skill",
        "ability",
        "including",
        "such",
        "etc",
        "must",
        "should",
        "would",
        "can",
        "may",
        "not",
        "but",
        "all",
        "any",
        "we",
        "us",
        "they",
        "their",
        "them",
        "its",
        "it",
        "be",
        "is",
        "as",
        "at",
        "by",
        "in",
        "of",
        "on",
        "or",
        "to",
        "an",
        "a",
        "plus",
        "join",
        "about",
        "across",
        "within",
        "using",
        "use",
        "used",
        "well",
        "also",
        "requirements",
        "responsibilities",
        "qualifications",
        "preferred",
        "required",
        "candidate",
        "candidates",
        "opportunity",
        "company",
    }
)

#: Alternate spellings that should score as the canonical bank keyword. A small
#: hand-curated table, not a thesaurus: every entry is a form the author has
#: actually seen in a job description.
ALIASES: dict[str, tuple[str, ...]] = {
    "machine learning": ("ml", "machine-learning"),
    "deep learning": ("dl", "deep-learning"),
    "natural language processing": ("nlp",),
    "nlp": ("natural language processing",),
    "scikit-learn": ("sklearn", "scikit learn"),
    "postgresql": ("postgres",),
    "javascript": ("js",),
    "kubernetes": ("k8s",),
    "continuous integration": ("ci", "ci/cd"),
    "time series": ("time-series", "timeseries"),
    "a/b testing": ("ab testing", "a b testing", "split testing"),
    "large language model": ("llm", "large language models"),
    "llm": ("large language model", "large language models"),
    "generative ai": ("genai", "gen ai"),
    "power bi": ("powerbi",),
    "explainability": ("xai", "interpretability"),
    "feature engineering": ("feature-engineering",),
}

#: Characters that may legitimately appear *inside* a technical token and must
#: therefore not be treated as a word boundary: ``c++``, ``c#``, ``node.js``,
#: ``.net``, ``ci/cd``. ``&`` is included so ``R&D`` does not match ``R``.
_TOKEN_CHARS = r"A-Za-z0-9+#&"  # noqa: S105 - a regex character class, not a credential

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-/]*")


#: Keywords shorter than this do not get a plural suffix. Without the guard,
#: the one-character keyword ``r`` would also match the token ``rs``, which
#: re-opens a narrower version of the very bug this module fixes.
_MIN_LENGTH_FOR_PLURAL = 4


@functools.lru_cache(maxsize=2048)
def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Compile a boundary-anchored pattern for one keyword.

    Internal whitespace matches any run of space, hyphen, underscore or slash,
    so ``machine learning`` also matches ``machine-learning``. Keywords long
    enough for it to be safe additionally match their plural.
    """
    term = keyword.strip().lower()
    escaped = re.sub(r"(\\?\s)+", r"[\\s\\-_/]+", re.escape(term))
    plural = r"(?:es|s)?" if len(term) >= _MIN_LENGTH_FOR_PLURAL else ""
    return re.compile(
        rf"(?<![{_TOKEN_CHARS}]){escaped}{plural}(?![{_TOKEN_CHARS}])",
        re.IGNORECASE,
    )


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace, *preserving* intra-token punctuation.

    The original stripped every non-alphanumeric character, which destroyed
    ``c++``, ``node.js`` and ``ci/cd`` before matching ever ran.
    """
    return re.sub(r"\s+", " ", text.lower()).strip()


def count_matches(haystack: str, keyword: str) -> int:
    """Occurrences of ``keyword`` (or one of its aliases) in normalised text.

    Counts *distinct spans*, not pattern hits. The keyword pattern already
    treats internal whitespace as "space, hyphen, underscore or slash", so a
    keyword like ``machine learning`` and its alias ``machine-learning`` both
    match the same characters -- summing the two counts scored one mention
    twice and inflated the project's rank.
    """
    if not keyword:
        return 0
    term = keyword.strip().lower()
    spans: set[tuple[int, int]] = set()
    for pattern in (keyword_pattern(term), *(keyword_pattern(a) for a in ALIASES.get(term, ()))):
        spans.update(match.span() for match in pattern.finditer(haystack))
    return len(spans)


def score_project(jd_text: str, key: str, project: Project) -> MatchResult:
    """Score one project against a job description.

    Normalises internally, so callers may pass raw JD text -- the original
    required pre-normalised input and silently returned an all-zero result if
    you forgot, which is the worst possible failure mode for a ranking tool.
    """
    haystack = normalize(jd_text)

    hits = [
        KeywordHit(keyword=keyword, occurrences=count)
        for keyword in project.keywords
        if (count := count_matches(haystack, keyword)) > 0
    ]
    matched_domains = [d for d in project.domain if count_matches(haystack, d) > 0]

    score = len(hits) + (DOMAIN_BONUS if matched_domains else 0)
    coverage = len(hits) / len(project.keywords) if project.keywords else 0.0

    return MatchResult(
        key=key,
        title=latex_to_display_text(project.title),
        score=score,
        matched_keywords=[hit.keyword for hit in hits],
        keyword_hits=hits,
        matched_domains=matched_domains,
        domain_match=bool(matched_domains),
        coverage=round(coverage, 4),
    )


def match_bank(
    jd_text: str,
    bank: ProjectBank,
    include_hidden: bool = False,
) -> list[MatchResult]:
    """Rank every selectable project against the JD, best first.

    Ties break on coverage, then on key, so the ordering is deterministic --
    the original relied on ``dict`` insertion order for ties, which made the
    output depend on how the JSON file happened to be written.
    """
    projects = bank.projects if include_hidden else bank.visible()
    results = [score_project(jd_text, key, project) for key, project in projects.items()]
    results.sort(key=lambda r: (-r.score, -r.coverage, r.key))
    return results


# --- gap analysis -----------------------------------------------------------

#: Sentence boundary: a terminator followed by whitespace. Splitting on a bare
#: ``.`` (what the original did) tore ``Node.js`` and ``.NET`` in half.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def extract_meaningful_terms(jd_text: str, min_length: int = 3) -> list[str]:
    """Pull candidate skill/tool terms out of a job description.

    Heuristic, and honest about it: a term qualifies if it is an all-caps
    acronym, contains a digit, or is capitalised mid-sentence. The
    mid-sentence requirement is what filters out the single largest source of
    false positives -- ordinary sentence-initial capitals.
    """
    candidates: dict[str, None] = {}
    for sentence in _SENTENCE_SPLIT_RE.split(jd_text):
        words = sentence.strip().split()
        for index, word in enumerate(words):
            cleaned = word.strip(",;:()[]{}\"'`*").rstrip(".")
            if len(cleaned) < min_length or cleaned.lower() in STOPWORDS:
                continue
            if not any(char.isalpha() for char in cleaned):
                continue
            is_acronym = cleaned.isupper() and cleaned.isalpha()
            has_digit = any(char.isdigit() for char in cleaned)
            is_midsentence_cap = index > 0 and cleaned[0].isupper() and not cleaned.isupper()
            if is_acronym or has_digit or is_midsentence_cap:
                candidates[cleaned] = None
    return sorted(candidates)


#: Keyed by bank version, so an edited bank invalidates itself. Bounded, since
#: a long-running process should not accumulate one entry per historical edit.
_HAYSTACK_CACHE: dict[str, str] = {}
_HAYSTACK_CACHE_MAX = 8


def build_bank_haystack(bank: ProjectBank) -> str:
    """All bank text -- keywords, domains, titles and bullets -- as one string.

    Matching gap terms against keywords alone (the original behaviour) reported
    false gaps: a tool named in a bullet but not duplicated in the keyword list
    was reported as missing from the bank when it plainly was not.
    """
    cached = _HAYSTACK_CACHE.get(bank.version)
    if cached is not None:
        return cached

    parts: list[str] = []
    for project in bank.projects.values():
        parts.extend(project.keywords)
        parts.extend(project.domain)
        parts.append(latex_to_display_text(project.title))
        parts.extend(latex_to_display_text(bullet) for bullet in project.bullets)
    haystack = normalize(" \n ".join(parts))

    if len(_HAYSTACK_CACHE) >= _HAYSTACK_CACHE_MAX:
        _HAYSTACK_CACHE.pop(next(iter(_HAYSTACK_CACHE)))
    _HAYSTACK_CACHE[bank.version] = haystack
    return haystack


def find_gap_terms(jd_text: str, bank: ProjectBank, limit: int | None = None) -> list[str]:
    """JD terms with no counterpart anywhere in the bank -- the real gap list."""
    haystack = build_bank_haystack(bank)
    gaps = [
        term for term in extract_meaningful_terms(jd_text) if count_matches(haystack, term) == 0
    ]
    return gaps[:limit] if limit is not None else gaps


MATCH_NOTE = (
    "This is a keyword pre-scan, not a final recommendation. A zero-match "
    "project is not necessarily irrelevant -- it may simply use different "
    "vocabulary than this job description. Apply judgment before excluding a "
    "strong project on score alone."
)
