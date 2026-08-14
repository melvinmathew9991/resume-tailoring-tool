"""
matcher.py -- scores each project in the bank against a JD's text.

Deliberately literal substring matching (not semantic/embedding-based):
most ATS keyword scanning is literal too, so this mirrors that rather
than trying to be "smarter" than the systems it's meant to help pass.

Known, documented limitation (see README): a project with zero literal
keyword overlap is not necessarily irrelevant -- see the Credit Default
case study in the README for a real example where the matcher's top
pick differed from the better human/Claude judgment call. This tool
ranks; a human (or Claude) still decides.
"""
import re
from dataclasses import dataclass, field


# Common English stopwords to exclude from "unmatched JD terms" output,
# so that list isn't dominated by noise like "and", "the", "with".
_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "our", "are", "will",
    "this", "that", "from", "have", "has", "into", "who", "what",
    "role", "job", "team", "work", "years", "year", "experience",
    "strong", "skills", "ability", "including", "such", "etc",
}


def normalize(text: str) -> str:
    """Lowercase and strip punctuation for matching purposes."""
    return re.sub(r"[^a-z0-9\s]", " ", text.lower())


@dataclass
class MatchResult:
    key: str
    title: str
    score: int
    matched_keywords: list = field(default_factory=list)
    domain_match: bool = False


def score_project(jd_text: str, project: dict) -> MatchResult:
    """Note: normalizes jd_text internally, so callers can pass raw JD
    text directly -- this used to require pre-normalized input, which
    was a silent-failure footgun (passing raw text produced a valid-
    looking but wrong all-zero result with no error). Fixed after a
    test caught it."""
    jd_text_norm = normalize(jd_text)

    matched = []
    for kw in project.get("keywords", []):
        if normalize(kw) in jd_text_norm:
            matched.append(kw)

    domain_match = any(normalize(d) in jd_text_norm for d in project.get("domain", []))

    return MatchResult(
        key="",  # filled by caller
        title=project.get("title", ""),
        score=len(matched) + (2 if domain_match else 0),  # domain overlap weighted higher
        matched_keywords=matched,
        domain_match=domain_match,
    )


def match_jd_to_bank(jd_text: str, bank: dict, exclude_keys: set = None) -> list:
    """Returns a list of MatchResult, sorted by score descending.

    exclude_keys: project keys to always skip (e.g. unverified/incomplete
    projects that should never be surfaced regardless of keyword match).
    """
    exclude_keys = exclude_keys or set()

    results = []
    for key, project in bank.items():
        if key in exclude_keys:
            continue
        result = score_project(jd_text, project)  # score_project normalizes internally now
        result.key = key
        results.append(result)

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def extract_meaningful_jd_terms(jd_text: str, min_length: int = 3) -> list:
    """Extract candidate skill/tool terms from JD text: capitalized
    words/acronyms and known multi-word tech phrases. Cleaner than a
    naive capitalized-word regex -- explicitly filters out sentence-
    initial capitals (the most common source of false positives) by
    requiring the term either be all-caps (likely acronym), contain a
    digit, or appear capitalized mid-sentence (not right after a period
    or at the very start of the text).
    """
    candidates = set()
    sentences = re.split(r"[.\n]", jd_text)

    for sentence in sentences:
        words = sentence.strip().split()
        for i, word in enumerate(words):
            cleaned = word.strip(",;:()[]\"'")
            if len(cleaned) < min_length:
                continue
            is_acronym = cleaned.isupper() and cleaned.isalpha()
            has_digit = any(c.isdigit() for c in cleaned)
            is_midsentence_cap = (
                i > 0
                and cleaned[0].isupper()
                and not cleaned.isupper()
                and cleaned.lower() not in _STOPWORDS
            )
            if is_acronym or has_digit or is_midsentence_cap:
                candidates.add(cleaned)

    return sorted(candidates)


def find_gap_terms(jd_text: str, bank: dict) -> list:
    """JD candidate terms with no match anywhere in the bank's keywords
    -- the actual gap list. Filtered against stopwords and very short
    tokens to keep signal-to-noise reasonable."""
    candidates = extract_meaningful_jd_terms(jd_text)
    all_bank_keywords = set()
    for p in bank.values():
        all_bank_keywords.update(normalize(k) for k in p.get("keywords", []) + p.get("domain", []))

    gaps = [
        c for c in candidates
        if normalize(c) not in all_bank_keywords and c.lower() not in _STOPWORDS
    ]
    return gaps
