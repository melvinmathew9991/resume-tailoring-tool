"""Bank and profile loading: malformed content, caching, and hot reload."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from resume_tailor.core.errors import BankError, ProfileError
from resume_tailor.data.bank_repo import BankRepository, lint_bank, parse_bank
from resume_tailor.data.profile_repo import ProfileRepository, parse_profile
from resume_tailor.domain.models import ProjectBank

pytestmark = pytest.mark.unit

VALID_PROJECT = {
    "title": "A Project",
    "github": "owner/repo",
    "domain": ["fintech"],
    "keywords": ["python"],
    "bullets": ["Did a thing."],
}


class TestParseBank:
    def test_valid_bank_parses(self) -> None:
        bank = parse_bank(json.dumps({"a": VALID_PROJECT}))
        assert len(bank) == 1

    def test_malformed_json_raises_bank_error(self) -> None:
        with pytest.raises(BankError, match="not valid project-bank JSON"):
            parse_bank("{not json")

    def test_duplicate_keys_are_rejected(self) -> None:
        """json.load silently keeps the last duplicate, so a copy-paste mistake
        in a hand-edited file quietly deletes a project."""
        entry = json.dumps(VALID_PROJECT)
        raw = f'{{"a": {entry}, "a": {entry}}}'
        with pytest.raises(BankError, match="duplicate project key"):
            parse_bank(raw)

    def test_empty_object_is_rejected(self) -> None:
        with pytest.raises(BankError, match="no projects"):
            parse_bank("{}")

    def test_top_level_array_is_rejected(self) -> None:
        with pytest.raises(BankError, match="JSON object"):
            parse_bank("[]")

    def test_missing_required_field_names_the_field(self) -> None:
        with pytest.raises(BankError, match="bullets"):
            parse_bank(json.dumps({"a": {"title": "T", "github": "o/r"}}))

    def test_empty_bullets_list_is_rejected(self) -> None:
        broken = {**VALID_PROJECT, "bullets": []}
        with pytest.raises(BankError, match="bullets"):
            parse_bank(json.dumps({"a": broken}))

    def test_error_message_identifies_the_source(self) -> None:
        with pytest.raises(BankError, match=r"my_bank\.json"):
            parse_bank("{", source="my_bank.json")


class TestBankRepository:
    def _write(self, path: Path, raw: dict[str, Any]) -> None:
        path.write_text(json.dumps(raw), encoding="utf-8")

    def test_loads_and_caches(self, tmp_path: Path) -> None:
        path = tmp_path / "bank.json"
        self._write(path, {"a": VALID_PROJECT})
        repo = BankRepository(path)
        assert repo.load() is repo.load()

    def test_reloads_when_the_file_changes(self, tmp_path: Path) -> None:
        """The original cached the bank in a module global that was never
        invalidated, so the README's "just edit the JSON" workflow silently
        served stale content until a restart (defect B11)."""
        path = tmp_path / "bank.json"
        self._write(path, {"a": VALID_PROJECT})
        repo = BankRepository(path)
        assert set(repo.load().projects) == {"a"}

        time.sleep(0.01)
        self._write(path, {"a": VALID_PROJECT, "b": VALID_PROJECT})
        assert set(repo.load().projects) == {"a", "b"}

    def test_version_changes_when_content_changes(self, tmp_path: Path) -> None:
        path = tmp_path / "bank.json"
        self._write(path, {"a": VALID_PROJECT})
        repo = BankRepository(path)
        before = repo.load().version

        time.sleep(0.01)
        self._write(path, {"a": {**VALID_PROJECT, "title": "Renamed"}})
        assert repo.load().version != before

    def test_missing_file_raises_bank_error(self, tmp_path: Path) -> None:
        with pytest.raises(BankError, match="cannot read project bank"):
            BankRepository(tmp_path / "nope.json").load()

    def test_utf8_bom_is_tolerated(self, tmp_path: Path) -> None:
        """Windows editors add a BOM, and json.loads chokes on it."""
        path = tmp_path / "bank.json"
        path.write_text(json.dumps({"a": VALID_PROJECT}), encoding="utf-8-sig")
        assert len(BankRepository(path).load()) == 1

    def test_invalidate_forces_a_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "bank.json"
        self._write(path, {"a": VALID_PROJECT})
        repo = BankRepository(path)
        first = repo.load()
        repo.invalidate()
        assert repo.load() is not first


class TestLintBank:
    def test_flags_a_profile_only_github_link(self) -> None:
        bank = ProjectBank.from_raw({"a": {**VALID_PROJECT, "github": "owner"}})
        assert any("profile, not a repository" in warning for warning in lint_bank(bank))

    def test_flags_missing_keywords_and_domains(self) -> None:
        bank = ProjectBank.from_raw({"a": {**VALID_PROJECT, "keywords": [], "domain": []}})
        warnings = " ".join(lint_bank(bank))
        assert "no keywords" in warnings
        assert "no domain tags" in warnings

    def test_clean_bank_produces_no_warnings(self) -> None:
        bank = ProjectBank.from_raw({"a": {**VALID_PROJECT, "bullets": ["One.", "Two."]}})
        assert lint_bank(bank) == []

    def test_real_bank_lint_is_advisory_only(self, real_bank: ProjectBank) -> None:
        assert isinstance(lint_bank(real_bank), list)


class TestProfileRepository:
    def test_parses_valid_profile(self, sample_profile_yaml: str) -> None:
        profile = parse_profile(sample_profile_yaml)
        assert profile.personal.name == "Test Person"

    def test_malformed_yaml_raises(self) -> None:
        with pytest.raises(ProfileError, match="not valid YAML"):
            parse_profile("key: [unclosed")

    def test_non_mapping_raises(self) -> None:
        with pytest.raises(ProfileError, match="mapping"):
            parse_profile("- a\n- b")

    def test_unknown_field_raises(self, sample_profile_yaml: str) -> None:
        with pytest.raises(ProfileError, match="failed validation"):
            parse_profile(sample_profile_yaml + "\nunexpected_key: 1\n")

    def test_missing_summary_raises(self) -> None:
        with pytest.raises(ProfileError, match="summary"):
            parse_profile("personal:\n  name: X\n")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProfileError, match="cannot read profile"):
            ProfileRepository(tmp_path / "nope.yaml").load()

    def test_reloads_when_the_file_changes(self, tmp_path: Path, sample_profile_yaml: str) -> None:
        path = tmp_path / "profile.yaml"
        path.write_text(sample_profile_yaml, encoding="utf-8")
        repo = ProfileRepository(path)
        assert repo.load().personal.name == "Test Person"

        time.sleep(0.01)
        path.write_text(
            sample_profile_yaml.replace("Test Person", "Renamed Person"), encoding="utf-8"
        )
        assert repo.load().personal.name == "Renamed Person"

    def test_production_profile_loads(self) -> None:
        """The real profile.yaml must stay valid; it is content, and content
        gets hand-edited."""
        from tests.conftest import REAL_DATA_DIR

        profile = ProfileRepository(REAL_DATA_DIR / "profile.yaml").load()
        assert profile.personal.name
        assert profile.summary
