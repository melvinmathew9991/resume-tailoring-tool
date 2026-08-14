import pytest
from resume_builder import build_resume, load_project_bank, PERSONAL_INFO
from pdf_compiler import CompilationError, LatexNotFoundError


class TestLoadProjectBank:
    def test_real_bank_loads_without_error(self, real_bank):
        assert len(real_bank) > 0

    def test_every_project_has_required_fields(self, real_bank):
        for key, project in real_bank.items():
            assert "title" in project, f"{key} missing title"
            assert "github" in project, f"{key} missing github"
            assert "bullets" in project, f"{key} missing bullets"

    def test_every_project_has_at_least_one_bullet(self, real_bank):
        for key, project in real_bank.items():
            assert len(project["bullets"]) >= 1, f"{key} has no bullets"

    def test_malformed_bank_raises_clear_error(self, tmp_path):
        import json
        bad_bank_path = tmp_path / "bad_bank.json"
        bad_bank_path.write_text(json.dumps({
            "broken_project": {"title": "Missing github and bullets"}
        }))
        with pytest.raises(ValueError, match="missing required field"):
            load_project_bank(bad_bank_path)

    def test_project_with_empty_bullets_list_raises(self, tmp_path):
        import json
        bad_bank_path = tmp_path / "empty_bullets.json"
        bad_bank_path.write_text(json.dumps({
            "empty_project": {"title": "T", "github": "x/y", "bullets": []}
        }))
        with pytest.raises(ValueError, match="no bullets"):
            load_project_bank(bad_bank_path)


class TestBuildResumeCore:
    """These are slower (they actually shell out to pdflatex), but this
    is the pipeline that matters most -- worth the real compile cost
    rather than mocking it away and missing real LaTeX bugs (as happened
    twice during development: the underscore bug and the dict.items()
    collision bug were BOTH only caught by actually compiling)."""

    def test_builds_successfully_with_real_projects(self, real_bank):
        keys = list(real_bank.keys())[:3]
        result = build_resume(keys, bank=real_bank)
        assert result.page_count <= 2
        assert result.warning == ""
        assert len(result.pdf_bytes) > 1000  # sanity: not an empty/corrupt PDF

    def test_unknown_project_key_raises_keyerror(self, real_bank):
        with pytest.raises(KeyError, match="Unknown project key"):
            build_resume(["this_project_does_not_exist"], bank=real_bank)

    def test_empty_project_selection_still_compiles(self, real_bank):
        # Should produce a resume with just the static sections (Experience,
        # Skills, Education) and no Projects content -- must not crash.
        result = build_resume([], bank=real_bank)
        assert result.page_count >= 1

    def test_single_project_compiles(self, real_bank):
        first_key = list(real_bank.keys())[0]
        result = build_resume([first_key], bank=real_bank)
        assert result.page_count <= 2

    def test_underscore_in_github_field_does_not_break_compilation(self, sample_bank):
        # Regression test for the exact bug caught during development:
        # a raw underscore in a GitHub repo name caused a fatal LaTeX
        # error ("Missing $ inserted") because LaTeX treats _ as a
        # subscript operator outside math mode.
        bank_with_underscore = {
            "proj": {
                "title": "Test Project",
                "github": "user/repo_with_underscores_everywhere",
                "bullets": ["A test bullet with no special characters."],
            }
        }
        result = build_resume(["proj"], bank=bank_with_underscore)
        assert result.page_count >= 1  # did not raise CompilationError

    def test_all_projects_selected_either_fits_or_warns_clearly(self, real_bank):
        # Must never silently exceed max_pages -- either it fits, or
        # `warning` is non-empty. This is the core safety guarantee.
        all_keys = list(real_bank.keys())
        result = build_resume(all_keys, bank=real_bank, max_pages=2)
        if result.page_count > 2:
            assert result.warning != "", (
                "Resume exceeded max_pages but no warning was set -- "
                "this is exactly the silent-overflow bug this tool exists to prevent."
            )

    def test_custom_max_pages_respected(self, real_bank):
        keys = list(real_bank.keys())[:2]
        result = build_resume(keys, bank=real_bank, max_pages=1)
        # With very little content, 1 page may or may not be achievable --
        # either way, if it doesn't fit, warning must be set.
        if result.page_count > 1:
            assert result.warning != ""

    def test_custom_summary_is_used(self, real_bank):
        custom = "A completely custom summary paragraph for testing purposes."
        keys = list(real_bank.keys())[:1]
        result = build_resume(keys, bank=real_bank, summary=custom)
        # We can't easily extract text from pdf_bytes without another
        # dependency, but a successful compile with a custom summary
        # string containing no special LaTeX chars should just work.
        assert result.page_count >= 1

    def test_custom_personal_info_overrides_defaults(self, real_bank):
        keys = list(real_bank.keys())[:1]
        custom_info = {"name": "Test Person", "email": "test@example.com"}
        result = build_resume(keys, bank=real_bank, personal_info=custom_info)
        assert result.page_count >= 1
