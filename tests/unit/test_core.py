"""Configuration, logging redaction, the error contract and the document store."""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from resume_tailor.core.config import Settings, get_settings, reset_settings
from resume_tailor.core.errors import AppError, CompilationError, NotFoundError
from resume_tailor.core.logging import redact_pii
from resume_tailor.services.document_store import DocumentStore

pytestmark = pytest.mark.unit


class TestSettings:
    def test_defaults_are_local_safe(self) -> None:
        settings = Settings()
        assert settings.cors_origins == ["http://localhost:8501"]
        assert settings.api_key is None
        assert "*" not in settings.cors_origins

    def test_cors_origins_accepts_a_comma_separated_string(self) -> None:
        """What people actually type into a compose file."""
        settings = Settings(cors_origins="http://a, http://b")  # type: ignore[arg-type]
        assert settings.cors_origins == ["http://a", "http://b"]

    def test_prod_rejects_wildcard_cors(self) -> None:
        with pytest.raises(ValidationError, match="cors_origins"):
            Settings(environment="prod", cors_origins=["*"])

    def test_prod_rejects_debug(self) -> None:
        with pytest.raises(ValidationError, match="debug"):
            Settings(environment="prod", debug=True, cors_origins=["https://x"])

    def test_empty_font_ladder_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            Settings(font_ladder=[])

    def test_font_ladder_must_descend(self) -> None:
        with pytest.raises(ValidationError, match="largest font size first"):
            Settings(font_ladder=[(8.8, 10.6), (9.6, 11.5)])

    def test_line_spacing_below_font_size_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="overlap"):
            Settings(font_ladder=[(10.0, 8.0)])

    def test_negative_font_size_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            Settings(font_ladder=[(-1.0, 11.5)])

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("api_port", 0),
            ("api_port", 70_000),
            ("compile_timeout_s", 0),
            ("max_concurrent_compiles", 0),
            ("max_pages_limit", 11),
            ("max_body_bytes", 10),
        ],
    )
    def test_out_of_range_values_are_rejected(self, field: str, value: object) -> None:
        with pytest.raises(ValidationError):
            Settings(**{field: value})  # type: ignore[arg-type]

    def test_bank_and_profile_paths_derive_from_data_dir(self, tmp_path) -> None:
        settings = Settings(data_dir=tmp_path)
        assert settings.bank_path == tmp_path / "project_bank.json"
        assert settings.profile_path == tmp_path / "profile.yaml"

    def test_settings_are_frozen(self) -> None:
        with pytest.raises(ValidationError):
            Settings().api_port = 9999  # type: ignore[misc]

    def test_get_settings_is_cached_and_resettable(self) -> None:
        first = get_settings()
        assert get_settings() is first
        reset_settings()
        assert get_settings() is not first

    def test_environment_variables_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RT_API_PORT", "9123")
        reset_settings()
        try:
            assert get_settings().api_port == 9123
        finally:
            reset_settings()


class TestLoggingRedaction:
    def test_email_is_redacted_from_free_text(self) -> None:
        result = redact_pii(None, "info", {"event": "contact a.b+c@example.com now"})
        assert "example.com" not in result["event"]

    def test_phone_is_redacted_from_free_text(self) -> None:
        result = redact_pii(None, "info", {"event": "call +91 9400725200 today"})
        assert "9400725200" not in result["event"]

    def test_sensitive_keys_are_blanked_entirely(self) -> None:
        result = redact_pii(None, "info", {"email": "a@b.c", "api_key": "secret"})
        assert result == {"email": "[redacted]", "api_key": "[redacted]"}

    def test_nested_structures_are_redacted(self) -> None:
        result = redact_pii(None, "info", {"payload": {"email": "a@b.c", "note": "ok"}})
        assert result["payload"]["email"] == "[redacted]"
        assert result["payload"]["note"] == "ok"

    def test_lists_are_redacted(self) -> None:
        result = redact_pii(None, "info", {"items": ["a@b.c", "fine"]})
        assert result["items"][0] == "[redacted]"

    def test_non_string_values_survive(self) -> None:
        assert redact_pii(None, "info", {"count": 3})["count"] == 3


class TestErrorContract:
    def test_problem_document_shape(self) -> None:
        problem = NotFoundError("missing thing", key="x").to_problem(instance="/api/v1/x")
        assert problem["status"] == 404
        assert problem["code"] == "not_found"
        assert problem["instance"] == "/api/v1/x"
        assert problem["context"] == {"key": "x"}

    def test_compilation_error_carries_a_log_tail(self) -> None:
        problem = CompilationError("boom", log_tail="! Undefined.").to_problem()
        assert problem["log_tail"] == "! Undefined."

    def test_context_is_omitted_when_empty(self) -> None:
        assert "context" not in AppError("plain").to_problem()

    def test_every_error_type_has_a_distinct_code(self) -> None:
        """The UI branches on `code`, so two errors sharing one would make two
        different failures indistinguishable to the user."""
        subclasses: set[type[AppError]] = set()
        stack = [AppError]
        while stack:
            current = stack.pop()
            subclasses.add(current)
            stack.extend(current.__subclasses__())
        codes = [cls.code for cls in subclasses]
        assert len(codes) == len(set(codes)), "duplicate error codes: " + str(
            sorted({code for code in codes if codes.count(code) > 1})
        )


class TestDocumentStore:
    def test_round_trip(self) -> None:
        store = DocumentStore()
        stored = store.put(b"%PDF-1.4", "resume.pdf", 2)
        assert store.get(stored.document_id).pdf_bytes == b"%PDF-1.4"

    def test_unknown_id_raises_not_found(self) -> None:
        with pytest.raises(NotFoundError, match="expired"):
            DocumentStore().get("nope")

    def test_expired_documents_are_evicted(self) -> None:
        store = DocumentStore(ttl_s=0)
        stored = store.put(b"%PDF", "a.pdf", 1)
        time.sleep(0.01)
        with pytest.raises(NotFoundError):
            store.get(stored.document_id)

    def test_oldest_is_evicted_when_full(self) -> None:
        store = DocumentStore(max_documents=2)
        first = store.put(b"a", "a.pdf", 1)
        store.put(b"b", "b.pdf", 1)
        store.put(b"c", "c.pdf", 1)
        assert len(store) == 2
        with pytest.raises(NotFoundError):
            store.get(first.document_id)

    def test_ids_are_unpredictable(self) -> None:
        store = DocumentStore()
        ids = {store.put(b"x", "x.pdf", 1).document_id for _ in range(20)}
        assert len(ids) == 20
        assert all(len(document_id) >= 16 for document_id in ids)
