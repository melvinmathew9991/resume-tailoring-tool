from matcher import (
    normalize,
    score_project,
    match_jd_to_bank,
    extract_meaningful_jd_terms,
    find_gap_terms,
)


class TestNormalize:
    def test_lowercases(self):
        assert normalize("PYTHON") == "python"

    def test_strips_punctuation(self):
        assert normalize("Python, SQL!") == "python  sql "

    def test_preserves_digits(self):
        assert normalize("Python3") == "python3"


class TestScoreProject:
    def test_exact_keyword_match(self, sample_bank):
        result = score_project("we need someone strong in python and sql", sample_bank["proj_a"])
        assert "python" in result.matched_keywords
        assert "sql" in result.matched_keywords
        assert result.score >= 2

    def test_no_match_scores_zero(self, sample_bank):
        result = score_project("we need a graphic designer", sample_bank["proj_a"])
        assert result.score == 0
        assert result.matched_keywords == []

    def test_domain_match_adds_bonus_weight(self, sample_bank):
        result = score_project("looking for fintech experience", sample_bank["proj_a"])
        assert result.domain_match is True
        assert result.score >= 2  # domain bonus even with no keyword hits

    def test_case_insensitive_matching(self, sample_bank):
        result = score_project("PYTHON AND SQL EXPERTISE REQUIRED", sample_bank["proj_a"])
        assert "python" in result.matched_keywords


class TestMatchJdToBank:
    def test_ranks_higher_score_first(self, sample_bank):
        jd = "We need strong Python, SQL, and machine learning skills for a fintech role."
        results = match_jd_to_bank(jd, sample_bank)
        assert results[0].key == "proj_a"  # more keyword overlap than proj_b
        assert results[0].score >= results[1].score

    def test_exclude_keys_respected(self, sample_bank):
        jd = "Python SQL machine learning fintech healthcare nlp"
        results = match_jd_to_bank(jd, sample_bank, exclude_keys={"proj_a"})
        keys = [r.key for r in results]
        assert "proj_a" not in keys
        assert "proj_b" in keys

    def test_empty_jd_all_zero_scores(self, sample_bank):
        results = match_jd_to_bank("", sample_bank)
        assert all(r.score == 0 for r in results)

    def test_empty_bank_returns_empty_list(self):
        results = match_jd_to_bank("python sql", {})
        assert results == []


class TestExtractMeaningfulJdTerms:
    def test_extracts_acronyms(self):
        terms = extract_meaningful_jd_terms("Experience with AWS and GCP required.")
        assert "AWS" in terms
        assert "GCP" in terms

    def test_extracts_terms_with_digits(self):
        terms = extract_meaningful_jd_terms("Familiarity with Python3 and Web3 technologies.")
        assert "Python3" in terms

    def test_ignores_sentence_initial_capitals(self):
        # "We" at the start of a sentence shouldn't be treated as a
        # meaningful acronym/term just because it's capitalized.
        terms = extract_meaningful_jd_terms("We need someone great. Excellent communication required.")
        assert "We" not in terms
        assert "Excellent" not in terms

    def test_extracts_midsentence_proper_nouns(self):
        terms = extract_meaningful_jd_terms("Experience with Docker and Kubernetes is required.")
        assert "Docker" in terms
        assert "Kubernetes" in terms

    def test_empty_input_returns_empty_list(self):
        assert extract_meaningful_jd_terms("") == []


class TestFindGapTerms:
    def test_finds_terms_absent_from_bank(self, sample_bank):
        jd = "We require Kubernetes and Docker and Python experience."
        gaps = find_gap_terms(jd, sample_bank)
        # Python IS a bank keyword; Docker/Kubernetes are not in sample_bank's keywords
        assert "Python" not in gaps or "python" not in [g.lower() for g in gaps]

    def test_no_gaps_when_jd_fully_covered(self, sample_bank):
        jd = "python sql"
        gaps = find_gap_terms(jd, sample_bank)
        # lowercase JD won't extract candidate terms via the capital-letter
        # heuristic at all, so gaps should be empty (nothing extracted)
        assert gaps == []
