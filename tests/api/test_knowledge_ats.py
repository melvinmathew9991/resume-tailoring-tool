"""HTTP surface for the two standalone features.

Both run against the isolated ``data_dir`` fixture, so a test that writes a
knowledge store cannot touch the real one. The knowledge store starts absent
in that directory, which is also the state a fresh checkout is in -- so the
empty-store path is exercised on every run rather than only when someone
remembers to test it.
"""

from __future__ import annotations

import base64
import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

RESUME = """Jane Doe

Technical Skills
Languages: Python, SQL
Cloud: AWS, Docker, Obscurelib

Work Experience
Data Scientist at Northwind -- Jan 2022 - Dec 2023
Built forecasting models for fintech clients.

Education
B.Tech Computer Science, 2019
"""

JD = """Senior Data Scientist
4+ years of experience with Python, SQL, Snowflake and Kubernetes required.
You will build models and own stakeholder communication for a banking client.
Bachelor degree required.
"""


def make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in paragraphs)
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def add_text(client: TestClient, text: str = RESUME, **extra: object) -> dict:
    response = client.post("/api/v1/knowledge", json={"text": text, **extra})
    assert response.status_code == 200, response.text
    return response.json()


# --- reading ----------------------------------------------------------------


class TestReadKnowledge:
    def test_a_fresh_installation_has_an_empty_store(self, client: TestClient) -> None:
        body = client.get("/api/v1/knowledge").json()
        assert body["is_empty"] is True
        assert body["entry_count"] == 0
        assert body["entries"] == []

    def test_no_file_is_created_just_by_reading(self, client: TestClient, data_dir: Path) -> None:
        client.get("/api/v1/knowledge")
        assert not (data_dir / "knowledge.json").exists()


# --- updating ---------------------------------------------------------------


class TestUpdateFromText:
    def test_pasted_text_is_extracted_and_stored(self, client: TestClient) -> None:
        body = add_text(client)
        assert body["added_entries"] > 0
        values = {entry["value"] for entry in body["knowledge"]["entries"]}
        assert {"python", "sql", "aws", "docker"} <= values

    def test_the_store_is_written_to_the_data_directory(
        self, client: TestClient, data_dir: Path
    ) -> None:
        add_text(client)
        stored = json.loads((data_dir / "knowledge.json").read_text(encoding="utf-8"))
        assert stored["entries"]

    def test_the_resume_content_files_are_never_touched(
        self, client: TestClient, data_dir: Path
    ) -> None:
        """The separation the PRD asks for, asserted on the filesystem. An
        uploaded document must not be able to reach the text a resume prints."""
        bank_before = (data_dir / "project_bank.json").read_bytes()
        profile_before = (data_dir / "profile.yaml").read_bytes()
        add_text(client)
        assert (data_dir / "project_bank.json").read_bytes() == bank_before
        assert (data_dir / "profile.yaml").read_bytes() == profile_before

    def test_every_entry_carries_its_evidence(self, client: TestClient) -> None:
        for entry in add_text(client)["knowledge"]["entries"]:
            assert entry["evidence"]
            assert entry["source_id"]

    def test_re_adding_the_same_text_adds_nothing_and_says_so(self, client: TestClient) -> None:
        add_text(client)
        second = add_text(client)
        assert second["added_entries"] == 0
        assert second["source_already_known"] is True
        assert "nothing new" in second["message"].lower()

    def test_merge_keeps_what_was_already_stored(self, client: TestClient) -> None:
        add_text(client)
        body = add_text(client, "Technical Skills\nTerraform, Kubernetes\n")
        values = {entry["value"] for entry in body["knowledge"]["entries"]}
        assert "python" in values  # from the first document
        assert "terraform" in values  # from the second

    def test_replace_starts_again_and_has_to_be_asked_for(self, client: TestClient) -> None:
        add_text(client)
        body = add_text(client, "Technical Skills\nTerraform\n", mode="replace")
        values = {entry["value"] for entry in body["knowledge"]["entries"]}
        assert "terraform" in values
        assert "python" not in values

    def test_supersede_swaps_one_document_and_keeps_the_rest(self, client: TestClient) -> None:
        add_text(client, "Technical Skills\nKafka\n", label="cv")
        add_text(client, "Technical Skills\nPython, Terraform\n", label="points")
        body = add_text(client, "Technical Skills\nPython\n", label="points", mode="supersede")
        values = {entry["value"] for entry in body["knowledge"]["entries"]}
        assert "terraform" not in values
        assert {"python", "kafka"} <= values
        assert body["removed_entries"] == 1
        assert body["superseded_sources"] == 1
        assert body["message"].startswith("Updated points")

    def test_supersede_with_no_earlier_version_is_refused(self, client: TestClient) -> None:
        add_text(client)
        response = client.post(
            "/api/v1/knowledge",
            json={"text": "Technical Skills\nKafka\n", "label": "never-seen", "mode": "supersede"},
        )
        assert response.status_code == 400
        assert "no stored document" in response.json()["detail"]

    def test_dated_experience_is_totalled(self, client: TestClient) -> None:
        body = add_text(client)
        assert body["knowledge"]["experience_months"] == 24

    def test_a_term_outside_the_vocabulary_is_still_stored(self, client: TestClient) -> None:
        values = {entry["value"] for entry in add_text(client)["knowledge"]["entries"]}
        assert "obscurelib" in values


