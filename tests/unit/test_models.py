"""Domain model validation -- the boundary that makes bad states unrepresentable."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from resume_tailor.domain.models import (
    PersonalInfo,
    Profile,
    Project,
    ProjectBank,
    ResumeSpec,
    compute_version,
)

pytestmark = pytest.mark.unit


def make_project(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "A Project",
        "github": "owner/repo",
        "domain": ["fintech"],
        "keywords": ["python"],
        "bullets": ["Did a thing."],
    }
    base.update(overrides)
    return base


class TestProject:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("owner/repo", "owner/repo"),
            ("https://github.com/owner/repo", "owner/repo"),
            ("http://github.com/owner/repo", "owner/repo"),
            ("github.com/owner/repo", "owner/repo"),
            ("owner/repo/", "owner/repo"),
            ("owner", "owner"),
            ("owner/repo_with_underscores", "owner/repo_with_underscores"),
            ("owner/repo.name-1", "owner/repo.name-1"),
        ],
    )
    def test_github_is_normalised(self, raw: str, expected: str) -> None:
        assert Project.model_validate(make_project(github=raw)).github == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "owner/repo}{\\input{x}",  # brace escape out of \href{}
            "owner/repo%comment",  # comments out the rest of the LaTeX line
            "owner/repo#fragment",
            "owner repo",
            "owner//repo",
            "/repo",
            "",
        ],
    )
    def test_unsafe_github_values_are_rejected(self, raw: str) -> None:
        """Shape-constraining the value removes the need to escape inside
        \\href{}, where escaping would break the URL anyway (defect B12)."""
        with pytest.raises(ValidationError):
            Project.model_validate(make_project(github=raw))

    def test_bullets_are_required(self) -> None:
        with pytest.raises(ValidationError):
            Project.model_validate(make_project(bullets=[]))

    def test_blank_bullet_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="bullet 1 is empty"):
            Project.model_validate(make_project(bullets=["ok", "   "]))

    def test_overlong_bullet_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exceeds 1000"):
            Project.model_validate(make_project(bullets=["x" * 1001]))

    def test_keywords_are_lowercased_and_deduplicated(self) -> None:
        project = Project.model_validate(make_project(keywords=["Python", "python", " SQL "]))
        assert project.keywords == ["python", "sql"]

    def test_empty_keyword_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Project.model_validate(make_project(keywords=[" "]))

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Project.model_validate(make_project(unexpected="x"))

    def test_hidden_defaults_to_false(self) -> None:
        assert Project.model_validate(make_project()).hidden is False

    def test_github_url_property(self) -> None:
        project = Project.model_validate(make_project(github="owner/repo"))
        assert project.github_url == "https://github.com/owner/repo"


class TestProjectBank:
    def test_version_is_stable_across_key_order(self) -> None:
        first = compute_version({"a": make_project(), "b": make_project()})
        second = compute_version({"b": make_project(), "a": make_project()})
        assert first == second

    def test_version_changes_with_content(self) -> None:
        before = compute_version({"a": make_project()})
        after = compute_version({"a": make_project(title="Different")})
        assert before != after

    def test_invalid_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProjectBank.from_raw({"Bad Key!": make_project()})

    def test_visible_excludes_hidden(self) -> None:
        bank = ProjectBank.from_raw({"a": make_project(), "b": make_project(hidden=True)})
        assert set(bank.visible()) == {"a"}
        assert len(bank) == 2


class TestPersonalInfo:
    def test_unknown_field_is_rejected(self) -> None:
        """This is what makes the reserved-key crash unrepresentable: a caller
        cannot smuggle a template variable name in through personal_info."""
        with pytest.raises(ValidationError):
            PersonalInfo.model_validate({"name": "X", "font_size": 4})

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "ftp://example.com",
            "https://example.com/a b",
            "https://example.com/{x}",
            "https://example.com/100%",
        ],
    )
    def test_unsafe_urls_are_rejected(self, url: str) -> None:
        with pytest.raises(ValidationError):
            PersonalInfo.model_validate({"name": "X", "github_url": url})

    def test_merged_with_revalidates(self) -> None:
        """model_copy(update=...) skips validators entirely, so a malformed URL
        would otherwise slide straight into the \\href{} argument."""
        base = PersonalInfo.model_validate({"name": "X"})
        with pytest.raises(ValueError, match="http"):
            base.merged_with({"github_url": "javascript:alert(1)"})

    def test_merged_with_ignores_empty_values(self) -> None:
        base = PersonalInfo.model_validate({"name": "Original", "title": "DS"})
        merged = base.merged_with({"name": "", "title": None})
        assert merged.name == "Original"
        assert merged.title == "DS"

    def test_merged_with_none_returns_self(self) -> None:
        base = PersonalInfo.model_validate({"name": "X"})
        assert base.merged_with(None) is base

    def test_merged_with_applies_overrides(self) -> None:
        base = PersonalInfo.model_validate({"name": "X", "email": "a@b.c"})
        assert base.merged_with({"name": "Y"}).name == "Y"


class TestResumeSpec:
    def test_keys_and_projects_must_align(self) -> None:
        profile = Profile.model_validate({"personal": {"name": "X"}, "summary": "s"})
        with pytest.raises(ValidationError, match="same length"):
            ResumeSpec(
                profile=profile,
                projects=[Project.model_validate(make_project())],
                project_keys=["a", "b"],
                max_pages=2,
                bank_version="x",
            )

    @pytest.mark.parametrize("pages", [0, -1, 11])
    def test_max_pages_is_bounded(self, pages: int) -> None:
        profile = Profile.model_validate({"personal": {"name": "X"}, "summary": "s"})
        with pytest.raises(ValidationError):
            ResumeSpec(
                profile=profile,
                projects=[],
                project_keys=[],
                max_pages=pages,
                bank_version="x",
            )


class TestEmailIsValidatedLikeAUrl:
    """The email reaches an href target too, and used to be the one that did not.

    The renderer builds a mailto link from it, so the raw value lands inside an
    href argument exactly as the URLs do. It carried only a length cap, which
    made it the single unguarded route into a link target.
    """

    @pytest.mark.parametrize(
        "email",
        [
            "me@example.com",
            "first_last@example.com",
            "me+tag@example.co.uk",
            "a.b.c@sub.domain.example",
            "",
        ],
    )
    def test_ordinary_addresses_are_accepted(self, email: str) -> None:
        assert PersonalInfo.model_validate({"name": "X", "email": email}).email == email

    @pytest.mark.parametrize(
        "email",
        [
            "a}{b@example.com",
            "me@example.com} more",
            "a#b@example.com",
            "a%b@example.com",
            "has space@example.com",
            'quote"@example.com',
        ],
    )
    def test_unsafe_addresses_are_rejected(self, email: str) -> None:
        with pytest.raises(ValidationError):
            PersonalInfo.model_validate({"name": "X", "email": email})

    def test_merged_with_revalidates_the_email_too(self) -> None:
        """model_copy(update=...) skips validators, and an override arrives over
        HTTP -- so the merge path needs the check as much as construction does."""
        base = PersonalInfo.model_validate({"name": "X", "email": "safe@example.com"})
        with pytest.raises(ValueError, match="unsafe inside"):
            base.merged_with({"email": "evil}{payload@example.com"})
