# Phases

Each phase leaves the repo green: `ruff check`, `mypy --strict`, and
`pytest -m "not latex"`.

## Phase 1 — Extraction (domain, no I/O)

`domain/extraction.py`: `extract_text(data, filename)` for pdf/docx/txt/md,
`extract_text_from_paste`, `split_sections`. Guards for empty, oversized,
unsupported, legacy `.doc` OLE magic, encrypted PDF, image-only PDF, zip bomb.
Tests: `tests/unit/test_extraction.py`.

## Phase 2 — Knowledge model and store

`domain/knowledge.py`: `KnowledgeEntry`, `KnowledgeSource`, `ExperienceFact`,
`KnowledgeBase` with `merged_with` and `without`, plus
`extract_knowledge(text, source)`.
`data/knowledge_repo.py`: load, save, atomic write, invalidate.
`core/errors.py`: `ExtractionError`, `KnowledgeError`.
Tests: `tests/unit/test_knowledge.py`.

## Phase 3 — ATS scoring (domain)

`domain/ats.py`: vocabularies, `extract_requirements`, `classify`,
`build_report`, weights, bands, methodology note. Reuses `matching.py`.
Tests: `tests/unit/test_ats.py` covering determinism, absence of frequency
inflation, exact/related/missing classification, weight renormalisation.

## Phase 4 — Service and API

`ResumeService`: `knowledge()`, `update_knowledge()`, `remove_knowledge_entry()`,
`candidate_corpus()`, `ats_check()`. `config.py` gains the knowledge filename
and the input limits. `api/schemas.py`, `api/v1/knowledge.py`, `api/v1/ats.py`,
wired into `api/v1/__init__.py` and `main.build_service`.
Tests: `tests/api/test_knowledge_ats.py`.

## Phase 5 — UI

`ui/knowledge_view.py`, `ui/ats_view.py`, the sidebar view switch in
`ui/app.py`, client methods in both modes, and the new state fields.
Tests: extend `tests/ui/test_streamlit_app.py`.

## Phase 6 — Docs

README feature sections, a pointer from `docs/ARCHITECTURE.md`, and the
progress log in `docs/features/Memory.md`.