class TestUpdateFromDocument:
    def test_a_docx_upload_is_read(self, client: TestClient) -> None:
        payload = base64.b64encode(make_docx(["Skills", "Python, Airflow"])).decode()
        response = client.post(
            "/api/v1/knowledge", json={"document_b64": payload, "filename": "cv.docx"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_kind"] == "docx"
        assert "airflow" in {entry["value"] for entry in body["knowledge"]["entries"]}


# --- validation -------------------------------------------------------------


class TestValidation:
    def test_neither_text_nor_document_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/v1/knowledge", json={})
        assert response.status_code == 400
        assert "exactly one" in response.json()["detail"]

    def test_both_text_and_document_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/knowledge",
            json={"text": "hi", "document_b64": "aGk=", "filename": "a.txt"},
        )
        assert response.status_code == 400

    def test_a_document_without_a_filename_is_refused(self, client: TestClient) -> None:
        """The extension selects the reader, so there is nothing to guess with."""
        response = client.post("/api/v1/knowledge", json={"document_b64": "aGk="})
        assert response.status_code == 400
        assert "filename" in response.json()["detail"]

    def test_blank_text_is_refused(self, client: TestClient) -> None:
        assert client.post("/api/v1/knowledge", json={"text": "   "}).status_code == 400

    def test_invalid_base64_is_an_extraction_failure(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/knowledge", json={"document_b64": "not base64!!", "filename": "cv.pdf"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "extraction_failed"

    def test_a_legacy_doc_is_refused_with_advice(self, client: TestClient) -> None:
        payload = base64.b64encode(b"anything").decode()
        response = client.post(
            "/api/v1/knowledge", json={"document_b64": payload, "filename": "cv.doc"}
        )
        assert response.status_code == 422
        assert ".docx" in response.json()["detail"]

    def test_an_unsupported_extension_names_the_supported_ones(self, client: TestClient) -> None:
        payload = base64.b64encode(b"anything").decode()
        response = client.post(
            "/api/v1/knowledge", json={"document_b64": payload, "filename": "cv.pages"}
        )
        assert response.status_code == 422
        assert ".pdf" in response.json()["detail"]

    def test_an_empty_document_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/v1/knowledge", json={"document_b64": "", "filename": "cv.txt"})
        assert response.status_code in (400, 422)

    def test_an_unknown_mode_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/v1/knowledge", json={"text": "hi", "mode": "wipe"})
        assert response.status_code == 400

    def test_unknown_fields_are_refused(self, client: TestClient) -> None:
        response = client.post("/api/v1/knowledge", json={"text": "hi", "extra": 1})
        assert response.status_code == 400


# --- removal ----------------------------------------------------------------


class TestRemoval:
    def test_one_entry_can_be_removed(self, client: TestClient) -> None:
        add_text(client)
        response = client.request(
            "DELETE", "/api/v1/knowledge/entries", params={"category": "skill", "value": "python"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["removed"] == 1
        assert "python" not in {e["value"] for e in body["knowledge"]["entries"]}

    def test_removing_something_absent_reports_zero_rather_than_pretending(
        self, client: TestClient
    ) -> None:
        add_text(client)
        response = client.request(
            "DELETE", "/api/v1/knowledge/entries", params={"category": "skill", "value": "cobol"}
        )
        assert response.json()["removed"] == 0

    def test_the_whole_store_can_be_emptied(self, client: TestClient) -> None:
        add_text(client)
        body = client.request("DELETE", "/api/v1/knowledge").json()
        assert body["is_empty"] is True


# --- ats check --------------------------------------------------------------


class TestAtsCheck:
    def test_it_works_before_anything_has_been_uploaded(self, client: TestClient) -> None:
        """The profile and project bank are candidate knowledge too, so the
        checker is useful on a fresh installation rather than refusing until
        the user has fed it something."""
        response = client.post("/api/v1/ats/check", json={"jd_text": JD})
        assert response.status_code == 200
        assert response.json()["requirement_count"] > 0

    def test_the_score_and_breakdown_are_returned(self, client: TestClient) -> None:
        add_text(client)
        body = client.post("/api/v1/ats/check", json={"jd_text": JD}).json()
        assert 0 <= body["score"] <= 100
        assert body["band"]
        names = [category["name"] for category in body["breakdown"]]
        assert "skills" in names
        assert names == sorted(names, key=list(body["weights"]).index)

    def test_the_breakdown_distinguishes_three_statuses(self, client: TestClient) -> None:
        add_text(client)
        body = client.post("/api/v1/ats/check", json={"jd_text": JD}).json()
        statuses = {
            requirement["status"]
            for category in body["breakdown"]
            for requirement in category["requirements"]
        }
        assert statuses <= {"exact", "related", "missing"}
        assert "missing" in statuses  # Snowflake is not in the fixture data

    def test_matches_report_where_the_evidence_came_from(self, client: TestClient) -> None:
        add_text(client)
        body = client.post("/api/v1/ats/check", json={"jd_text": JD}).json()
        matched = [
            requirement
            for category in body["breakdown"]
            for requirement in category["requirements"]
            if requirement["status"] == "exact"
        ]
        assert matched
        assert all(set(r["matched_sources"]) <= {"knowledge", "profile", "bank"} for r in matched)

    def test_repetition_does_not_change_the_score(self, client: TestClient) -> None:
        add_text(client)
        once = client.post("/api/v1/ats/check", json={"jd_text": JD}).json()
        many = client.post("/api/v1/ats/check", json={"jd_text": JD + "\nPython. " * 30}).json()
        assert many["score"] == once["score"]

    def test_the_same_request_twice_gives_an_identical_report(self, client: TestClient) -> None:
        add_text(client)
        first = client.post("/api/v1/ats/check", json={"jd_text": JD}).json()
        second = client.post("/api/v1/ats/check", json={"jd_text": JD}).json()
        assert first == second

    def test_the_methodology_is_published_with_the_result(self, client: TestClient) -> None:
        body = client.post("/api/v1/ats/check", json={"jd_text": JD}).json()
        assert abs(sum(body["weights"].values()) - 1.0) < 1e-9
        assert body["note"]

    def test_no_document_is_produced(self, client: TestClient, data_dir: Path) -> None:
        """Feature 2 generates nothing: no resume, and no change to any file."""
        before = {path.name: path.read_bytes() for path in data_dir.iterdir()}
        body = client.post("/api/v1/ats/check", json={"jd_text": JD}).json()
        assert "document_id" not in body
        assert "download_url" not in body
        assert {path.name: path.read_bytes() for path in data_dir.iterdir()} == before

    def test_an_empty_job_description_is_refused(self, client: TestClient) -> None:
        assert client.post("/api/v1/ats/check", json={"jd_text": "   "}).status_code == 400

    def test_a_missing_job_description_is_refused(self, client: TestClient) -> None:
        assert client.post("/api/v1/ats/check", json={}).status_code == 400

    def test_an_oversized_job_description_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/v1/ats/check", json={"jd_text": "x" * 300_000})
        assert response.status_code in (400, 413)

    def test_adding_knowledge_can_close_a_gap(self, client: TestClient) -> None:
        """The two features connect exactly here, and nowhere else."""
        before = client.post("/api/v1/ats/check", json={"jd_text": "We need Snowflake."}).json()
        assert "snowflake" in before["missing_requirements"]
        add_text(client, "Technical Skills\nSnowflake\n")
        after = client.post("/api/v1/ats/check", json={"jd_text": "We need Snowflake."}).json()
        assert "snowflake" not in after["missing_requirements"]
        assert after["score"] > before["score"]


class TestHardFilters:
    def test_a_failed_degree_gate_caps_the_score(self, client: TestClient) -> None:
        """The test profile holds a BSc; a PhD demand is a hard filter it fails."""
        body = client.post(
            "/api/v1/ats/check", json={"jd_text": "Required: Python and SQL. A PhD is required."}
        ).json()
        degree = next(gate for gate in body["gates"] if gate["name"] == "degree")
        assert (degree["status"], degree["found"]) == ("fail", "bachelor's degree")
        assert body["capped"] is True
        assert body["score"] == body["gate_cap"] < body["uncapped_score"]

    def test_the_resume_score_carries_the_same_gates(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/resume/ats",
            json={"jd_text": "A PhD is required.", "selected_project_keys": ["proj_a"]},
        )
        assert [gate["name"] for gate in response.json()["gates"]] == ["degree"]


class TestUpdateMessages:
    """The message is the only thing telling the user which of three things
    happened, so each has to say something different."""

    def test_a_new_document_with_nothing_extractable_says_so(self, client: TestClient) -> None:
        body = add_text(client, "the quick brown fox jumped over the lazy dog")
        assert body["added_entries"] == 0
        assert body["source_already_known"] is False
        assert "no skills" in body["message"]
        assert "already known" not in body["message"]

    def test_a_repeat_upload_says_it_is_a_repeat(self, client: TestClient) -> None:
        add_text(client)
        assert "added before" in add_text(client)["message"]

    def test_a_new_document_of_known_facts_says_they_were_known(self, client: TestClient) -> None:
        add_text(client, "Technical Skills\nPython\n")
        body = add_text(client, "Technical Skills\nPython, Python\n")
        assert body["added_entries"] == 0
        assert body["source_already_known"] is False
        assert "already known" in body["message"]


class TestExperienceCredit:
    def test_profile_dates_count_before_anything_is_uploaded(self, client: TestClient) -> None:
        """The fresh-checkout case: an "N years" requirement must not be
        reported missing when the profile holds dated roles."""
        body = client.post(
            "/api/v1/ats/check", json={"jd_text": "We need 1+ years of experience."}
        ).json()
        experience = next(c for c in body["breakdown"] if c["name"] == "experience")
        requirement = experience["requirements"][0]
        assert requirement["status"] == "exact"
        assert requirement["matched_sources"] == ["profile"]


class TestResumeScopedAts:
    """`/resume/ats` scores the document; `/ats/check` scores the candidate.

    They are two different numbers on purpose, and the tests keep them apart.
    """

    def _check(self, client: TestClient, keys: list[str], jd: str = JD) -> dict:
        response = client.post(
            "/api/v1/resume/ats", json={"jd_text": jd, "selected_project_keys": keys}
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_it_scores_the_selection_it_was_given(self, client: TestClient) -> None:
        body = self._check(client, ["proj_a"])
        assert body["project_keys"] == ["proj_a"]
        assert 0 <= body["score"] <= 100

    def test_changing_the_selection_changes_the_score(self, client: TestClient) -> None:
        """The point of scoring the document rather than the candidate: it is
        feedback on the choice the user is making.

        FastAPI is chosen deliberately -- it appears in a *bullet* of `proj_a`,
        so selecting that project genuinely puts the word on the page.
        """
        jd = "We need FastAPI."
        without = self._check(client, [], jd)
        with_it = self._check(client, ["proj_a"], jd)
        assert without["score"] == 0
        assert with_it["score"] > without["score"]

    def test_a_keyword_only_project_does_not_move_the_score(self, client: TestClient) -> None:
        """`proj_b` is tagged nlp/healthcare in the bank but says neither word
        in a bullet. Selecting it cannot help a resume pass a screen that reads
        the page, and the score correctly refuses to pretend otherwise."""
        jd = "We need NLP and healthcare experience."
        without = self._check(client, ["proj_a"], jd)
        with_it = self._check(client, ["proj_a", "proj_b"], jd)
        assert with_it["score"] == without["score"]

    def test_bank_keywords_are_not_counted(self, client: TestClient) -> None:
        """`proj_a` carries the keyword "machine learning" in the bank but never
        says it in a bullet. An ATS reads the page, not the metadata, so this
        must score as missing -- otherwise the tool flatters a resume for words
        no screen can see."""
        body = self._check(client, ["proj_a"], "We need machine learning.")
        assert "machine learning" in body["missing_requirements"]

    def test_it_reports_what_the_candidate_has_but_the_page_lacks(self, client: TestClient) -> None:
        """The actionable half: a gap closable by picking a different project,
        as opposed to one the candidate genuinely does not cover."""
        body = self._check(client, ["proj_a"], "We need NLP.")
        # Displayed as the posting spelled it, so compare case-insensitively.
        assert "nlp" in {term.lower() for term in body["covered_elsewhere"]}

    def test_the_resume_score_sees_the_dates_the_page_prints(self, client: TestClient) -> None:
        """The page prints the profile's dated roles. Leaving them out reported
        every "N years" demand as unverifiable for a resume that states it."""
        body = self._check(client, ["proj_a"], "We need 1+ years of experience.")
        experience = next(c for c in body["breakdown"] if c["name"] == "experience")
        assert experience["requirements"][0]["status"] == "exact"
        assert experience["requirements"][0]["matched_sources"] == ["resume"]

    def test_covered_elsewhere_is_a_subset_of_missing(self, client: TestClient) -> None:
        body = self._check(client, ["proj_a"])
        assert set(body["covered_elsewhere"]) <= set(body["missing_requirements"])

    def test_it_generates_nothing(self, client: TestClient) -> None:
        body = self._check(client, ["proj_a"])
        assert "document_id" not in body
        assert "download_url" not in body

    def test_it_disagrees_with_the_standalone_check_and_that_is_correct(
        self, client: TestClient
    ) -> None:
        """The candidate corpus is strictly wider than one resume, so the
        standalone score must be at least the document score for the same JD."""
        jd = "We need NLP and healthcare experience."
        document = self._check(client, ["proj_a"], jd)
        candidate = client.post("/api/v1/ats/check", json={"jd_text": jd}).json()
        assert candidate["score"] >= document["score"]

    def test_an_empty_job_description_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/resume/ats", json={"jd_text": "  ", "selected_project_keys": ["proj_a"]}
        )
        assert response.status_code == 400

    def test_a_hidden_project_is_refused_here_too(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/resume/ats",
            json={"jd_text": JD, "selected_project_keys": ["proj_hidden"]},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "hidden_project"

    def test_the_selection_rules_are_the_generate_rules(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/resume/ats", json={"jd_text": JD, "selected_project_keys": ["nope"]}
        )
        assert response.status_code == 404
        assert response.json()["code"] == "unknown_project"


class TestStoreHealthIsVisible:
    def test_a_healthy_store_reports_no_prose_warning(self, client: TestClient) -> None:
        add_text(client)
        warnings = client.get("/api/v1/knowledge").json()["warnings"]
        assert not [w for w in warnings if "not recognised terms" in w]

    def test_warnings_are_returned_with_the_store(self, client: TestClient) -> None:
        """So "did my upload work?" is answerable from one GET."""
        add_text(client, "Technical Skills\nPython, SQL\n")
        body = client.get("/api/v1/knowledge").json()
        assert any("no dated roles" in w for w in body["warnings"])

    def test_the_version_changes_after_a_replace(self, client: TestClient) -> None:
        """The check that an upload actually landed: a new store hashes
        differently, and the hash is reported."""
        before = add_text(client)["knowledge"]["version"]
        after = add_text(client, "Technical Skills\nTerraform\n", mode="replace")
        assert after["knowledge"]["version"] != before
        assert after["knowledge"]["entry_count"] < 60

    def test_replace_leaves_exactly_one_source(self, client: TestClient) -> None:
        add_text(client)
        body = add_text(client, "Technical Skills\nTerraform\n", mode="replace")
        assert len(body["knowledge"]["sources"]) == 1
        assert body["knowledge"]["sources"][0]["label"] == "pasted text"
